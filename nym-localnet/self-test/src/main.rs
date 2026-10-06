//! Localnet self-test with the path selection strategy chosen on the command line.
//!
//! Usage: nym-self-test [network.json] [--strategy STRATEGY] [--size BYTES]
//!                      [--real-routing R] [--real-ack-routing R]
//!                      [--cover-routing R] [--cover-ack-routing R]
//!
//! STRATEGY: baseline (default) | khf:LAYERS (e.g. khf:1,2) | kw:K (e.g. kw:2) | alpha:ALPHA (e.g. alpha:0.8)
//! R (routing): strategy (default) | baseline; see README.md.

use std::process::ExitCode;

fn main() -> ExitCode {
    nym_localnet_self_test::run()
}
