//! Localnet smoke test: connect an ephemeral client to the local mixnet using
//! the generated topology file, send a message to ourselves and wait for it to
//! come back through gateway -> mix1 -> mix2 -> mix3 -> gateway.
//!
//! Usage: nym-self-test [path/to/network.json]

use nym_sdk::mixnet::{self, MixnetMessageSender};
use nym_topology::HardcodedTopologyProvider;
use std::process::ExitCode;
use std::time::{Duration, Instant};

const TIMEOUT: Duration = Duration::from_secs(60);

#[tokio::main]
async fn main() -> ExitCode {
    nym_bin_common::logging::setup_tracing_logger();

    let topology_path = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "data/network.json".to_string());

    let provider = match HardcodedTopologyProvider::new_from_file(&topology_path) {
        Ok(provider) => provider,
        Err(err) => {
            eprintln!("failed to load topology from '{topology_path}': {err}");
            return ExitCode::FAILURE;
        }
    };

    let mut client = match mixnet::MixnetClientBuilder::new_ephemeral()
        .custom_topology_provider(Box::new(provider))
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

    let payload = format!("hello from the localnet self-test @ {:?}", Instant::now());
    let sent_at = Instant::now();
    client
        .send_plain_message(our_address, &payload)
        .await
        .expect("failed to send message");
    println!("Sent: {payload}");

    let received = tokio::time::timeout(TIMEOUT, async {
        while let Some(messages) = client.wait_for_messages().await {
            for msg in messages {
                if msg.message == payload.as_bytes() {
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
