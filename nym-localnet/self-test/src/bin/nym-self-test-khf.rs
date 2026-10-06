//! Self-test with K-Hops Fixed (K-HF): the nodes on the given layers are sampled once per
//! session and reused by every packet; the other layers are sampled per packet.
//!
//! Usage: nym-self-test-khf [network.json] [--size BYTES] [--real-routing R] [--real-ack-routing R] [--cover-routing R] [--cover-ack-routing R] [--layers LAYERS]
//!
//! --layers LAYERS   comma-separated fixed layers, 1-3 (default: 1)

use std::process::ExitCode;

const DEFAULT_LAYERS: &str = "1";

fn main() -> ExitCode {
    nym_localnet_self_test::run(Some("--layers"), |arg| {
        nym_localnet_self_test::khf(arg.unwrap_or(DEFAULT_LAYERS))
    })
}
