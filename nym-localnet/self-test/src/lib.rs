//! Localnet smoke test behind the `nym-self-test` binary: connect an ephemeral client to the
//! local mixnet using the generated topology file, send a message to ourselves and wait for
//! it to come back through gateway -> mix1 -> mix2 -> mix3 -> gateway.
//!
//! Arguments:
//!   [path/to/network.json]       topology file (default: data/network.json)
//!   --strategy STRATEGY          path selection strategy (see "When Mixnets Fail", NDSS 2026):
//!                                `baseline` (default), `khf:LAYERS` (e.g. `khf:1,2`),
//!                                `kw:K` (e.g. `kw:2`) or `alpha:ALPHA` (e.g. `alpha:0.8`)
//!   --size BYTES                 size of the message sent to ourselves (default: a short
//!                                greeting). Each sphinx packet carries ~2 KB, so e.g. 100000
//!                                spreads the message over ~50 packets of the same session.
//!   --real-routing ROUTING       real traffic: forward data packets and their reply SURBs
//!   --real-ack-routing ROUTING   SURB-ACKs of real data packets
//!   --cover-routing ROUTING      loop cover packets
//!   --cover-ack-routing ROUTING  SURB-ACKs of loop cover packets
//!                                ROUTING is `strategy` (default) or `baseline` (uniformly
//!                                random route, leaving the session untouched)
//!
//! Path selection decisions are logged under `nym_topology::path_selection`: new sessions at
//! info level (shown by default) and every chosen route at debug level, e.g.
//!   RUST_LOG=warn,nym_topology::path_selection=debug nym-self-test --strategy khf:1,2

use nym_sdk::mixnet::{self, MixnetMessageSender, PathSelectionStrategy, Routing, RoutingConfig};
use nym_topology::provider_trait::{async_trait, TopologyProvider};
use nym_topology::NymTopology;
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::{Duration, Instant};

const TIMEOUT: Duration = Duration::from_secs(60);
const DEFAULT_LOG_FILTER: &str = "warn,nym_topology::path_selection=info,nym_sphinx::preparer=info";

/// Re-reads the topology file on every refresh, so the client picks up the new sphinx keys
/// that the localnet writes after each key rotation.
struct FileTopologyProvider {
    path: PathBuf,
    last_good: NymTopology,
}

impl FileTopologyProvider {
    fn new(path: impl Into<PathBuf>) -> std::io::Result<Self> {
        let path = path.into();
        let last_good = NymTopology::new_from_file(&path)?;
        Ok(FileTopologyProvider { path, last_good })
    }
}

#[async_trait]
impl TopologyProvider for FileTopologyProvider {
    async fn get_new_topology(&mut self) -> Option<NymTopology> {
        match NymTopology::new_from_file(&self.path) {
            Ok(topology) => self.last_good = topology,
            Err(err) => eprintln!(
                "failed to reload topology from '{}', keeping the previous one: {err}",
                self.path.display()
            ),
        }
        Some(self.last_good.clone())
    }
}

struct Args {
    topology_path: String,
    strategy: PathSelectionStrategy,
    message_size: Option<usize>,
    routing_config: RoutingConfig,
}

/// Entry point of the `nym-self-test` binary.
pub fn run() -> ExitCode {
    if std::env::var_os("RUST_LOG").is_none() {
        std::env::set_var("RUST_LOG", DEFAULT_LOG_FILTER);
    }
    nym_bin_common::logging::setup_tracing_logger();

    let args = match parse_args(std::env::args().skip(1)) {
        Ok(args) => args,
        Err(err) => {
            eprintln!("{err}");
            return ExitCode::FAILURE;
        }
    };

    tokio::runtime::Runtime::new()
        .expect("failed to start the tokio runtime")
        .block_on(self_test(
            args.topology_path,
            args.strategy,
            args.routing_config,
            args.message_size,
        ))
}

async fn self_test(
    topology_path: String,
    strategy: PathSelectionStrategy,
    routing_config: RoutingConfig,
    message_size: Option<usize>,
) -> ExitCode {
    let provider = match FileTopologyProvider::new(&topology_path) {
        Ok(provider) => provider,
        Err(err) => {
            eprintln!("failed to load topology from '{topology_path}': {err}");
            return ExitCode::FAILURE;
        }
    };

    let mut client = match mixnet::MixnetClientBuilder::new_ephemeral()
        .custom_topology_provider(Box::new(provider))
        .path_selection_strategy(strategy)
        .real_routing(routing_config.real)
        .real_ack_routing(routing_config.real_ack)
        .cover_routing(routing_config.cover)
        .cover_ack_routing(routing_config.cover_ack)
        .build()
        .expect("failed to build client")
        .connect_to_mixnet()
        .await
    {
        Ok(client) => client,
        Err(err) => {
            eprintln!("failed to connect to the localnet: {err}");
            return ExitCode::FAILURE;
        }
    };

    let our_address = *client.nym_address();
    println!("Connected. Our nym address: {our_address}");
    println!(
        "Path selection strategy: {:?}",
        client.path_selection_strategy()
    );
    println!("Routing: {}", client.routing_config());

    let greeting = format!("hello from the localnet self-test @ {:?}", Instant::now());
    let payload = match message_size {
        Some(size) => greeting.bytes().cycle().take(size).collect::<Vec<u8>>(),
        None => greeting.clone().into_bytes(),
    };
    let sent_at = Instant::now();
    client
        .send_plain_message(our_address, &payload)
        .await
        .expect("failed to send message");
    match message_size {
        Some(size) => println!("Sent: {size} byte message"),
        None => println!("Sent: {greeting}"),
    }

    let received = tokio::time::timeout(TIMEOUT, async {
        while let Some(messages) = client.wait_for_messages().await {
            for msg in messages {
                if msg.message == payload {
                    return true;
                }
            }
        }
        false
    })
    .await
    .unwrap_or(false);

    client.disconnect().await;

    if received {
        println!(
            "SUCCESS: received our own message back after {:.2?}",
            sent_at.elapsed()
        );
        ExitCode::SUCCESS
    } else {
        eprintln!("FAILURE: message did not arrive within {TIMEOUT:?}");
        ExitCode::FAILURE
    }
}

fn parse_args(mut args: impl Iterator<Item = String>) -> Result<Args, String> {
    let mut parsed = Args {
        topology_path: "data/network.json".to_string(),
        strategy: PathSelectionStrategy::Baseline,
        message_size: None,
        routing_config: RoutingConfig::default(),
    };

    while let Some(arg) = args.next() {
        if arg == "--strategy" {
            let value = args.next().ok_or("--strategy requires a value")?;
            parsed.strategy = parse_strategy(&value)?;
        } else if arg == "--size" {
            let value = args.next().ok_or("--size requires a value")?;
            match value.parse() {
                Ok(size) if size > 0 => parsed.message_size = Some(size),
                _ => return Err(format!("invalid size '{value}'")),
            }
        } else if let Some(routing) = routing_flag(&mut parsed.routing_config, &arg) {
            let value = args.next().ok_or(format!("{arg} requires a value"))?;
            *routing = value.parse()?;
        } else if arg.starts_with("--") {
            return Err(format!("unknown option '{arg}'"));
        } else {
            parsed.topology_path = arg;
        }
    }
    Ok(parsed)
}

/// The routing that `flag` sets, if it is one of the routing flags.
fn routing_flag<'a>(config: &'a mut RoutingConfig, flag: &str) -> Option<&'a mut Routing> {
    match flag {
        "--real-routing" => Some(&mut config.real),
        "--real-ack-routing" => Some(&mut config.real_ack),
        "--cover-routing" => Some(&mut config.cover),
        "--cover-ack-routing" => Some(&mut config.cover_ack),
        _ => None,
    }
}

/// K-HF: comma-separated mix layers (1-3) whose node is fixed for the session, e.g. `1,2`.
fn khf(layers: &str) -> Result<PathSelectionStrategy, String> {
    let fixed_layers = layers
        .split(',')
        .map(|layer| match layer.parse() {
            Ok(layer @ 1..=3) => Ok(layer),
            _ => Err(format!(
                "invalid layer '{layer}' in '{layers}' (expected 1-3)"
            )),
        })
        .collect::<Result<_, _>>()?;
    Ok(PathSelectionStrategy::KHopsFixed { fixed_layers })
}

/// K/W: number of nodes preselected per layer for the session.
fn kw(k: &str) -> Result<PathSelectionStrategy, String> {
    match k.parse() {
        Ok(k) if k > 0 => Ok(PathSelectionStrategy::KOverW { k }),
        _ => Err(format!("invalid K '{k}' (expected a positive integer)")),
    }
}

/// α-SS: probability of reusing a route already assigned in the session.
fn alpha(alpha: &str) -> Result<PathSelectionStrategy, String> {
    match alpha.parse() {
        Ok(a) if (0.0..=1.0).contains(&a) => Ok(PathSelectionStrategy::AlphaSticky { alpha: a }),
        _ => Err(format!(
            "invalid alpha '{alpha}' (expected a value in [0, 1])"
        )),
    }
}

/// Parses `baseline`, `khf:<layers>`, `kw:<k>` or `alpha:<alpha>`.
fn parse_strategy(value: &str) -> Result<PathSelectionStrategy, String> {
    match value.split_once(':').unwrap_or((value, "")) {
        ("baseline", "") => Ok(PathSelectionStrategy::Baseline),
        ("khf", layers) => khf(layers),
        ("kw", k) => kw(k),
        ("alpha", a) => alpha(a),
        _ => Err(format!("invalid strategy '{value}'")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(args: &[&str]) -> Result<Args, String> {
        parse_args(args.iter().map(|arg| arg.to_string()))
    }

    #[test]
    fn strategy_defaults_to_baseline() {
        let args = parse(&[]).unwrap();
        assert_eq!(args.strategy, PathSelectionStrategy::Baseline);
        assert_eq!(args.topology_path, "data/network.json");
    }

    #[test]
    fn strategies_are_parsed() {
        let strategy = |value| parse(&["--strategy", value]).map(|args| args.strategy);
        assert_eq!(strategy("baseline"), Ok(PathSelectionStrategy::Baseline));
        assert_eq!(
            strategy("khf:1,2"),
            Ok(PathSelectionStrategy::KHopsFixed {
                fixed_layers: vec![1, 2]
            })
        );
        assert_eq!(strategy("kw:2"), Ok(PathSelectionStrategy::KOverW { k: 2 }));
        assert_eq!(
            strategy("alpha:0.5"),
            Ok(PathSelectionStrategy::AlphaSticky { alpha: 0.5 })
        );
        for invalid in ["khf:4", "khf:", "kw:0", "alpha:1.5", "alpha", "random"] {
            assert!(strategy(invalid).is_err(), "{invalid}");
        }
        // the flags of the removed per-strategy binaries
        assert!(parse(&["--layers", "1,2"]).is_err());
    }

    #[test]
    fn routing_flags_default_to_the_strategy() {
        let args = parse(&["--strategy", "khf:1,2"]).unwrap();
        assert_eq!(args.routing_config, RoutingConfig::default());
    }

    #[test]
    fn routing_flags_are_parsed() {
        let args = parse(&[
            "--real-routing",
            "baseline",
            "--real-ack-routing",
            "strategy",
            "--cover-routing",
            "baseline",
            "--cover-ack-routing",
            "strategy",
        ])
        .unwrap();
        assert_eq!(
            args.routing_config,
            RoutingConfig {
                real: Routing::Baseline,
                real_ack: Routing::Strategy,
                cover: Routing::Baseline,
                cover_ack: Routing::Strategy,
            }
        );

        let args = parse(&[
            "--real-ack-routing",
            "baseline",
            "--cover-ack-routing",
            "baseline",
        ])
        .unwrap();
        assert_eq!(
            args.routing_config,
            RoutingConfig {
                real: Routing::Strategy,
                real_ack: Routing::Baseline,
                cover: Routing::Strategy,
                cover_ack: Routing::Baseline,
            }
        );
    }

    #[test]
    fn invalid_routing_is_rejected() {
        assert!(parse(&["--real-routing", "random"]).is_err());
        assert!(parse(&["--cover-ack-routing"]).is_err());
        // the old two-flag interface is gone
        assert!(parse(&["--ack-routing", "baseline"]).is_err());
    }
}
