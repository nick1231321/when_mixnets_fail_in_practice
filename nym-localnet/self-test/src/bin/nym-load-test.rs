//! Localnet load generator: many clients in one process, grouped by path selection strategy,
//! sending messages to each other while latency and routes are recorded.
//!
//! Usage: nym-load-test CONFIG.toml [OUTPUT_DIR]
//! See load-test.example.toml and README.md.

use std::process::ExitCode;

fn main() -> ExitCode {
    nym_localnet_self_test::load_test::run()
}
