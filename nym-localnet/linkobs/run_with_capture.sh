#!/bin/bash
# One load-test run observed from the network, with resource monitoring:
#   capture the client<->gateway links, run the load test under resmon, convert the capture to
#   per-cell events, export the observer's view and the gateway's view of the same run, score both.
#
#   run_with_capture.sh CONFIG.toml OUT_DIR [--tls] [--score]
#
# OUT_DIR: capture/ (pcaps), events.csv, run/ (load test output + monitor.csv + verdict.json),
#          export_link/ (observer), export_gateway/ (gateway), scores.txt
# Capture mode: LINKOBS_MODE=docker (default, no root) or host (sudo tcpdump on lo0), see capture.sh.
set -eu
cfg=${1:?config.toml}; out=${2:?out dir}; shift 2
tls=""; score=""
for arg in "$@"; do case $arg in --tls) tls="--tls";; --score) score=1;; esac; done
HERE=$(cd "$(dirname "$0")" && pwd)
LOCAL=$(cd "$HERE/.." && pwd)
BIN=$LOCAL/self-test/target/release/nym-load-test
WIRE=${MIXCORR_WIRE_DIR:-$LOCAL/data-netem/wire}
MODE=${LINKOBS_MODE:-docker}
mkdir -p "$out/run"; out=$(cd "$out" && pwd)
sed -E "s#^output_dir = .*#output_dir = \"$out/run\"#" "$cfg" > "$out/run/config.toml"

"$HERE/capture.sh" start "$out/capture"
trap '"$HERE/capture.sh" stop "$out/capture" >/dev/null 2>&1 || true' EXIT
# the load test under the resource monitor (monitor.csv + verdict.json in run/)
python3 "$LOCAL/resmon/resmon.py" wrap "$out/run" --load-test-log "$out/run/load_test.log" -- \
  sh -c "\"$BIN\" \"$out/run/config.toml\" > \"$out/run/load_test.log\" 2>&1" || true
sleep 3
"$HERE/capture.sh" stop "$out/capture"; trap - EXIT
[ -f "$out/run/clients.csv" ] || { echo "load test failed, see $out/run/load_test.log"; exit 1; }

# capture -> per-frame events (one file per gateway in docker mode, merged)
echo "conn,direction,t_ns,len,kind" > "$out/events.csv"
if [ "$MODE" = docker ]; then
  for p in "$out"/capture/*.pcap; do
    gw=$(basename "$p" .pcap)
    python3 "$HERE/pcap_streams.py" "$p" $tls --gateway-ports 9000,9001 --conn-prefix "$gw/" --out "$out/capture/$gw.events.csv"
    tail -n +2 "$out/capture/$gw.events.csv" >> "$out/events.csv"
  done
else
  python3 "$HERE/pcap_streams.py" "$out/capture/link.pcap" $tls --out "$out/capture/link.events.csv"
  tail -n +2 "$out/capture/link.events.csv" >> "$out/events.csv"
fi
python3 "$HERE/link_export.py" --events "$out/events.csv" --run "$out/run" --wire-dir "$WIRE" --out "$out/export_link" $tls
python3 "$LOCAL/mixmatch/export_their_format.py" export "$out/run" "$out/export_gateway" --wire-dir "$WIRE"
echo "exports: $out/export_link (observer)  $out/export_gateway (gateway)"

if [ -n "$score" ]; then
  {
    echo "--- run verdict: $(python3 -c "import json;v=json.load(open('$out/run/verdict.json'));print(v['verdict'], '; '.join(v['invalid']+v['suspect']))")"
    echo "--- paper's rule and the tree, observer view vs gateway view (5-fold by initiator within the run)"
    python3 "$HERE/score_tree.py" --test "link=$out/export_link:$out/run" --test "gateway=$out/export_gateway:$out/run" 2>/dev/null
  } | tee "$out/scores.txt"
fi
