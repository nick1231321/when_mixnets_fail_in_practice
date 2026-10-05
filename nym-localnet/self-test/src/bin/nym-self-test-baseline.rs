//! Self-test with the baseline strategy: every packet gets an independent, uniformly random
//! route (current Nym behaviour).
//!
//! Usage: nym-self-test-baseline [network.json] [--size BYTES]

use nym_sdk::mixnet::PathSelectionStrategy;
use std::process::ExitCode;

fn main() -> ExitCode {
    nym_localnet_self_test::run(None, |_| Ok(PathSelectionStrategy::Baseline))
}
