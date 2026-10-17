#!/bin/bash
# Path-selection strategies on the live Nym network (our own clients, default profile, client-side wire log).
# Waits for the heterogeneous queue, then 3 strategies x 3 seeds, 10 clients (5 random pairs) x 240 s.
# Scoring: leave-one-run-out within each strategy, and models trained on the baseline mainnet runs.
set -u
LOCAL=$(cd "$(dirname "$0")/../.." && pwd)
HERE=$LOCAL/linkobs/mainnet
BIN=$LOCAL/self-test/target/release/nym-load-test
OUT=${OUT:-$HOME/flow-matching-data/2026-10-16/mainnet_strat}
HOMO=$HOME/flow-matching-data/2026-10-16/mainnet
WAIT=$HOME/flow-matching-data/2026-10-16/mainnet_het/REPORT.txt
mkdir -p "$OUT"; REPORT=$OUT/REPORT.txt
say () { echo "$(date +%T) $*" | tee -a "$REPORT"; }

say "waiting for the heterogeneous queue"
until grep -q "heterogeneous mainnet queue done" "$WAIT" 2>/dev/null; do sleep 30; done
say "strategy queue start"
seed=30
for st in "khf:1,2" "kw:2" "alpha:0.5"; do
  tag=$(echo "$st" | tr ':,' '-.')
  for k in 1 2 3; do
    seed=$((seed + 1)); name="${tag}_s$seed"; run=$OUT/runs/$name
    say "run $name (strategy $st)"
    mkdir -p "$run/wire"
    python3 "$HERE/pick_gateways.py" --out "$run/config.toml" --output-dir "$run" --seed "$seed" \
      --clients 10 --duration 240 --strategy "$st" >> "$REPORT" 2>&1 || { say "config failed"; continue; }
    MIXCORR_WIRE_DIR="$run/wire" python3 "$LOCAL/resmon/resmon.py" wrap "$run" --load-test-log "$run/load_test.log" -- \
      sh -c "\"$BIN\" \"$run/config.toml\" > \"$run/load_test.log\" 2>&1" >> "$REPORT" 2>&1
    if [ ! -f "$run/clients.csv" ]; then say "run $name FAILED (see $run/load_test.log)"; continue; fi
    grep -A2 "delivered" "$run/load_test.log" | tee -a "$REPORT" > /dev/null
    python3 "$HERE/client_wire_export.py" --wire-dir "$run/wire" --run "$run" --out "$OUT/exports/$name" >> "$REPORT" 2>&1
    say "end $name: $(python3 -c "import json;v=json.load(open('$run/verdict.json'));print(v['verdict'], '; '.join(v['invalid']+v['suspect']))")"
  done
done
say "scoring"
for st in khf-1.2 kw-2 alpha-0.5; do
  SPECS=""; for r in "$OUT"/exports/${st}_s*; do [ -f "$r/flowpairs.txt" ] && n=$(basename "$r") && SPECS="$SPECS --test $n=$r:$OUT/runs/$n"; done
  [ -n "$SPECS" ] || continue
  echo "=== $st: leave-one-run-out within the strategy" | tee -a "$REPORT"
  python3 "$LOCAL/linkobs/score_tree.py" $SPECS 2>/dev/null | tee -a "$REPORT"
  echo "=== $st: trained on the baseline mainnet runs" | tee -a "$REPORT"
  python3 "$LOCAL/linkobs/score_tree.py" $(for s in 1 2 3; do echo "--train mn_s$s=$HOMO/exports/mn_s$s:$HOMO/runs/mn_s$s"; done) $SPECS 2>/dev/null | tee -a "$REPORT"
done
say "strategy queue done"
