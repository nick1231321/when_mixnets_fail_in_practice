//! Self-test with K/W: K nodes are preselected per layer once per session; every packet
//! samples uniformly from that subset in each layer.
//!
//! Usage: nym-self-test-kw [network.json] [--size BYTES] [--real-routing R] [--real-ack-routing R] [--cover-routing R] [--cover-ack-routing R] [--k K]
//!
//! --k K   nodes preselected per layer (default: 2; values above the layer size use all nodes)

use std::process::ExitCode;

const DEFAULT_K: &str = "2";

fn main() -> ExitCode {
    nym_localnet_self_test::run(Some("--k"), |arg| {
        nym_localnet_self_test::kw(arg.unwrap_or(DEFAULT_K))
    })
}
