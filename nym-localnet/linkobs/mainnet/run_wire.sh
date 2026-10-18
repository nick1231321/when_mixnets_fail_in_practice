#!/bin/bash
# One load-test run on the live Nym network, observed on the wire at our own clients: tcpdump on
# this machine's default interface records the clients' WebSocket connections to their entry
# gateways while nym-load-test runs under the resource monitor. Nothing is run or modified on any
# gateway; only this machine's own connections are captured (filter: the gateways' client ports).
#
#   run_wire.sh OUT_DIR [--seed N] [--clients N] [--duration S] [--strategy S] [--groups SPEC]
#                       [--mixing-ms N]            (extra options go to pick_gateways.py)
#
# OUT_DIR/ gets: config.toml, the load test's files (clients.csv, messages.csv, run.json, ...),
#   monitor.csv + verdict.json, capture/link.pcap, capture/capture.log, and the extracted wire view
#   capture/link.frames.csv.gz (plain ws://, one row per WebSocket frame) or
#   capture/link.segments.csv.gz (wss://, one row per TCP segment) — see linkobs/pcap_streams.py —
#   and capture/link.identities.csv (conn -> client identity key, read from the ws:// handshake).
# Needs sudo for tcpdump (run `sudo -v` first so the run is not interrupted by a password prompt).
# MAINNET_PORTS (default "9000,9001") = the gateways' client ports to capture; MAINNET_IFACE
# overrides the interface.
set -eu
out=${1:?OUT_DIR}; shift
HERE=$(cd "$(dirname "$0")" && pwd); LOCAL=$(cd "$HERE/../.." && pwd)
BIN=$LOCAL/self-test/target/release/nym-load-test
PORTS=${MAINNET_PORTS:-9000,9001}
mkdir -p "$out/capture"; out=$(cd "$out" && pwd)

if [ -n "${MAINNET_IFACE:-}" ]; then iface=$MAINNET_IFACE
elif [ "$(uname)" = Darwin ]; then iface=$(route -n get default | awk '/interface:/{print $2}')
else iface=$(ip route show default | awk '{print $5; exit}'); fi
filter=$(echo "$PORTS" | tr ',' '\n' | sed 's/^/tcp port /' | paste -sd '|' - | sed 's/|/ or /g')

python3 "$HERE/pick_gateways.py" --out "$out/config.toml" --output-dir "$out" "$@"
sudo -v
sudo tcpdump -i "$iface" -s 0 -B 65536 -nn -U -w "$out/capture/link.pcap" "$filter" > "$out/capture/tcpdump.log" 2>&1 &
cap=$!; sleep 2
kill -0 "$cap" 2>/dev/null || { cat "$out/capture/tcpdump.log"; echo "tcpdump did not start"; exit 1; }
echo "capturing $iface ($filter) -> $out/capture/link.pcap"
trap 'sudo kill -INT "$cap" 2>/dev/null || true' EXIT

python3 "$LOCAL/resmon/resmon.py" wrap "$out" --load-test-log "$out/load_test.log" \
  --thresholds delivered_min_pct=0 -- sh -c "\"$BIN\" \"$out/config.toml\" > \"$out/load_test.log\" 2>&1" || true
sleep 3
sudo kill -INT "$cap"; while kill -0 "$cap" 2>/dev/null; do sleep 0.5; done; trap - EXIT
grep -E 'packets (captured|dropped by kernel)' "$out/capture/tcpdump.log" | tr '\n' ' ' > "$out/capture/capture.log"; echo >> "$out/capture/capture.log"
cat "$out/capture/capture.log"
[ -f "$out/clients.csv" ] || { echo "load test failed, see $out/load_test.log"; exit 1; }

# wire view: WebSocket frames for plain ws:// (port 9000), TCP segments for wss:// (9001)
python3 "$LOCAL/linkobs/pcap_streams.py" "$out/capture/link.pcap" --gateway-ports "$PORTS" --out "$out/capture/link.frames.csv" \
  --identities "$out/capture/link.identities.csv"
python3 "$LOCAL/linkobs/pcap_streams.py" "$out/capture/link.pcap" --gateway-ports "$PORTS" --tls --out "$out/capture/link.segments.csv"
gzip -f "$out/capture/link.frames.csv" "$out/capture/link.segments.csv"
grep -E "delivered|p50" "$out/load_test.log" | tail -4
echo "done: $out"
