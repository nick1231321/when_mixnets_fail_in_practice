#!/bin/bash
# Live Nym network check, link observer on our OWN clients (client-side wire log, no capture, no sudo).
#   1. wait for the localnet netem-off queue to finish (it must not compete for CPU)
#   2. build nym-load-test with the client-side wire log + mainnet mode
#   3. probe: 1 pair, 60 s on mainnet; abort if a client cannot connect or nothing is delivered
#      (e.g. gateways refusing bandwidth without zk-nym credentials)
#   4. three runs, 5 pairs (10 clients) x 240 s each, SDK default mixing/cover, monitored
#   5. export (client-link view) and score: paper's rule and tree, leave-one-run-out on mainnet
# Everything goes to $OUT; REPORT.txt says how far it got and why it stopped.
set -u
LOCAL=$(cd "$(dirname "$0")/../.." && pwd)
HERE=$LOCAL/linkobs/mainnet
BIN=$LOCAL/self-test/target/release/nym-load-test
OUT=${OUT:-$HOME/flow-matching-data/2026-10-16/mainnet}
WAIT_LOG=${WAIT_LOG:-$HOME/flow-matching-data/2026-10-16/netem_off/queue.log}
mkdir -p "$OUT"; REPORT=$OUT/REPORT.txt
say () { echo "$(date +%T) $*" | tee -a "$REPORT"; }
stop () { say "STOPPED: $*"; exit 1; }

say "night script start; waiting for $WAIT_LOG to finish"
until grep -qE "netem re-applied|aborting" "$WAIT_LOG" 2>/dev/null; do sleep 30; done
grep -q "aborting" "$WAIT_LOG" && say "note: the netem-off queue aborted (see its log); continuing with mainnet"

say "building nym-load-test (client wire log + mainnet mode)"
( cd "$LOCAL/self-test" && cargo build --release --bin nym-load-test ) > "$OUT/build.log" 2>&1 \
  || stop "build failed, see $OUT/build.log"
say "build ok: $(ls -la "$BIN" | awk '{print $6, $7, $8}')"

run_mainnet () { # name seed clients duration
  local name=$1 seed=$2 clients=$3 dur=$4 run=$OUT/runs/$1
  mkdir -p "$run/wire"
  python3 "$HERE/pick_gateways.py" --out "$run/config.toml" --output-dir "$run" --seed "$seed" \
    --clients "$clients" --duration "$dur" >> "$REPORT" 2>&1 || return 1
  MIXCORR_WIRE_DIR="$run/wire" python3 "$LOCAL/resmon/resmon.py" wrap "$run" --load-test-log "$run/load_test.log" -- \
    sh -c "\"$BIN\" \"$run/config.toml\" > \"$run/load_test.log\" 2>&1" >> "$REPORT" 2>&1
  [ -f "$run/clients.csv" ] || return 1
  grep -A2 "delivered" "$run/load_test.log" | tee -a "$REPORT" > /dev/null
  return 0
}
delivered_pct () { awk '/delivered/{f=1;next} f && /%/{gsub("%","",$3); print $3; exit}' "$1/load_test.log"; }

if [ -z "${SKIP_PROBE:-}" ]; then
say "probe: 1 pair for 60 s on mainnet"
run_mainnet probe 101 2 60 || stop "probe could not connect (see $OUT/runs/probe/load_test.log): gateways may require zk-nym credentials"
p=$(delivered_pct "$OUT/runs/probe"); say "probe delivered ${p:-0}%"
awk -v d="${p:-0}" 'BEGIN{exit !(d+0 > 50)}' || stop "probe delivered ${p:-0}% (< 50 %), not continuing"
[ -s "$(ls "$OUT"/runs/probe/wire/wire-*.csv 2>/dev/null | head -1)" ] || stop "the client-side wire log is empty"
else say "probe skipped (SKIP_PROBE set)"; fi

for s in 1 2 3; do
  say "run mn_s$s: 10 clients x 240 s"
  run_mainnet "mn_s$s" "$s" 10 240 || { say "run mn_s$s failed to start, skipped"; continue; }
  say "mn_s$s delivered $(delivered_pct "$OUT/runs/mn_s$s")%; verdict $(python3 -c "import json;v=json.load(open('$OUT/runs/mn_s$s/verdict.json'));print(v['verdict'], '; '.join(v['invalid']+v['suspect']))")"
  python3 "$HERE/client_wire_export.py" --wire-dir "$OUT/runs/mn_s$s/wire" --run "$OUT/runs/mn_s$s" \
    --out "$OUT/exports/mn_s$s" >> "$REPORT" 2>&1 || say "export of mn_s$s failed"
done

SPECS=""
for s in 1 2 3; do [ -f "$OUT/exports/mn_s$s/flowpairs.txt" ] && SPECS="$SPECS --test mn_s$s=$OUT/exports/mn_s$s:$OUT/runs/mn_s$s"; done
[ -n "$SPECS" ] || stop "no mainnet run exported"
say "scoring (leave-one-run-out across the mainnet runs)"
python3 "$LOCAL/linkobs/score_tree.py" $SPECS 2>/dev/null | tee "$OUT/scores_mainnet.txt" | tee -a "$REPORT"
say "night script done"
