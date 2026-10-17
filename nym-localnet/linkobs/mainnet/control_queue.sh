#!/bin/bash
# Homogeneous baseline control runs on mainnet at the current time of day (same config as the night runs
# mn_s1..3: 10 clients, defaults, baseline routing), started right after the strategy queue, to separate
# the effect of heterogeneous profiles / strategies from the network's time-varying packet loss.
set -u
LOCAL=$(cd "$(dirname "$0")/../.." && pwd); HERE=$LOCAL/linkobs/mainnet
BIN=$LOCAL/self-test/target/release/nym-load-test
OUT=$HOME/flow-matching-data/2026-10-16/mainnet_control; mkdir -p "$OUT"; REPORT=$OUT/REPORT.txt
WAIT=$HOME/flow-matching-data/2026-10-16/mainnet_strat/REPORT.txt
say () { echo "$(date +%T) $*" | tee -a "$REPORT"; }
say "waiting for the strategy queue"
until grep -q "strategy queue done" "$WAIT" 2>/dev/null; do sleep 30; done
for seed in 41 42 43; do
  name=ctl_s$seed; run=$OUT/runs/$name; mkdir -p "$run/wire"; say "run $name"
  python3 "$HERE/pick_gateways.py" --out "$run/config.toml" --output-dir "$run" --seed $seed --clients 10 --duration 240 >> "$REPORT" 2>&1
  MIXCORR_WIRE_DIR="$run/wire" python3 "$LOCAL/resmon/resmon.py" wrap "$run" --load-test-log "$run/load_test.log" -- \
    sh -c "\"$BIN\" \"$run/config.toml\" > \"$run/load_test.log\" 2>&1" >> "$REPORT" 2>&1
  [ -f "$run/clients.csv" ] || { say "run $name FAILED"; continue; }
  python3 "$HERE/client_wire_export.py" --wire-dir "$run/wire" --run "$run" --out "$OUT/exports/$name" >> "$REPORT" 2>&1
  say "end $name: $(python3 -c "import json;v=json.load(open('$run/verdict.json'));print(v['verdict'])")"
done
SPECS=""; for r in "$OUT"/exports/ctl_s*; do [ -f "$r/flowpairs.txt" ] && n=$(basename $r) && SPECS="$SPECS --test $n=$r:$OUT/runs/$n"; done
[ -n "$SPECS" ] && python3 "$LOCAL/linkobs/score_tree.py" $SPECS 2>/dev/null | tee -a "$REPORT"
say "control queue done"
