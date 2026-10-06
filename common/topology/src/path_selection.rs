// Copyright 2026 - Nym Technologies SA <contact@nymtech.net>
// SPDX-License-Identifier: Apache-2.0

//! Path selection strategies from "When Mixnets Fail: Evaluating, Quantifying, and Mitigating
//! the Impact of Adversarial Nodes in Mix Networks" (Rahimi, NDSS 2026).
//!
//! Each strategy constrains the routes assigned to packets of the same client-destination
//! session, to reduce the chance that any packet traverses a fully compromised route.
//!
//! A session covers every packet of a conversation, not only the forward data packets: the
//! SURB-ACKs and reply SURBs created for messages to a recipient draw their mix path from that
//! recipient's session, and loop cover traffic uses the session of the client's own address.
//! [`AuxiliaryRoutes`] can instead give the acks of data packets, or the loop cover packets and
//! their acks, independent uniform routes that leave the sessions untouched.
//!
//! Routing decisions are logged under the `nym_topology::path_selection` target: new sessions
//! at `info` and every chosen route at `debug`.

use crate::{NodeId, NymRouteProvider, NymTopology, NymTopologyError};
use nym_sphinx_addressing::clients::{Recipient, RecipientBytes};
use nym_sphinx_addressing::nodes::NodeIdentity;
use nym_sphinx_types::Node as SphinxNode;
use rand::seq::IndexedRandom;
use rand::{CryptoRng, Rng, RngExt};
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use tracing::{debug, info, warn};

/// Number of mix layers in a route.
pub const MIX_LAYERS: usize = 3;

/// Attempts at rejection-sampling an unused route for α-SS before enumerating the unused routes.
const MAX_FRESH_ROUTE_ATTEMPTS: usize = 64;

/// Largest route space for which α-SS enumerates the unused routes when rejection sampling fails.
const MAX_ENUMERATED_ROUTES: usize = 1_000_000;

/// Mixnodes of a route, one per layer.
pub type MixPath = [NodeId; MIX_LAYERS];

/// How mix routes are chosen for the packets of a client-destination session.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum PathSelectionStrategy {
    /// Current Nym behaviour: every packet gets an independent, uniformly random route.
    #[default]
    Baseline,

    /// K-Hops Fixed (K-HF): the mixnodes on `fixed_layers` (1-indexed) are sampled once per
    /// session and reused by every packet; the remaining layers are sampled per packet.
    KHopsFixed { fixed_layers: Vec<usize> },

    /// K/W: `k` mixnodes are preselected per layer once per session; every packet then
    /// samples uniformly from that preselected subset in each layer.
    KOverW { k: usize },

    /// α-Sticky Selection (α-SS): with probability `alpha` a packet reuses a route already
    /// assigned in the session, otherwise it gets a fresh uniformly random route.
    AlphaSticky { alpha: f64 },
}

/// How the routes of one kind of auxiliary packet (acks or loop cover traffic) are chosen.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum AuxiliaryRouting {
    /// Follow the [`PathSelectionStrategy`], sharing the session with the other packets.
    #[default]
    FollowStrategy,

    /// Independent, uniformly random route per packet; the session is left untouched.
    Baseline,
}

impl std::str::FromStr for AuxiliaryRouting {
    type Err = String;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "strategy" => Ok(AuxiliaryRouting::FollowStrategy),
            "baseline" => Ok(AuxiliaryRouting::Baseline),
            _ => Err(format!(
                "invalid routing '{value}' (expected 'strategy' or 'baseline')"
            )),
        }
    }
}

/// Routing of the packets other than forward data packets and reply SURBs, which always follow
/// the [`PathSelectionStrategy`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct AuxiliaryRoutes {
    /// SURB-ACKs of data packets.
    pub ack: AuxiliaryRouting,

    /// Loop cover packets and their SURB-ACKs.
    pub cover: AuxiliaryRouting,
}

impl AuxiliaryRoutes {
    /// How routes of `kind` packets are chosen.
    pub fn routing(&self, kind: RouteKind) -> AuxiliaryRouting {
        match kind {
            RouteKind::Forward | RouteKind::ReplySurb => AuxiliaryRouting::FollowStrategy,
            RouteKind::Ack => self.ack,
            RouteKind::Cover | RouteKind::CoverAck => self.cover,
        }
    }
}

/// The kind of packet a route is chosen for. Unless [`AuxiliaryRoutes`] says otherwise, all kinds
/// share the session state and the kind only labels the routing decision in the logs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RouteKind {
    /// Forward data packet to the session's recipient.
    Forward,

    /// SURB-ACK of a data packet sent in the session.
    Ack,

    /// Reply SURB handed to the session's recipient.
    ReplySurb,

    /// Loop cover packet.
    Cover,

    /// SURB-ACK carried by a loop cover packet.
    CoverAck,
}

impl std::fmt::Display for RouteKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let kind = match self {
            RouteKind::Forward => "forward",
            RouteKind::Ack => "ack",
            RouteKind::ReplySurb => "reply-surb",
            RouteKind::Cover => "cover",
            RouteKind::CoverAck => "cover-ack",
        };
        f.write_str(kind)
    }
}

/// Per-session state of a strategy.
#[derive(Debug)]
enum SessionState {
    /// K-HF: the fixed node of each layer, `None` for layers sampled per packet.
    KHopsFixed { fixed: [Option<NodeId>; MIX_LAYERS] },

    /// K/W: the preselected nodes of each layer.
    KOverW { subsets: [Vec<NodeId>; MIX_LAYERS] },

    /// α-SS: the leading set, i.e. all routes assigned so far in the session.
    AlphaSticky {
        leading: Vec<MixPath>,
        leading_lookup: HashSet<MixPath>,
    },
}

#[derive(Debug)]
struct Session {
    state: SessionState,
    packets: u64,
}

/// Chooses mix routes according to a [`PathSelectionStrategy`], keeping per-session state.
#[derive(Debug)]
pub struct PathSelector {
    strategy: PathSelectionStrategy,
    auxiliary_routes: AuxiliaryRoutes,
    sessions: HashMap<RecipientBytes, Session>,
}

/// [`PathSelector`] shared between all senders of a client.
pub type SharedPathSelector = Arc<Mutex<PathSelector>>;

/// Short, log-friendly label for a session.
fn session_label(recipient: &Recipient) -> String {
    let full = recipient.to_string();
    full.chars().take(8).collect()
}

/// Valid mixnodes on the given layer (0-indexed), sorted for reproducibility.
fn layer_nodes(topology: &NymTopology, layer: usize) -> Vec<NodeId> {
    let assigned = match layer {
        0 => &topology.rewarded_set.layer1,
        1 => &topology.rewarded_set.layer2,
        _ => &topology.rewarded_set.layer3,
    };
    let mut nodes: Vec<NodeId> = assigned
        .iter()
        .copied()
        .filter(|id| topology.node_details.contains_key(id))
        .collect();
    nodes.sort_unstable();
    nodes
}

fn all_layer_nodes(topology: &NymTopology) -> Result<[Vec<NodeId>; MIX_LAYERS], NymTopologyError> {
    let layers = [
        layer_nodes(topology, 0),
        layer_nodes(topology, 1),
        layer_nodes(topology, 2),
    ];
    if layers.iter().any(Vec::is_empty) {
        return Err(NymTopologyError::NoMixnodesAvailable);
    }
    Ok(layers)
}

fn random_path<R: Rng + ?Sized>(rng: &mut R, layers: &[Vec<NodeId>; MIX_LAYERS]) -> MixPath {
    // layers are guaranteed to be non-empty by `all_layer_nodes`
    std::array::from_fn(|l| *layers[l].choose(rng).unwrap())
}

/// Samples uniformly from the routes not in `used`, or `None` if there is no such route
/// (or the route space is too large to enumerate after rejection sampling failed).
fn random_unused_path<R: Rng + ?Sized>(
    rng: &mut R,
    layers: &[Vec<NodeId>; MIX_LAYERS],
    used: &HashSet<MixPath>,
) -> Option<MixPath> {
    for _ in 0..MAX_FRESH_ROUTE_ATTEMPTS {
        let path = random_path(rng, layers);
        if !used.contains(&path) {
            return Some(path);
        }
    }

    let total_paths: usize = layers.iter().map(Vec::len).product();
    if total_paths > MAX_ENUMERATED_ROUTES {
        return None;
    }
    let unused: Vec<MixPath> = layers[0]
        .iter()
        .flat_map(|&a| layers[1].iter().map(move |&b| (a, b)))
        .flat_map(|(a, b)| layers[2].iter().map(move |&c| [a, b, c]))
        .filter(|path| !used.contains(path))
        .collect();
    unused.choose(rng).copied()
}

fn path_is_valid(path: &MixPath, layers: &[Vec<NodeId>; MIX_LAYERS]) -> bool {
    path.iter()
        .zip(layers)
        .all(|(node, layer)| layer.binary_search(node).is_ok())
}

impl PathSelector {
    pub fn new(strategy: PathSelectionStrategy) -> Self {
        PathSelector {
            strategy,
            auxiliary_routes: AuxiliaryRoutes::default(),
            sessions: HashMap::new(),
        }
    }

    #[must_use]
    pub fn with_auxiliary_routes(mut self, auxiliary_routes: AuxiliaryRoutes) -> Self {
        self.auxiliary_routes = auxiliary_routes;
        self
    }

    pub fn new_shared(strategy: PathSelectionStrategy) -> SharedPathSelector {
        Arc::new(Mutex::new(Self::new(strategy)))
    }

    /// Shared selector for `strategy`, or `None` for [`PathSelectionStrategy::Baseline`], whose
    /// independent uniform routes need no session state.
    pub fn new_shared_for(
        strategy: PathSelectionStrategy,
        auxiliary_routes: AuxiliaryRoutes,
    ) -> Option<SharedPathSelector> {
        match strategy {
            PathSelectionStrategy::Baseline => None,
            strategy => {
                info!(
                    "[path-selection] forward and reply SURB routes will use {strategy:?}; ack routing: {:?}, cover routing: {:?}",
                    auxiliary_routes.ack, auxiliary_routes.cover
                );
                Some(Arc::new(Mutex::new(
                    Self::new(strategy).with_auxiliary_routes(auxiliary_routes),
                )))
            }
        }
    }

    pub fn strategy(&self) -> &PathSelectionStrategy {
        &self.strategy
    }

    pub fn auxiliary_routes(&self) -> &AuxiliaryRoutes {
        &self.auxiliary_routes
    }

    /// Chooses the mix path for the next `kind` packet of the session with `recipient`.
    pub fn select_path<R>(
        &mut self,
        rng: &mut R,
        topology: &NymTopology,
        recipient: &Recipient,
        kind: RouteKind,
    ) -> Result<MixPath, NymTopologyError>
    where
        R: Rng + CryptoRng + ?Sized,
    {
        let layers = all_layer_nodes(topology)?;
        let label = session_label(recipient);

        if matches!(self.strategy, PathSelectionStrategy::Baseline) {
            let path = random_path(rng, &layers);
            debug!("[path-selection] session {label} ({kind}): baseline route {path:?}");
            return Ok(path);
        }

        if self.auxiliary_routes.routing(kind) == AuxiliaryRouting::Baseline {
            let path = random_path(rng, &layers);
            debug!(
                "[path-selection] session {label} ({kind}): baseline route {path:?} (configured for {kind} packets, session untouched)"
            );
            return Ok(path);
        }

        let strategy = &self.strategy;
        let session = self
            .sessions
            .entry(recipient.to_bytes())
            .or_insert_with(|| Session {
                state: Self::new_session_state(rng, strategy, &layers, &label),
                packets: 0,
            });
        session.packets += 1;
        let packet = session.packets;

        let path = match &mut session.state {
            SessionState::KHopsFixed { fixed } => {
                let path = std::array::from_fn(|l| match fixed[l] {
                    Some(node) if layers[l].binary_search(&node).is_ok() => node,
                    Some(node) => {
                        let replacement = *layers[l].choose(rng).unwrap();
                        warn!(
                            "[path-selection] session {label}: fixed node {node} on layer {} left the topology, replacing it with {replacement}",
                            l + 1
                        );
                        fixed[l] = Some(replacement);
                        replacement
                    }
                    None => *layers[l].choose(rng).unwrap(),
                });
                debug!(
                    "[path-selection] session {label} packet #{packet} ({kind}): K-HF route {path:?} (fixed {fixed:?})"
                );
                path
            }
            SessionState::KOverW { subsets } => {
                for (l, subset) in subsets.iter_mut().enumerate() {
                    subset.retain(|node| layers[l].binary_search(node).is_ok());
                    if subset.is_empty() {
                        warn!(
                            "[path-selection] session {label}: all preselected nodes on layer {} left the topology, picking a new one",
                            l + 1
                        );
                        subset.push(*layers[l].choose(rng).unwrap());
                    }
                }
                let path = std::array::from_fn(|l| *subsets[l].choose(rng).unwrap());
                debug!(
                    "[path-selection] session {label} packet #{packet} ({kind}): K/W route {path:?}"
                );
                path
            }
            SessionState::AlphaSticky {
                leading,
                leading_lookup,
            } => {
                let PathSelectionStrategy::AlphaSticky { alpha } = self.strategy else {
                    unreachable!("session state always matches the strategy")
                };
                leading.retain(|path| path_is_valid(path, &layers));
                leading_lookup.retain(|path| path_is_valid(path, &layers));

                // roll a uniform number in [0, 1): reuse a leading-set route if it is below
                // alpha, otherwise take a route sampled uniformly from those not yet in the
                // leading set. The first packet has nothing to reuse, so it does not roll.
                let roll: Option<f64> = (!leading.is_empty()).then(|| rng.random());
                let dice = match roll {
                    Some(roll) if roll < alpha => format!("roll {roll:.4} < α={alpha} → reuse"),
                    Some(roll) => format!("roll {roll:.4} ≥ α={alpha} → new"),
                    None => "first packet, no roll → new".to_string(),
                };
                let fresh = match roll {
                    Some(roll) if roll < alpha => None,
                    _ => random_unused_path(rng, &layers, leading_lookup),
                };

                match fresh {
                    Some(path) => {
                        leading_lookup.insert(path);
                        leading.push(path);
                        debug!(
                            "[path-selection] session {label} packet #{packet} ({kind}): [{dice}] α-SS new route {path:?} (leading set: {} routes)",
                            leading.len()
                        );
                        path
                    }
                    None => {
                        // leading set is non-empty here: a fresh route always exists for the
                        // first packet since all layers are non-empty
                        let path = *leading.choose(rng).unwrap();
                        let note = if roll.is_some_and(|roll| roll >= alpha) {
                            " (no unused routes left, reusing instead)"
                        } else {
                            ""
                        };
                        debug!(
                            "[path-selection] session {label} packet #{packet} ({kind}): [{dice}] α-SS reused route {path:?}{note} (leading set: {} routes)",
                            leading.len()
                        );
                        path
                    }
                }
            }
        };
        Ok(path)
    }

    fn new_session_state<R>(
        rng: &mut R,
        strategy: &PathSelectionStrategy,
        layers: &[Vec<NodeId>; MIX_LAYERS],
        label: &str,
    ) -> SessionState
    where
        R: Rng + CryptoRng + ?Sized,
    {
        match strategy {
            PathSelectionStrategy::Baseline => {
                unreachable!("baseline does not keep session state")
            }
            PathSelectionStrategy::KHopsFixed { fixed_layers } => {
                let fixed = std::array::from_fn(|l| {
                    fixed_layers
                        .contains(&(l + 1))
                        .then(|| *layers[l].choose(rng).unwrap())
                });
                info!("[path-selection] new session {label}: K-HF fixed nodes per layer {fixed:?}");
                SessionState::KHopsFixed { fixed }
            }
            PathSelectionStrategy::KOverW { k } => {
                let subsets = std::array::from_fn(|l| {
                    if *k > layers[l].len() {
                        warn!(
                            "[path-selection] K={k} exceeds the {} nodes on layer {}, using all of them",
                            layers[l].len(),
                            l + 1
                        );
                    }
                    let mut subset: Vec<NodeId> = layers[l].sample(rng, *k).copied().collect();
                    subset.sort_unstable();
                    subset
                });
                info!(
                    "[path-selection] new session {label}: K/W (K={k}) preselected nodes per layer {subsets:?}"
                );
                SessionState::KOverW { subsets }
            }
            PathSelectionStrategy::AlphaSticky { alpha } => {
                info!("[path-selection] new session {label}: α-SS with α={alpha}");
                SessionState::AlphaSticky {
                    leading: Vec::new(),
                    leading_lookup: HashSet::new(),
                }
            }
        }
    }
}

impl NymRouteProvider {
    /// Builds the sphinx route for the next `kind` packet of the session with `recipient`,
    /// choosing its mix path with the given [`PathSelector`] and ending at `egress`.
    pub fn route_to_egress_with_selector<R>(
        &self,
        rng: &mut R,
        selector: &mut PathSelector,
        recipient: &Recipient,
        kind: RouteKind,
        egress: NodeIdentity,
    ) -> Result<Vec<SphinxNode>, NymTopologyError>
    where
        R: Rng + CryptoRng + ?Sized,
    {
        let path = selector.select_path(rng, &self.topology, recipient, kind)?;
        let mut route: Vec<SphinxNode> = path
            .iter()
            .map(|id| {
                self.topology
                    .node_details
                    .get(id)
                    .map(Into::into)
                    .ok_or(NymTopologyError::NoMixnodesAvailable)
            })
            .collect::<Result<_, _>>()?;
        route.extend(self.empty_route_to_egress(egress)?);
        Ok(route)
    }

    /// Builds the sphinx route for the next `kind` packet of the session with `recipient`:
    /// chosen by `selector` if there is one, otherwise uniformly at random.
    pub fn route_to_egress_for_session<R>(
        &self,
        rng: &mut R,
        selector: Option<&SharedPathSelector>,
        recipient: &Recipient,
        kind: RouteKind,
        egress: NodeIdentity,
    ) -> Result<Vec<SphinxNode>, NymTopologyError>
    where
        R: Rng + CryptoRng + ?Sized,
    {
        match selector {
            Some(selector) => {
                let mut selector = selector
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                self.route_to_egress_with_selector(rng, &mut selector, recipient, kind, egress)
            }
            None => self.random_route_to_egress(rng, egress),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::node::{RoutingNode, SupportedRoles};
    use nym_crypto::asymmetric::{ed25519, x25519};
    use rand_chacha::ChaCha8Rng;
    use rand_chacha::rand_core::SeedableRng;

    const W: usize = 5;
    const PACKETS: usize = 1000;

    fn rng() -> ChaCha8Rng {
        ChaCha8Rng::seed_from_u64(42)
    }

    /// Topology with `w` mixnodes per layer; layer `l` (0-indexed) holds ids `l*w+1 ..= (l+1)*w`.
    fn test_topology(rng: &mut ChaCha8Rng, w: usize) -> NymTopology {
        let mut topology = NymTopology::default();
        for l in 0..MIX_LAYERS {
            for i in 1..=w {
                let node_id = (l * w + i) as NodeId;
                topology.node_details.insert(
                    node_id,
                    RoutingNode {
                        node_id,
                        mix_host: format!("127.0.0.1:{}", 10000 + node_id).parse().unwrap(),
                        entry: None,
                        identity_key: *ed25519::KeyPair::new(rng).public_key(),
                        sphinx_key: *x25519::KeyPair::new(rng).public_key(),
                        supported_roles: SupportedRoles {
                            mixnode: true,
                            mixnet_entry: false,
                            mixnet_exit: false,
                        },
                    },
                );
                match l {
                    0 => topology.rewarded_set.layer1.insert(node_id),
                    1 => topology.rewarded_set.layer2.insert(node_id),
                    _ => topology.rewarded_set.layer3.insert(node_id),
                };
            }
        }
        topology
    }

    fn test_recipient(rng: &mut ChaCha8Rng) -> Recipient {
        Recipient::new(
            *ed25519::KeyPair::new(rng).public_key(),
            *x25519::KeyPair::new(rng).public_key(),
            *ed25519::KeyPair::new(rng).public_key(),
        )
    }

    fn run(strategy: PathSelectionStrategy, packets: usize) -> (PathSelector, Vec<MixPath>) {
        run_with_width(strategy, packets, W)
    }

    fn run_with_width(
        strategy: PathSelectionStrategy,
        packets: usize,
        w: usize,
    ) -> (PathSelector, Vec<MixPath>) {
        let mut rng = rng();
        let topology = test_topology(&mut rng, w);
        let recipient = test_recipient(&mut rng);
        let mut selector = PathSelector::new(strategy);
        let paths = (0..packets)
            .map(|_| {
                selector
                    .select_path(&mut rng, &topology, &recipient, RouteKind::Forward)
                    .unwrap()
            })
            .collect();
        (selector, paths)
    }

    fn distinct_on_layer(paths: &[MixPath], layer: usize) -> usize {
        paths.iter().map(|p| p[layer]).collect::<HashSet<_>>().len()
    }

    fn distinct_paths(paths: &[MixPath]) -> usize {
        paths.iter().collect::<HashSet<_>>().len()
    }

    #[test]
    fn every_path_uses_one_node_from_each_layer() {
        let strategies = [
            PathSelectionStrategy::Baseline,
            PathSelectionStrategy::KHopsFixed {
                fixed_layers: vec![2],
            },
            PathSelectionStrategy::KOverW { k: 2 },
            PathSelectionStrategy::AlphaSticky { alpha: 0.5 },
        ];
        for strategy in strategies {
            let (_, paths) = run(strategy, PACKETS);
            for path in paths {
                for (l, node) in path.iter().enumerate() {
                    let layer_ids = (l * W + 1) as NodeId..=((l + 1) * W) as NodeId;
                    assert!(
                        layer_ids.contains(node),
                        "node {node} is not on layer {}",
                        l + 1
                    );
                }
            }
        }
    }

    #[test]
    fn baseline_keeps_no_session_state_and_spreads_routes() {
        let (selector, paths) = run(PathSelectionStrategy::Baseline, PACKETS);
        assert!(selector.sessions.is_empty());
        assert_eq!(distinct_paths(&paths), W.pow(MIX_LAYERS as u32));
    }

    #[test]
    fn khf_fixes_only_the_selected_layers() {
        let (_, paths) = run(
            PathSelectionStrategy::KHopsFixed {
                fixed_layers: vec![1, 2],
            },
            PACKETS,
        );
        assert_eq!(distinct_on_layer(&paths, 0), 1);
        assert_eq!(distinct_on_layer(&paths, 1), 1);
        assert_eq!(distinct_on_layer(&paths, 2), W);
    }

    #[test]
    fn khf_with_all_layers_fixed_uses_a_single_route() {
        let (_, paths) = run(
            PathSelectionStrategy::KHopsFixed {
                fixed_layers: vec![1, 2, 3],
            },
            PACKETS,
        );
        assert_eq!(distinct_paths(&paths), 1);
    }

    #[test]
    fn kw_uses_exactly_k_nodes_per_layer() {
        let (_, paths) = run(PathSelectionStrategy::KOverW { k: 2 }, PACKETS);
        for l in 0..MIX_LAYERS {
            assert_eq!(distinct_on_layer(&paths, l), 2);
        }
    }

    #[test]
    fn kw_clamps_k_to_the_layer_size() {
        let (_, paths) = run(PathSelectionStrategy::KOverW { k: 10 }, PACKETS);
        for l in 0..MIX_LAYERS {
            assert_eq!(distinct_on_layer(&paths, l), W);
        }
    }

    #[test]
    fn alpha_one_reuses_the_first_route() {
        let (_, paths) = run(PathSelectionStrategy::AlphaSticky { alpha: 1.0 }, PACKETS);
        assert_eq!(distinct_paths(&paths), 1);
    }

    #[test]
    fn alpha_zero_never_reuses_until_all_routes_are_used() {
        let total = W.pow(MIX_LAYERS as u32);
        let (_, paths) = run(PathSelectionStrategy::AlphaSticky { alpha: 0.0 }, total);
        assert_eq!(distinct_paths(&paths), total);
    }

    #[test]
    fn alpha_falls_back_to_reuse_once_all_routes_are_used() {
        let total = W.pow(MIX_LAYERS as u32);
        let (selector, paths) = run(
            PathSelectionStrategy::AlphaSticky { alpha: 0.0 },
            total + 50,
        );
        assert_eq!(distinct_paths(&paths), total);
        assert_eq!(
            selector.sessions.values().next().unwrap().packets,
            (total + 50) as u64
        );
    }

    #[test]
    fn alpha_reuse_rate_matches_alpha() {
        // expected leading set size: 1 + (1 - alpha) * (PACKETS - 1) ≈ 200, out of 1000 routes
        let (selector, _) = run_with_width(
            PathSelectionStrategy::AlphaSticky { alpha: 0.8 },
            PACKETS,
            10,
        );
        let session = selector.sessions.values().next().unwrap();
        let SessionState::AlphaSticky { leading, .. } = &session.state else {
            panic!("unexpected session state")
        };
        assert!(
            (160..=240).contains(&leading.len()),
            "leading set has {} routes",
            leading.len()
        );
        assert_eq!(session.packets, PACKETS as u64);
    }

    #[test]
    fn all_route_kinds_share_the_session() {
        let mut rng = rng();
        let topology = test_topology(&mut rng, W);
        let recipient = test_recipient(&mut rng);
        let mut selector = PathSelector::new(PathSelectionStrategy::KHopsFixed {
            fixed_layers: vec![1, 2, 3],
        });
        let kinds = [
            RouteKind::Forward,
            RouteKind::Ack,
            RouteKind::ReplySurb,
            RouteKind::Cover,
            RouteKind::CoverAck,
        ];
        let paths: Vec<MixPath> = kinds
            .iter()
            .map(|&kind| {
                selector
                    .select_path(&mut rng, &topology, &recipient, kind)
                    .unwrap()
            })
            .collect();
        assert_eq!(distinct_paths(&paths), 1);
        assert_eq!(selector.sessions.len(), 1);
        assert_eq!(
            selector.sessions[&recipient.to_bytes()].packets,
            kinds.len() as u64
        );
    }

    fn all_layers_fixed() -> PathSelectionStrategy {
        PathSelectionStrategy::KHopsFixed {
            fixed_layers: vec![1, 2, 3],
        }
    }

    #[test]
    fn baseline_ack_routing_bypasses_the_session() {
        let mut rng = rng();
        let topology = test_topology(&mut rng, W);
        let recipient = test_recipient(&mut rng);
        let mut selector =
            PathSelector::new(all_layers_fixed()).with_auxiliary_routes(AuxiliaryRoutes {
                ack: AuxiliaryRouting::Baseline,
                cover: AuxiliaryRouting::FollowStrategy,
            });
        let fixed = selector
            .select_path(&mut rng, &topology, &recipient, RouteKind::Forward)
            .unwrap();

        let acks: Vec<MixPath> = (0..PACKETS)
            .map(|_| {
                selector
                    .select_path(&mut rng, &topology, &recipient, RouteKind::Ack)
                    .unwrap()
            })
            .collect();
        // with every layer fixed the strategy would give a single route
        assert!(distinct_paths(&acks) > W);
        assert_eq!(selector.sessions[&recipient.to_bytes()].packets, 1);

        // the cover ack follows the cover routing, i.e. the strategy here
        let cover_ack = selector
            .select_path(&mut rng, &topology, &recipient, RouteKind::CoverAck)
            .unwrap();
        assert_eq!(cover_ack, fixed);
        assert_eq!(selector.sessions[&recipient.to_bytes()].packets, 2);
    }

    #[test]
    fn baseline_cover_routing_bypasses_the_session() {
        let mut rng = rng();
        let topology = test_topology(&mut rng, W);
        let us = test_recipient(&mut rng);
        let mut selector =
            PathSelector::new(all_layers_fixed()).with_auxiliary_routes(AuxiliaryRoutes {
                ack: AuxiliaryRouting::FollowStrategy,
                cover: AuxiliaryRouting::Baseline,
            });

        let cover: Vec<MixPath> = (0..PACKETS)
            .flat_map(|_| [RouteKind::Cover, RouteKind::CoverAck])
            .map(|kind| {
                selector
                    .select_path(&mut rng, &topology, &us, kind)
                    .unwrap()
            })
            .collect();
        // with every layer fixed the strategy would give a single route
        assert!(distinct_paths(&cover) > W);
        assert!(selector.sessions.is_empty());

        // acks of data packets still follow the strategy
        let ack = selector
            .select_path(&mut rng, &topology, &us, RouteKind::Ack)
            .unwrap();
        let forward = selector
            .select_path(&mut rng, &topology, &us, RouteKind::Forward)
            .unwrap();
        assert_eq!(ack, forward);
        assert_eq!(selector.sessions[&us.to_bytes()].packets, 2);
    }

    #[test]
    fn forward_and_reply_surbs_always_follow_the_strategy() {
        let routes = AuxiliaryRoutes {
            ack: AuxiliaryRouting::Baseline,
            cover: AuxiliaryRouting::Baseline,
        };
        assert_eq!(
            routes.routing(RouteKind::Forward),
            AuxiliaryRouting::FollowStrategy
        );
        assert_eq!(
            routes.routing(RouteKind::ReplySurb),
            AuxiliaryRouting::FollowStrategy
        );
        assert_eq!(routes.routing(RouteKind::Ack), AuxiliaryRouting::Baseline);
        assert_eq!(routes.routing(RouteKind::Cover), AuxiliaryRouting::Baseline);
        assert_eq!(
            routes.routing(RouteKind::CoverAck),
            AuxiliaryRouting::Baseline
        );
    }

    #[test]
    fn auxiliary_routing_parses_from_str() {
        assert_eq!("strategy".parse(), Ok(AuxiliaryRouting::FollowStrategy));
        assert_eq!("baseline".parse(), Ok(AuxiliaryRouting::Baseline));
        assert!("random".parse::<AuxiliaryRouting>().is_err());
    }

    #[test]
    fn sessions_are_kept_per_recipient() {
        let mut rng = rng();
        let topology = test_topology(&mut rng, W);
        let alice = test_recipient(&mut rng);
        let bob = test_recipient(&mut rng);
        let mut selector = PathSelector::new(PathSelectionStrategy::KHopsFixed {
            fixed_layers: vec![1],
        });

        for _ in 0..10 {
            selector
                .select_path(&mut rng, &topology, &alice, RouteKind::Forward)
                .unwrap();
            selector
                .select_path(&mut rng, &topology, &bob, RouteKind::Forward)
                .unwrap();
        }
        assert_eq!(selector.sessions.len(), 2);
        assert!(selector.sessions.values().all(|s| s.packets == 10));
    }

    #[test]
    fn khf_replaces_a_fixed_node_that_left_the_topology() {
        let mut rng = rng();
        let mut topology = test_topology(&mut rng, W);
        let recipient = test_recipient(&mut rng);
        let mut selector = PathSelector::new(PathSelectionStrategy::KHopsFixed {
            fixed_layers: vec![1],
        });

        let fixed = selector
            .select_path(&mut rng, &topology, &recipient, RouteKind::Forward)
            .unwrap()[0];
        topology.node_details.remove(&fixed);

        let replacement = selector
            .select_path(&mut rng, &topology, &recipient, RouteKind::Forward)
            .unwrap()[0];
        assert_ne!(replacement, fixed);
        let later = selector
            .select_path(&mut rng, &topology, &recipient, RouteKind::Forward)
            .unwrap()[0];
        assert_eq!(later, replacement);
    }
}
