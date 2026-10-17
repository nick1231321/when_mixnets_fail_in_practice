//! Load generator for the localnet: runs many clients in one process, grouped by path selection
//! strategy and routing, that send messages to each other, for flow-matching experiments (e.g.
//! MixMatch). All clients share one clock (the [`instrumentation`] time base), so its own
//! message timings are on one timeline.
//!
//! Output files (in `output_dir`):
//!   run.json       configuration, timeline and topology (node addresses and roles)
//!   clients.csv    one row per client: group, strategy, routing, gateway, tag, wire tag, partner
//!   messages.csv   one row per message: sender, receiver, submit and receive times
//!   lag.csv        scheduler lag probe: how late a 10 ms timer fired
//!
//! The wire-level packet log used for flow matching is produced by the GATEWAYS (set
//! MIXCORR_WIRE_DIR on the gateway nodes); this generator only drives the traffic and records
//! the client->pair mapping in clients.csv (its `wire_tag` column matches the gateway's tag).
//!
//! Logging uses LOAD_TEST_LOG (default `warn`); RUST_LOG is ignored so that per-packet debug
//! logging is never enabled by accident during a measurement.

use crate::{parse_strategy, FileTopologyProvider};
use nym_sdk::mixnet::{
    self, MixnetClient, MixnetMessageSender, PathSelectionStrategy, Recipient, Routing,
    RoutingConfig,
};
use nym_sdk::DebugConfig;
use nym_sphinx_chunking::instrumentation;
use nym_task::ShutdownManager;
use nym_topology::NymTopology;
use serde::Deserialize;
use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::{watch, Semaphore};
use tokio::task::JoinSet;

/// First bytes of every load-test payload.
const PAYLOAD_MAGIC: &[u8; 8] = b"NYMLOAD1";
/// magic (8) | message id (8) | sender index (4) | padding (4) | submit time ns (8)
const PAYLOAD_HEADER_LEN: usize = 32;
const LAG_PROBE_INTERVAL: Duration = Duration::from_millis(10);
const MAX_CONCURRENT_CONNECTS: usize = 8;
/// Attempts to connect each client; under load a gateway handshake can time out.
const CONNECT_ATTEMPTS: u32 = 3;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(60);
/// On the live network a client fetches the topology from the nym-api and handshakes with a
/// public gateway: slower and flakier than the localnet, so allow more time, more attempts and
/// fewer simultaneous connections.
const MAINNET_MAX_CONCURRENT_CONNECTS: usize = 2;
const MAINNET_CONNECT_ATTEMPTS: u32 = 5;
const MAINNET_CONNECT_TIMEOUT: Duration = Duration::from_secs(180);
/// Time allowed for all clients to disconnect at the end of the run.
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    #[serde(default = "default_topology")]
    topology: PathBuf,
    /// "localnet" (default): topology and gateways from the `topology` file. "mainnet": the live
    /// Nym network; the SDK fetches the topology from the nym-api, and the entry gateways are
    /// taken from `gateways` (identity keys), which must then be set.
    #[serde(default)]
    network: Option<String>,
    /// Entry gateway identity keys to spread the clients over (mainnet mode).
    #[serde(default)]
    gateways: Vec<String>,
    #[serde(default = "default_output_dir")]
    output_dir: PathBuf,
    duration_s: u64,
    #[serde(default)]
    warmup_s: u64,
    #[serde(default = "default_drain_s")]
    drain_s: u64,
    message_size: usize,
    messages_per_client_per_min: f64,
    #[serde(default = "default_pairing")]
    pairing: String,
    #[serde(default = "default_seed")]
    seed: u64,
    #[serde(default)]
    client: ClientSettings,
    #[serde(rename = "group")]
    groups: Vec<GroupConfig>,
}

/// Client settings shared by all groups. `None` keeps the client default.
#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct ClientSettings {
    /// Loop cover traffic stream (default: on).
    loop_cover_traffic: Option<bool>,
    /// Poisson-paced sending with cover packets in empty slots (default: on). When off, real
    /// packets are sent as soon as they are ready and no cover packets fill the gaps.
    poisson_sending: Option<bool>,
    /// Average delay per mix hop.
    average_packet_delay_ms: Option<u64>,
    /// Average gap between packets of the Poisson sending stream.
    message_sending_average_delay_ms: Option<u64>,
    /// Average gap between loop cover packets (the cover stream's Poisson rate).
    loop_cover_traffic_average_delay_ms: Option<u64>,
    /// Extra time a client waits for a SURB-ACK before retransmitting, on top of 1.5x the
    /// expected mixing delay (client default 1.5 s). Raise it when real network RTTs are large
    /// next to the mixing delay (e.g. the netem localnet with intercontinental routes):
    /// otherwise the acks of long routes arrive after the timeout and the spurious
    /// retransmissions flood the network.
    ack_wait_addition_s: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct GroupConfig {
    name: Option<String>,
    count: usize,
    #[serde(default = "default_strategy")]
    strategy: String,
    real_routing: Option<String>,
    real_ack_routing: Option<String>,
    cover_routing: Option<String>,
    cover_ack_routing: Option<String>,
    // per-group traffic overrides; default to the top-level values. Set different values on
    // different groups to run one experiment with heterogeneous traffic, to test flow matching
    // under a realistic traffic mix. The three *stream* parameters below are what a gateway
    // observes on the wire (they set the client's Poisson emission), unlike the application-level
    // message_size / messages_per_client_per_min which the cover+Poisson stream largely masks.
    message_size: Option<usize>,
    messages_per_client_per_min: Option<f64>,
    /// Per-hop mixing delay for this group's clients (the delay the matcher models).
    average_packet_delay_ms: Option<u64>,
    /// Real-traffic Poisson stream rate (average gap between real-stream packets).
    message_sending_average_delay_ms: Option<u64>,
    /// Loop cover stream rate (average gap between cover packets).
    loop_cover_traffic_average_delay_ms: Option<u64>,
    /// Loop cover stream on/off for this group (NymVPN's "background cover traffic" toggle).
    loop_cover_traffic: Option<bool>,
    /// Poisson sending with cover in empty slots on/off (NymVPN's "send traffic continuously").
    poisson_sending: Option<bool>,
}

fn default_topology() -> PathBuf {
    "data/network.json".into()
}
fn default_output_dir() -> PathBuf {
    "load-test-results".into()
}
fn default_drain_s() -> u64 {
    30
}
fn default_pairing() -> String {
    "random-pairs".into()
}
fn default_seed() -> u64 {
    1
}
fn default_strategy() -> String {
    "baseline".into()
}

/// A group after parsing its strategy and routing.
struct Group {
    name: String,
    strategy_arg: String,
    strategy: PathSelectionStrategy,
    routing: RoutingConfig,
    message_size: usize,
    rate_per_min: f64,
}

impl Group {
    fn parse(
        config: &GroupConfig,
        index: usize,
        default_size: usize,
        default_rate: f64,
    ) -> Result<Self, String> {
        let routing = |value: &Option<String>| -> Result<Routing, String> {
            value.as_deref().map_or(Ok(Routing::Strategy), str::parse)
        };
        let message_size = config.message_size.unwrap_or(default_size);
        let rate_per_min = config.messages_per_client_per_min.unwrap_or(default_rate);
        if message_size == 0 || rate_per_min <= 0.0 {
            return Err(format!(
                "group {index}: message_size and messages_per_client_per_min must be positive"
            ));
        }
        Ok(Group {
            name: config
                .name
                .clone()
                .unwrap_or_else(|| format!("g{index}-{}", config.strategy)),
            strategy_arg: config.strategy.clone(),
            strategy: parse_strategy(&config.strategy)?,
            routing: RoutingConfig {
                real: routing(&config.real_routing)?,
                real_ack: routing(&config.real_ack_routing)?,
                cover: routing(&config.cover_routing)?,
                cover_ack: routing(&config.cover_ack_routing)?,
            },
            message_size,
            rate_per_min,
        })
    }
}

/// Small deterministic PRNG (SplitMix64), so runs are reproducible from the seed.
struct Prng(u64);

impl Prng {
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        z ^ (z >> 31)
    }

    /// Uniform in (0, 1].
    fn unit(&mut self) -> f64 {
        ((self.next_u64() >> 11) as f64 + 1.0) / (1u64 << 53) as f64
    }

    /// Exponentially distributed with the given mean.
    fn exponential(&mut self, mean: f64) -> f64 {
        -mean * self.unit().ln()
    }

    fn shuffle<T>(&mut self, items: &mut [T]) {
        for i in (1..items.len()).rev() {
            let j = (self.next_u64() % (i as u64 + 1)) as usize;
            items.swap(i, j);
        }
    }
}

/// Per client: index of the client it sends to (`None`: it only receives), and its gateway.
struct Plan {
    partner: Vec<Option<usize>>,
    gateway: Vec<usize>,
}

fn plan(pairing: &str, clients: usize, gateways: usize, rng: &mut Prng) -> Result<Plan, String> {
    let mut partner = vec![None; clients];
    let mut gateway = vec![0; clients];
    let other = |g: usize| (g + 1) % gateways.max(1);
    match pairing {
        // shuffled pairs that send to each other, on different gateways
        "random-pairs" => {
            let mut order: Vec<usize> = (0..clients).collect();
            rng.shuffle(&mut order);
            for pair in order.chunks(2) {
                match *pair {
                    [a, b] => {
                        partner[a] = Some(b);
                        partner[b] = Some(a);
                        gateway[a] = 0;
                        gateway[b] = other(0);
                    }
                    // odd count: the last client sends to the first one
                    [a] => partner[a] = Some(order[0]),
                    _ => unreachable!(),
                }
            }
        }
        // pair the first half of clients with the second half positionally (client i <-> i+n/2),
        // on different gateways. With two equal groups this makes every pair span the two groups,
        // so a run can isolate one cross-group combination (e.g. group-A initiator, group-C responder).
        "split-pairs" => {
            if clients % 2 != 0 {
                return Err("split-pairs needs an even number of clients".into());
            }
            let half = clients / 2;
            for i in 0..half {
                partner[i] = Some(half + i);
                partner[half + i] = Some(i);
                gateway[i] = 0;
                gateway[half + i] = other(0);
            }
        }
        "ring" => {
            for i in 0..clients {
                partner[i] = Some((i + 1) % clients);
                gateway[i] = i % gateways.max(1);
            }
        }
        // client 0 only receives, everyone else sends to it
        "many-to-one" => {
            for i in 1..clients {
                partner[i] = Some(0);
                gateway[i] = other(i % gateways.max(1));
            }
        }
        "self" => {
            for i in 0..clients {
                partner[i] = Some(i);
                gateway[i] = i % gateways.max(1);
            }
        }
        other => {
            return Err(format!(
                "invalid pairing '{other}' (expected random-pairs, split-pairs, ring, many-to-one or self)"
            ))
        }
    }
    Ok(Plan { partner, gateway })
}

struct SentMessage {
    id: u64,
    sender: usize,
    receiver: usize,
    t_submit_ns: u64,
    size: usize,
}

struct ReceivedMessage {
    id: u64,
    receiver: usize,
    t_received_ns: u64,
}

fn payload(id: u64, sender: usize, size: usize, t_submit_ns: u64) -> Vec<u8> {
    let mut payload = Vec::with_capacity(size.max(PAYLOAD_HEADER_LEN));
    payload.extend_from_slice(PAYLOAD_MAGIC);
    payload.extend_from_slice(&id.to_le_bytes());
    payload.extend_from_slice(&(sender as u32).to_le_bytes());
    payload.extend_from_slice(&0u32.to_le_bytes());
    payload.extend_from_slice(&t_submit_ns.to_le_bytes());
    payload.resize(size.max(PAYLOAD_HEADER_LEN), 0x5a);
    payload
}

fn parse_payload_id(payload: &[u8]) -> Option<u64> {
    if payload.len() < PAYLOAD_HEADER_LEN || &payload[..8] != PAYLOAD_MAGIC {
        return None;
    }
    Some(u64::from_le_bytes(payload[8..16].try_into().ok()?))
}

fn debug_config(settings: &ClientSettings) -> DebugConfig {
    let mut debug = DebugConfig::default();
    if let Some(on) = settings.loop_cover_traffic {
        debug.cover_traffic.disable_loop_cover_traffic_stream = !on;
    }
    if let Some(on) = settings.poisson_sending {
        debug.traffic.disable_main_poisson_packet_distribution = !on;
    }
    if let Some(ms) = settings.average_packet_delay_ms {
        debug.traffic.average_packet_delay = Duration::from_millis(ms);
    }
    if let Some(ms) = settings.message_sending_average_delay_ms {
        debug.traffic.message_sending_average_delay = Duration::from_millis(ms);
    }
    if let Some(ms) = settings.loop_cover_traffic_average_delay_ms {
        debug.cover_traffic.loop_cover_traffic_average_delay = Duration::from_millis(ms);
    }
    if let Some(s) = settings.ack_wait_addition_s {
        debug.acknowledgements.ack_wait_addition = Duration::from_secs(s);
    }
    debug
}

/// One connected client and the shutdown manager that owns its tasks.
struct Connected {
    client: MixnetClient,
    // keeps the client's own shutdown manager alive (one per client: a panic in one client
    // must not cancel the others, which the shared SDK default would do)
    _shutdown: ShutdownManager,
}

async fn connect(
    topology_path: Option<&Path>,
    group: &Group,
    gateway: String,
    debug: DebugConfig,
) -> Result<Connected, String> {
    let shutdown = ShutdownManager::new_without_signals();
    let mut builder = mixnet::MixnetClientBuilder::new_ephemeral();
    // localnet: the topology file; mainnet (None): the SDK's default, the live nym-api
    if let Some(path) = topology_path {
        let provider = FileTopologyProvider::new(path)
            .map_err(|err| format!("failed to load topology: {err}"))?;
        builder = builder.custom_topology_provider(Box::new(provider));
    }
    let client = builder
        .custom_shutdown(shutdown.shutdown_tracker_owned())
        .request_gateway(gateway)
        .debug_config(debug)
        .path_selection_strategy(group.strategy.clone())
        .real_routing(group.routing.real)
        .real_ack_routing(group.routing.real_ack)
        .cover_routing(group.routing.cover)
        .cover_ack_routing(group.routing.cover_ack)
        .build()
        .map_err(|err| format!("failed to build client: {err}"))?
        .connect_to_mixnet()
        .await
        .map_err(|err| format!("failed to connect: {err}"))?;
    Ok(Connected {
        client,
        _shutdown: shutdown,
    })
}

/// Entry point of the `nym-load-test` binary: `nym-load-test CONFIG.toml [OUTPUT_DIR]`.
pub fn run() -> ExitCode {
    // per-packet logging (e.g. path selection at debug level) would distort the measurements,
    // so an inherited RUST_LOG is ignored; LOAD_TEST_LOG sets the filter explicitly
    let filter = std::env::var("LOAD_TEST_LOG").unwrap_or_else(|_| "warn".to_string());
    std::env::set_var("RUST_LOG", filter);
    nym_bin_common::logging::setup_tracing_logger();

    let mut args = std::env::args().skip(1);
    let Some(config_path) = args.next() else {
        eprintln!("usage: nym-load-test CONFIG.toml [OUTPUT_DIR]");
        return ExitCode::FAILURE;
    };
    let mut config: Config = match std::fs::read_to_string(&config_path)
        .map_err(|err| err.to_string())
        .and_then(|text| toml::from_str(&text).map_err(|err| err.to_string()))
    {
        Ok(config) => config,
        Err(err) => {
            eprintln!("invalid config '{config_path}': {err}");
            return ExitCode::FAILURE;
        }
    };
    if let Some(output_dir) = args.next() {
        config.output_dir = output_dir.into();
    }

    tokio::runtime::Runtime::new()
        .expect("failed to start the tokio runtime")
        .block_on(async {
            match load_test(config).await {
                Ok(()) => ExitCode::SUCCESS,
                Err(err) => {
                    eprintln!("load test failed: {err}");
                    ExitCode::FAILURE
                }
            }
        })
}

async fn load_test(config: Config) -> Result<(), String> {
    let groups = config
        .groups
        .iter()
        .enumerate()
        .map(|(i, group)| {
            Group::parse(
                group,
                i,
                config.message_size,
                config.messages_per_client_per_min,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    let client_group: Vec<usize> = config
        .groups
        .iter()
        .enumerate()
        .flat_map(|(i, group)| std::iter::repeat_n(i, group.count))
        .collect();
    let clients = client_group.len();
    if clients == 0 {
        return Err("no clients configured".into());
    }
    if config.message_size == 0 || config.messages_per_client_per_min <= 0.0 {
        return Err("message_size and messages_per_client_per_min must be positive".into());
    }

    let mainnet = config.network.as_deref() == Some("mainnet");
    let (topology, gateways): (NymTopology, Vec<String>) = if mainnet {
        // the live network: no local topology; the gateways come from the config
        (NymTopology::default(), config.gateways.clone())
    } else {
        let topology = NymTopology::new_from_file(&config.topology)
            .map_err(|err| format!("failed to load topology: {err}"))?;
        let gateways = topology
            .entry_gateways()
            .map(|node| node.identity_key.to_base58_string())
            .collect();
        (topology, gateways)
    };
    if gateways.is_empty() {
        return Err(if mainnet {
            "mainnet mode needs `gateways` (entry gateway identity keys) in the config".into()
        } else {
            "the topology has no entry gateways".into()
        });
    }

    let mut rng = Prng(config.seed);
    let plan = plan(&config.pairing, clients, gateways.len(), &mut rng)?;
    std::fs::create_dir_all(&config.output_dir)
        .map_err(|err| format!("failed to create {}: {err}", config.output_dir.display()))?;

    println!(
        "connecting {clients} clients ({} groups, pairing {}, {} gateways)...",
        groups.len(),
        config.pairing,
        gateways.len()
    );

    // base client settings, then per-group stream overrides (mixing, real-stream and cover-stream
    // rates). The gateway observes the client's Poisson emission on the wire, so this is where
    // heterogeneous traffic actually lives (see GroupConfig).
    let base_debug = debug_config(&config.client);
    let group_debug: Vec<DebugConfig> = config
        .groups
        .iter()
        .map(|gc| {
            let mut d = base_debug.clone();
            if let Some(ms) = gc.average_packet_delay_ms {
                d.traffic.average_packet_delay = Duration::from_millis(ms);
            }
            if let Some(ms) = gc.message_sending_average_delay_ms {
                d.traffic.message_sending_average_delay = Duration::from_millis(ms);
            }
            if let Some(ms) = gc.loop_cover_traffic_average_delay_ms {
                d.cover_traffic.loop_cover_traffic_average_delay = Duration::from_millis(ms);
            }
            if let Some(on) = gc.loop_cover_traffic {
                d.cover_traffic.disable_loop_cover_traffic_stream = !on;
            }
            if let Some(on) = gc.poisson_sending {
                d.traffic.disable_main_poisson_packet_distribution = !on;
            }
            d
        })
        .collect();

    // connect all clients, a few at a time
    let groups = Arc::new(groups);
    let group_debug = Arc::new(group_debug);
    let (max_concurrent, connect_attempts, connect_timeout) = if mainnet {
        (MAINNET_MAX_CONCURRENT_CONNECTS, MAINNET_CONNECT_ATTEMPTS, MAINNET_CONNECT_TIMEOUT)
    } else {
        (MAX_CONCURRENT_CONNECTS, CONNECT_ATTEMPTS, CONNECT_TIMEOUT)
    };
    let semaphore = Arc::new(Semaphore::new(max_concurrent));
    let mut connecting = JoinSet::new();
    for index in 0..clients {
        let (groups, semaphore) = (groups.clone(), semaphore.clone());
        let group = client_group[index];
        let debug = group_debug[group].clone();
        let topology_path = (!mainnet).then(|| config.topology.clone());
        let gateway = gateways[plan.gateway[index]].clone();
        connecting.spawn(async move {
            let _permit = semaphore.acquire_owned().await;
            let mut result = Err(String::new());
            for attempt in 1..=connect_attempts {
                result = tokio::time::timeout(
                    connect_timeout,
                    connect(
                        topology_path.as_deref(),
                        &groups[group],
                        gateway.clone(),
                        debug.clone(),
                    ),
                )
                .await
                .unwrap_or_else(|_| Err("connection timed out".to_string()));
                match &result {
                    Ok(_) => break,
                    Err(err) if attempt < connect_attempts => {
                        eprintln!("client {index}: attempt {attempt} failed ({err}), retrying");
                        tokio::time::sleep(Duration::from_secs(5 * u64::from(attempt))).await;
                    }
                    Err(_) => {}
                }
            }
            (index, result)
        });
    }
    let mut connected: Vec<Option<Connected>> = (0..clients).map(|_| None).collect();
    while let Some(joined) = connecting.join_next().await {
        let (index, result) = joined.map_err(|err| format!("connect task failed: {err}"))?;
        connected[index] = Some(result.map_err(|err| format!("client {index}: {err}"))?);
    }
    let connected: Vec<Connected> = connected.into_iter().map(Option::unwrap).collect();
    let addresses: Vec<Recipient> = connected.iter().map(|c| *c.client.nym_address()).collect();
    write_clients(
        &config,
        &groups,
        &client_group,
        &plan,
        &gateways,
        &addresses,
    )?;
    println!("all clients connected");

    // timeline
    let t_start = instrumentation::now_ns();
    let send_until = t_start + config.duration_s * 1_000_000_000;
    let (stop_tx, stop_rx) = watch::channel(false);
    let received = Arc::new(Mutex::new(Vec::<ReceivedMessage>::new()));

    // scheduler lag probe
    let lag_stop = stop_rx.clone();
    let lag_probe = tokio::spawn(async move {
        let mut samples = Vec::new();
        while !*lag_stop.borrow() {
            let before = Instant::now();
            tokio::time::sleep(LAG_PROBE_INTERVAL).await;
            let lag = before.elapsed().saturating_sub(LAG_PROBE_INTERVAL);
            samples.push((instrumentation::now_ns(), lag.as_micros() as u64));
        }
        samples
    });

    // per client: a sender task (if it has a partner) and a receiver task that owns the client
    let mut senders = JoinSet::new();
    let mut receivers = JoinSet::new();
    for (index, connected) in connected.into_iter().enumerate() {
        if let Some(partner) = plan.partner[index] {
            // the client sends at its group's rate and message size (per-group overrides let one
            // run carry heterogeneous traffic); a packet-timing flow matcher then sees a mix
            let group = &groups[client_group[index]];
            let mean_gap_s = 60.0 / group.rate_per_min;
            let sender = connected.client.split_sender();
            let destination = addresses[partner];
            let size = group.message_size;
            let mut rng = Prng(config.seed ^ ((index as u64 + 1) << 32));
            senders.spawn(async move {
                let mut sent = Vec::new();
                let mut abandoned = 0usize;
                let mut sequence = 0u64;
                loop {
                    let gap = Duration::from_secs_f64(rng.exponential(mean_gap_s));
                    // never sleep past the end of sending: a very low rate (e.g. a cover-only
                    // client) would otherwise keep the run waiting for a gap of hours
                    let now = instrumentation::now_ns();
                    if now >= send_until {
                        break;
                    }
                    let remaining = Duration::from_nanos(send_until.saturating_sub(now));
                    tokio::time::sleep(gap.min(remaining)).await;
                    let now = instrumentation::now_ns();
                    if now >= send_until {
                        break;
                    }
                    let id = ((index as u64) << 32) | sequence;
                    sequence += 1;
                    let t_submit_ns = instrumentation::now_ns();
                    let message = payload(id, index, size, t_submit_ns);
                    // a client that cannot keep up stops accepting messages; give up on a
                    // submission still waiting when sending ends, so the run always finishes
                    let remaining = Duration::from_nanos(send_until.saturating_sub(now));
                    match tokio::time::timeout(
                        remaining,
                        sender.send_plain_message(destination, message),
                    )
                    .await
                    {
                        Ok(Ok(())) => {}
                        Ok(Err(err)) => {
                            eprintln!("client {index}: failed to send message {id}: {err}");
                            continue;
                        }
                        Err(_) => {
                            abandoned += 1;
                            break;
                        }
                    }
                    sent.push(SentMessage {
                        id,
                        sender: index,
                        receiver: partner,
                        t_submit_ns,
                        size,
                    });
                }
                (sent, abandoned)
            });
        }

        let mut client = connected.client;
        let shutdown = connected._shutdown;
        let mut stop = stop_rx.clone();
        let received = received.clone();
        receivers.spawn(async move {
            loop {
                tokio::select! {
                    _ = stop.changed() => break,
                    messages = client.wait_for_messages() => {
                        let Some(messages) = messages else { break };
                        let t_received_ns = instrumentation::now_ns();
                        let mut received = received.lock().unwrap();
                        for message in messages {
                            if let Some(id) = parse_payload_id(&message.message) {
                                received.push(ReceivedMessage { id, receiver: index, t_received_ns });
                            }
                        }
                    }
                }
            }
            if tokio::time::timeout(Duration::from_secs(10), client.disconnect())
                .await
                .is_err()
            {
                eprintln!("client {index}: disconnect timed out");
            }
            drop(shutdown);
        });
    }

    println!(
        "sending for {} s (first {} s are warm-up)...",
        config.duration_s, config.warmup_s
    );
    let mut sent = Vec::new();
    let mut abandoned = 0;
    while let Some(joined) = senders.join_next().await {
        let (client_sent, client_abandoned) =
            joined.map_err(|err| format!("sender task failed: {err}"))?;
        sent.extend(client_sent);
        abandoned += client_abandoned;
    }
    if abandoned > 0 {
        println!(
            "WARNING: {abandoned} clients could not accept a message before sending ended (overloaded)"
        );
    }
    let t_send_end = instrumentation::now_ns();

    // wait until everything sent has arrived, at most drain_s
    println!(
        "sent {} messages; waiting up to {} s for the rest to arrive...",
        sent.len(),
        config.drain_s
    );
    let drain_deadline = Instant::now() + Duration::from_secs(config.drain_s);
    while Instant::now() < drain_deadline {
        if received.lock().unwrap().len() >= sent.len() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    let t_end = instrumentation::now_ns();

    let _ = stop_tx.send(true);
    if tokio::time::timeout(SHUTDOWN_TIMEOUT, async {
        while receivers.join_next().await.is_some() {}
    })
    .await
    .is_err()
    {
        eprintln!("some clients did not shut down within {SHUTDOWN_TIMEOUT:?}; aborting them");
        receivers.abort_all();
    }
    let lag = lag_probe.await.unwrap_or_default();
    let received = std::mem::take(&mut *received.lock().unwrap());
    write_messages(&config, &sent, &received)?;
    write_lag(&config, &lag)?;
    write_run(
        &config,
        &topology,
        t_start,
        t_send_end,
        t_end,
        abandoned,
    )?;
    print_summary(&config, &groups, &client_group, &sent, &received, &lag);
    Ok(())
}

fn write_file(config: &Config, name: &str, contents: String) -> Result<(), String> {
    let path = config.output_dir.join(name);
    std::fs::write(&path, contents)
        .map_err(|err| format!("failed to write {}: {err}", path.display()))
}

fn write_clients(
    config: &Config,
    groups: &[Group],
    client_group: &[usize],
    plan: &Plan,
    gateways: &[String],
    addresses: &[Recipient],
) -> Result<(), String> {
    let mut csv = String::from(
        "client,group,strategy,real_routing,real_ack_routing,cover_routing,cover_ack_routing,gateway,partner,tag,wire_tag,rate_per_min,message_size,address\n",
    );
    for (index, address) in addresses.iter().enumerate() {
        let group = &groups[client_group[index]];
        let partner = plan.partner[index].map_or(String::new(), |p| p.to_string());
        let _ = writeln!(
            csv,
            "{index},\"{}\",\"{}\",{},{},{},{},{},{partner},{},{},{},{},{address}",
            group.name,
            group.strategy_arg,
            group.routing.real,
            group.routing.real_ack,
            group.routing.cover,
            group.routing.cover_ack,
            gateways[plan.gateway[index]],
            instrumentation::recipient_tag(address),
            // tag the wire-level events carry (hash of the client's identity key), so the
            // MixMatch analysis can map each client's gateway-link flows to its pair
            instrumentation::identity_tag(&address.identity().to_bytes()),
            group.rate_per_min,
            group.message_size,
        );
    }
    write_file(config, "clients.csv", csv)
}

fn write_messages(
    config: &Config,
    sent: &[SentMessage],
    received: &[ReceivedMessage],
) -> Result<(), String> {
    let arrivals: std::collections::HashMap<u64, &ReceivedMessage> =
        received.iter().map(|r| (r.id, r)).collect();
    let mut csv = String::from("message,sender,receiver,size,t_submit_ns,t_received_ns\n");
    for message in sent {
        let t_received = arrivals
            .get(&message.id)
            .filter(|r| r.receiver == message.receiver)
            .map_or(String::new(), |r| r.t_received_ns.to_string());
        let _ = writeln!(
            csv,
            "{},{},{},{},{},{t_received}",
            message.id, message.sender, message.receiver, message.size, message.t_submit_ns
        );
    }
    write_file(config, "messages.csv", csv)
}

fn write_lag(config: &Config, lag: &[(u64, u64)]) -> Result<(), String> {
    let mut csv = String::from("t_ns,lag_us\n");
    for (t, lag) in lag {
        let _ = writeln!(csv, "{t},{lag}");
    }
    write_file(config, "lag.csv", csv)
}

/// Offset from the load test's monotonic clock (`instrumentation::now_ns`) to the wall clock the
/// wire logs use, so the timeline can be placed on the logs' time axis.
fn wall_offset_ns() -> i64 {
    let wall = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as i64)
        .unwrap_or(0);
    wall - instrumentation::now_ns() as i64
}

fn write_run(
    config: &Config,
    topology: &NymTopology,
    t_start: u64,
    t_send_end: u64,
    t_end: u64,
    abandoned: usize,
) -> Result<(), String> {
    let nodes: Vec<serde_json::Value> = topology
        .mixnodes()
        .map(|node| (node, "mixnode"))
        .chain(topology.entry_gateways().map(|node| (node, "gateway")))
        .map(|(node, role)| {
            serde_json::json!({
                "node_id": node.node_id,
                "role": role,
                "mix_host": node.mix_host.to_string(),
                "packed_address": instrumentation::pack_address(node.mix_host),
            })
        })
        .collect();
    let run = serde_json::json!({
        "config": {
            "topology": config.topology,
            "network": config.network.as_deref().unwrap_or("localnet"),
            "gateways": config.gateways,
            "duration_s": config.duration_s,
            "warmup_s": config.warmup_s,
            "drain_s": config.drain_s,
            "message_size": config.message_size,
            "messages_per_client_per_min": config.messages_per_client_per_min,
            "pairing": config.pairing,
            "seed": config.seed,
            "client": {
                "loop_cover_traffic": config.client.loop_cover_traffic,
                "poisson_sending": config.client.poisson_sending,
                "average_packet_delay_ms": config.client.average_packet_delay_ms,
                "message_sending_average_delay_ms": config.client.message_sending_average_delay_ms,
                "loop_cover_traffic_average_delay_ms": config.client.loop_cover_traffic_average_delay_ms,
            },
            "groups": config.groups.iter().map(|g| serde_json::json!({
                "name": g.name, "count": g.count, "strategy": g.strategy,
                "message_size": g.message_size, "messages_per_client_per_min": g.messages_per_client_per_min,
                "average_packet_delay_ms": g.average_packet_delay_ms,
                "message_sending_average_delay_ms": g.message_sending_average_delay_ms,
                "loop_cover_traffic_average_delay_ms": g.loop_cover_traffic_average_delay_ms,
                "loop_cover_traffic": g.loop_cover_traffic, "poisson_sending": g.poisson_sending,
                "real_routing": g.real_routing, "real_ack_routing": g.real_ack_routing,
                "cover_routing": g.cover_routing, "cover_ack_routing": g.cover_ack_routing,
            })).collect::<Vec<_>>(),
        },
        "timeline_ns": {
            "start": t_start,
            "warmup_end": t_start + config.warmup_s * 1_000_000_000,
            "send_end": t_send_end,
            "end": t_end,
            // wall clock (ns since the Unix epoch, the wire logs' clock) = timeline value + this
            "wall_offset_ns": wall_offset_ns(),
        },
        "submissions_abandoned": abandoned,
        "nodes": nodes,
    });
    write_file(
        config,
        "run.json",
        serde_json::to_string_pretty(&run).unwrap_or_default(),
    )
}

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return f64::NAN;
    }
    let rank = ((p / 100.0) * (sorted.len() - 1) as f64).round() as usize;
    sorted[rank.min(sorted.len() - 1)]
}

fn print_summary(
    config: &Config,
    groups: &[Group],
    client_group: &[usize],
    sent: &[SentMessage],
    received: &[ReceivedMessage],
    lag: &[(u64, u64)],
) {
    let arrivals: std::collections::HashMap<u64, u64> =
        received.iter().map(|r| (r.id, r.t_received_ns)).collect();
    let warmup_end = sent
        .iter()
        .map(|m| m.t_submit_ns)
        .min()
        .unwrap_or(0)
        .saturating_add(config.warmup_s * 1_000_000_000);

    println!("\nmessage latency (submit -> fully received), excluding warm-up:");
    println!(
        "{:<24} {:>6} {:>9} {:>9} {:>9} {:>9}",
        "group", "sent", "delivered", "p50 ms", "p95 ms", "max ms"
    );
    for (g, group) in groups.iter().enumerate() {
        let messages: Vec<&SentMessage> = sent
            .iter()
            .filter(|m| client_group[m.sender] == g && m.t_submit_ns >= warmup_end)
            .collect();
        let mut latencies: Vec<f64> = messages
            .iter()
            .filter_map(|m| {
                arrivals
                    .get(&m.id)
                    .map(|t| (t - m.t_submit_ns) as f64 / 1e6)
            })
            .collect();
        latencies.sort_by(|a, b| a.total_cmp(b));
        println!(
            "{:<24} {:>6} {:>8.1}% {:>9.1} {:>9.1} {:>9.1}",
            group.name,
            messages.len(),
            100.0 * latencies.len() as f64 / messages.len().max(1) as f64,
            percentile(&latencies, 50.0),
            percentile(&latencies, 95.0),
            latencies.last().copied().unwrap_or(f64::NAN),
        );
    }
    let mut lags: Vec<f64> = lag.iter().map(|(_, l)| *l as f64 / 1000.0).collect();
    lags.sort_by(|a, b| a.total_cmp(b));
    println!(
        "\nscheduler lag: p50 {:.2} ms, p99 {:.2} ms, max {:.2} ms",
        percentile(&lags, 50.0),
        percentile(&lags, 99.0),
        lags.last().copied().unwrap_or(f64::NAN)
    );
    println!("results written to {}", config.output_dir.display());
}
