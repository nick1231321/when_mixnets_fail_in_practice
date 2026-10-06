//! Self-test with α-Sticky Selection (α-SS): with probability α a packet reuses a route
//! already assigned in the session, otherwise it gets a fresh uniformly random route.
//!
//! Usage: nym-self-test-alpha [network.json] [--size BYTES] [--ack-routing ROUTING] [--cover-routing ROUTING] [--alpha ALPHA]
//!
//! --alpha ALPHA   reuse probability in [0, 1] (default: 0.8)

use std::process::ExitCode;

const DEFAULT_ALPHA: &str = "0.8";

fn main() -> ExitCode {
    nym_localnet_self_test::run(Some("--alpha"), |arg| {
        nym_localnet_self_test::alpha(arg.unwrap_or(DEFAULT_ALPHA))
    })
}
