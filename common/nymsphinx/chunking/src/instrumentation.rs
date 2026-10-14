// Copyright 2026 - Nym Technologies SA <contact@nymtech.net>
// SPDX-License-Identifier: Apache-2.0

//! Instrumentation for the localnet flow-matching experiments. Two independent, low-overhead
//! facilities:
//!
//! * a **monotonic clock and stable client tags** used by the load generator to time its own
//!   message traffic and label clients ([`now_ns`], [`recipient_tag`], [`identity_tag`],
//!   [`pack_address`]);
//!
//! * a **gateway-side wire log**: when the environment variable `MIXCORR_WIRE_DIR` is set, a
//!   gateway appends one line per Sphinx packet crossing a client<->gateway link - in either
//!   direction, cover traffic included - to a per-process CSV file in that directory
//!   (`wire-<host>.csv`, columns `wall_ns,direction,client,size`). This is the gateway-adversary
//!   observation point used by flow-matching attacks such as MixMatch (PoPETs 2024.2): the
//!   gateway sees only timestamps and sizes, not content, and cannot tell real from cover
//!   traffic. Timestamps are wall-clock nanoseconds so logs from several gateways on one host
//!   share a time base. When the variable is unset, [`wire_log`] is a single `OnceLock` read.

use nym_sphinx_addressing::clients::Recipient;
use std::fs::File;
use std::io::{LineWriter, Write};
use std::net::SocketAddr;
use std::sync::{Mutex, OnceLock};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

// ---------------------------------------------------------------- monotonic clock + tags

/// Nanoseconds since the first call (a process-wide monotonic time base). Used by the load
/// generator so all its clients share one clock.
pub fn now_ns() -> u64 {
    static EPOCH: OnceLock<Instant> = OnceLock::new();
    EPOCH.get_or_init(Instant::now).elapsed().as_nanos() as u64
}

/// Stable 64-bit tag of a client address (FNV-1a of its bytes).
pub fn recipient_tag(recipient: &Recipient) -> u64 {
    identity_tag(&recipient.to_bytes())
}

/// Stable 64-bit tag of raw key bytes (FNV-1a), e.g. a client's ed25519 identity public key.
/// A client's gateway-side destination address is exactly its identity key, so the tag computed
/// at the gateway ([`wire_log`]) matches the one the load generator records for that client.
pub fn identity_tag(bytes: &[u8]) -> u64 {
    bytes.iter().fold(0xcbf2_9ce4_8422_2325_u64, |hash, byte| {
        (hash ^ u64::from(*byte)).wrapping_mul(0x0000_0100_0000_01b3)
    })
}

/// Packs an IPv4 socket address as `ip << 16 | port` (0 for IPv6).
pub fn pack_address(address: SocketAddr) -> u64 {
    match address {
        SocketAddr::V4(v4) => (u64::from(u32::from(*v4.ip())) << 16) | u64::from(v4.port()),
        SocketAddr::V6(_) => 0,
    }
}

// ---------------------------------------------------------------- gateway wire log (file)

static WIRE: OnceLock<Option<Mutex<LineWriter<File>>>> = OnceLock::new();

/// The gateway wire-log writer, or `None` when `MIXCORR_WIRE_DIR` is unset or could not be
/// opened. Opened once, lazily, into `<dir>/wire-<host>.csv` (host = container hostname) with a header.
fn wire_writer() -> Option<&'static Mutex<LineWriter<File>>> {
    WIRE.get_or_init(|| {
        let dir = std::env::var("MIXCORR_WIRE_DIR").ok()?;
        std::fs::create_dir_all(&dir).ok()?;
        // one file per gateway process; the container's hostname is unique (its id is 1 in every
        // container, so a pid would collide across gateways sharing the volume)
        let host = std::env::var("HOSTNAME").unwrap_or_else(|_| std::process::id().to_string());
        let path = format!("{dir}/wire-{host}.csv");
        let mut writer = LineWriter::new(File::create(&path).ok()?);
        writeln!(writer, "wall_ns,direction,client,size").ok()?;
        Some(Mutex::new(writer))
    })
    .as_ref()
}

/// Whether the gateway wire log is active (`MIXCORR_WIRE_DIR` set and the file opened).
pub fn wire_enabled() -> bool {
    wire_writer().is_some()
}

/// Appends a wire event: a packet crossing a client<->gateway link. `to_gateway` is the
/// direction (true = client -> gateway), `client` the [`identity_tag`] of the client, `size` the
/// wire size in bytes. A no-op (one `OnceLock` read) unless `MIXCORR_WIRE_DIR` is set.
pub fn wire_log(to_gateway: bool, client: u64, size: usize) {
    if let Some(writer) = wire_writer() {
        let ns = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0);
        let direction = if to_gateway { "to_gw" } else { "from_gw" };
        if let Ok(mut writer) = writer.lock() {
            let _ = writeln!(writer, "{ns},{direction},{client},{size}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn now_ns_is_monotonic() {
        let a = now_ns();
        let b = now_ns();
        assert!(b >= a);
    }

    #[test]
    fn packs_ipv4_addresses() {
        let address: SocketAddr = "127.0.0.1:10004".parse().unwrap();
        assert_eq!(pack_address(address) & 0xffff, 10004);
        assert_eq!(pack_address(address) >> 16, 0x7f00_0001);
    }

    #[test]
    fn wire_log_is_noop_without_env() {
        // MIXCORR_WIRE_DIR is not set in the test environment
        wire_log(true, 123, 2413);
        assert!(!wire_enabled());
    }
}
