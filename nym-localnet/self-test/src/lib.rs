//! Localnet smoke test shared by the self-test binaries: connect an ephemeral client to the
//! local mixnet using the generated topology file, send a message to ourselves and wait for
//! it to come back through gateway -> mix1 -> mix2 -> mix3 -> gateway.
//!
//! Every binary accepts:
//!   [path/to/network.json]   topology file (default: data/network.json)
//!   --size BYTES             size of the message sent to ourselves (default: a short
//!                            greeting). Each sphinx packet carries ~2 KB, so e.g. 100000
//!                            spreads the message over ~50 packets of the same session.
//!
//! plus the parameter of its path selection strategy (see "When Mixnets Fail", NDSS 2026).
//!
//! Path selection decisions are logged under `nym_topology::path_selection`: new sessions at
//! info level (shown by default) and every chosen route at debug level, e.g.
//!   RUST_LOG=warn,nym_topology::path_selection=debug nym-self-test-khf --layers 1,2

use nym_sdk::mixnet::{self, MixnetMessageSender, PathSelectionStrategy};
use nym_topology::HardcodedTopologyProvider;
use std::process::ExitCode;
use std::time::{Duration, Instant};

const TIMEOUT: Duration = Duration::from_secs(60);
const DEFAULT_LOG_FILTER: &str = "warn,nym_topology::path_selection=info,nym_sphinx::preparer=info";

/// Builds the strategy from the value of the binary's strategy flag (`None` if not given).
pub type StrategyFromArg = fn(Option<&str>) -> Result<PathSelectionStrategy, String>;

struct Args {
    topology_path: String,
    strategy_arg: Option<String>,
    message_size: Option<usize>,
}

/// Entry point of a self-test binary. `strategy_flag` is the command line flag carrying the
/// strategy parameter (e.g. `--k`), if the strategy has one.
pub fn run(strategy_flag: Option<&str>, strategy_from_arg: StrategyFromArg) -> ExitCode {
    if std::env::var_os("RUST_LOG").is_none() {
        std::env::set_var("RUST_LOG", DEFAULT_LOG_FILTER);
    }
    nym_bin_common::logging::setup_tracing_logger();

    let args = match parse_args(std::env::args().skip(1), strategy_flag) {
        Ok(args) => args,
        Err(err) => {
            eprintln!("{err}");
            return ExitCode::FAILURE;
        }
    };
    let strategy = match strategy_from_arg(args.strategy_arg.as_deref()) {
        Ok(strategy) => strategy,
        Err(err) => {
            eprintln!("{err}");
            return ExitCode::FAILURE;
        }
    };

    tokio::runtime::Runtime::new()
        .expect("failed to start the tokio runtime")
        .block_on(self_test(args.topology_path, strategy, args.message_size))
}

async fn self_test(
    topology_path: String,
    strategy: PathSelectionStrategy,
    message_size: Option<usize>,
) -> ExitCode {
    let provider = match HardcodedTopologyProvider::new_from_file(&topology_path) {
        Ok(provider) => provider,
        Err(err) => {
            eprintln!("failed to load topology from '{topology_path}': {err}");
            return ExitCode::FAILURE;
        }
    };

    let mut client = match mixnet::MixnetClientBuilder::new_ephemeral()
        .custom_topology_provider(Box::new(provider))
        .path_selection_strategy(strategy)
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

fn parse_args(
    mut args: impl Iterator<Item = String>,
    strategy_flag: Option<&str>,
) -> Result<Args, String> {
    let mut parsed = Args {
        topology_path: "data/network.json".to_string(),
        strategy_arg: None,
        message_size: None,
    };

    while let Some(arg) = args.next() {
        if Some(arg.as_str()) == strategy_flag {
            parsed.strategy_arg = Some(args.next().ok_or(format!("{arg} requires a value"))?);
        } else if arg == "--size" {
            let value = args.next().ok_or("--size requires a value")?;
            match value.parse() {
                Ok(size) if size > 0 => parsed.message_size = Some(size),
                _ => return Err(format!("invalid size '{value}'")),
            }
        } else if arg.starts_with("--") {
            return Err(format!("unknown option '{arg}'"));
        } else {
            parsed.topology_path = arg;
        }
    }
    Ok(parsed)
}

/// K-HF: comma-separated mix layers (1-3) whose node is fixed for the session, e.g. `1,2`.
pub fn khf(layers: &str) -> Result<PathSelectionStrategy, String> {
    let fixed_layers = layers
        .split(',')
        .map(|layer| match layer.parse() {
            Ok(layer @ 1..=3) => Ok(layer),
            _ => Err(format!("invalid layer '{layer}' in '{layers}' (expected 1-3)")),
        })
        .collect::<Result<_, _>>()?;
    Ok(PathSelectionStrategy::KHopsFixed { fixed_layers })
}

/// K/W: number of nodes preselected per layer for the session.
pub fn kw(k: &str) -> Result<PathSelectionStrategy, String> {
    match k.parse() {
        Ok(k) if k > 0 => Ok(PathSelectionStrategy::KOverW { k }),
        _ => Err(format!("invalid K '{k}' (expected a positive integer)")),
    }
}

/// α-SS: probability of reusing a route already assigned in the session.
pub fn alpha(alpha: &str) -> Result<PathSelectionStrategy, String> {
    match alpha.parse() {
        Ok(a) if (0.0..=1.0).contains(&a) => Ok(PathSelectionStrategy::AlphaSticky { alpha: a }),
        _ => Err(format!("invalid alpha '{alpha}' (expected a value in [0, 1])")),
    }
}

/// Parses `baseline`, `khf:<layers>`, `kw:<k>` or `alpha:<alpha>`.
pub fn any_strategy(value: &str) -> Result<PathSelectionStrategy, String> {
    match value.split_once(':').unwrap_or((value, "")) {
        ("baseline", "") => Ok(PathSelectionStrategy::Baseline),
        ("khf", layers) => khf(layers),
        ("kw", k) => kw(k),
        ("alpha", a) => alpha(a),
        _ => Err(format!("invalid strategy '{value}'")),
    }
}
