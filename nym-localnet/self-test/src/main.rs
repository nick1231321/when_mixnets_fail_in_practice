//! Self-test with the path selection strategy chosen on the command line.
//!
//! Usage: nym-self-test [network.json] [--size BYTES] [--ack-routing ROUTING] [--cover-routing ROUTING] [--strategy <STRATEGY>]
//!
//! STRATEGY: baseline (default) | khf:1,2 | kw:10 | alpha:0.8
//! ROUTING: strategy (default) | baseline; see the library docs.
//! See the strategy-specific binaries (nym-self-test-{baseline,khf,kw,alpha}) for details.

use std::process::ExitCode;

fn main() -> ExitCode {
    nym_localnet_self_test::run(Some("--strategy"), |arg| {
        nym_localnet_self_test::any_strategy(arg.unwrap_or("baseline"))
    })
}
