//! Localnet smoke test: connect an ephemeral client to the local mixnet using
//! the generated topology file, send a message to ourselves and wait for it to
//! come back through gateway -> mix1 -> mix2 -> mix3 -> gateway.
//!
//! Usage: nym-self-test [path/to/network.json] [--strategy <STRATEGY>] [--size <BYTES>]
//!
//! --size BYTES   size of the message sent to ourselves (default: a short greeting). Each
//!                sphinx packet carries ~2 KB, so e.g. 100000 spreads the message over ~50
//!                packets of the same session.
//!
//! STRATEGY (path selection, see "When Mixnets Fail", NDSS 2026):
//!   baseline       independent uniform route per packet (default)
//!   khf:1,2        K-Hops Fixed: fix the nodes on layers 1 and 2 for the session
//!   kw:10          K/W: preselect 10 nodes per layer for the session
//!   alpha:0.8      α-Sticky Selection: reuse a previous route with probability 0.8
//!
//! Path selection decisions are logged under `nym_topology::path_selection`: new sessions at
//! info level (shown by default) and every chosen route at debug level, e.g.
//!   RUST_LOG=warn,nym_topology::path_selection=debug nym-self-test --strategy khf:1,2

use nym_sdk::mixnet::{self, MixnetMessageSender, PathSelectionStrategy};
use nym_topology::HardcodedTopologyProvider;
use std::process::ExitCode;
use std::time::{Duration, Instant};

const TIMEOUT: Duration = Duration::from_secs(60);
const DEFAULT_LOG_FILTER: &str = "warn,nym_topology::path_selection=info,nym_sphinx::preparer=info";

struct Args {
    topology_path: String,
    strategy: PathSelectionStrategy,
    message_size: Option<usize>,
}

#[tokio::main]
async fn main() -> ExitCode {
    if std::env::var_os("RUST_LOG").is_none() {
        std::env::set_var("RUST_LOG", DEFAULT_LOG_FILTER);
    }
    nym_bin_common::logging::setup_tracing_logger();

    let Args {
        topology_path,
        strategy,
        message_size,
    } = match parse_args(std::env::args().skip(1)) {
        Ok(args) => args,
        Err(err) => {
            eprintln!("{err}");
            return ExitCode::FAILURE;
        }
    };

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

fn parse_args(mut args: impl Iterator<Item = String>) -> Result<Args, String> {
    let mut parsed = Args {
        topology_path: "data/network.json".to_string(),
        strategy: PathSelectionStrategy::Baseline,
        message_size: None,
    };

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--strategy" => {
                let value = args.next().ok_or("--strategy requires a value")?;
                parsed.strategy = parse_strategy(&value)?;
            }
            "--size" => {
                let value = args.next().ok_or("--size requires a value")?;
                match value.parse() {
                    Ok(size) if size > 0 => parsed.message_size = Some(size),
                    _ => return Err(format!("invalid size '{value}'")),
                }
            }
            _ => parsed.topology_path = arg,
        }
    }
    Ok(parsed)
}

fn parse_strategy(value: &str) -> Result<PathSelectionStrategy, String> {
    let (name, param) = value.split_once(':').unwrap_or((value, ""));
    let invalid = || format!("invalid strategy '{value}'");

    match name {
        "baseline" => Ok(PathSelectionStrategy::Baseline),
        "khf" => {
            let fixed_layers = param
                .split(',')
                .map(|layer| match layer.parse() {
                    Ok(layer @ 1..=3) => Ok(layer),
                    _ => Err(format!("invalid layer '{layer}' in '{value}' (expected 1-3)")),
                })
                .collect::<Result<_, _>>()?;
            Ok(PathSelectionStrategy::KHopsFixed { fixed_layers })
        }
        "kw" => match param.parse() {
            Ok(k) if k > 0 => Ok(PathSelectionStrategy::KOverW { k }),
            _ => Err(invalid()),
        },
        "alpha" => match param.parse() {
            Ok(alpha) if (0.0..=1.0).contains(&alpha) => {
                Ok(PathSelectionStrategy::AlphaSticky { alpha })
            }
            _ => Err(invalid()),
        },
        _ => Err(invalid()),
    }
}
