#!/bin/bash
# Heterogeneous client profiles on the live Nym network (our own clients; client-side wire log).
# Three designs x 3 seeds, 10 clients (5 random pairs) x 240 s each:
#   H-mix      per-hop mixing 15 / 50 / 100 ms, defaults otherwise
#   H-traffic  mixing 15 ms; real traffic, loop cover and sending rate differ per group
#   H-all      both at once
# Then: leave-one-run-out scoring within each design, and scoring with models trained on the
# homogeneous mainnet runs (mn_s1..3), i.e. an attacker who calibrated on default clients only.
set -u
LOCAL=$(cd "$(dirname "$0")/../.." && pwd)
HERE=$LOCAL/linkobs/mainnet
BIN=$LOCAL/self-test/target/release/nym-load-test
OUT=${OUT:-$HOME/flow-matching-data/2026-10-16/mainnet_het}
HOMO=$HOME/flow-matching-data/2026-10-16/mainnet
mkdir -p "$OUT"; REPORT=$OUT/REPORT.txt
say () { echo "$(date +%T) $*" | tee -a "$REPORT"; }

design () { # name -> group profiles (bash 3 on macOS has no associative arrays)
  case $1 in
    hmix)     echo "A:4:15:::;B:3:50:::;C:3:100:::";;
    htraffic) echo "A:4:15:40:500:2:20000;B:3:15:20:200:6:50000;C:3:15:10:50:16:200000";;
    hall)     echo "A:4:15:40:500:2:20000;B:3:50:20:200:6:50000;C:3:100:10:50:16:200000";;
  esac
}

run_one () { # name seed groups
  local name=$1 seed=$2 groups=$3 run=$OUT/runs/$1
  mkdir -p "$run/wire"
  python3 "$HERE/pick_gateways.py" --out "$run/config.toml" --output-dir "$run" --seed "$seed" \
    --clients 10 --duration 240 --groups "$groups" >> "$REPORT" 2>&1 || return 1
  MIXCORR_WIRE_DIR="$run/wire" python3 "$LOCAL/resmon/resmon.py" wrap "$run" --load-test-log "$run/load_test.log" -- \
    sh -c "\"$BIN\" \"$run/config.toml\" > \"$run/load_test.log\" 2>&1" >> "$REPORT" 2>&1
  [ -f "$run/clients.csv" ] || return 1
  grep -A4 "delivered" "$run/load_test.log" | tee -a "$REPORT" > /dev/null
  python3 "$HERE/client_wire_export.py" --wire-dir "$run/wire" --run "$run" --out "$OUT/exports/$name" >> "$REPORT" 2>&1
}

say "heterogeneous mainnet queue start"
seed=20
for d in hmix htraffic hall; do
  for k in 1 2 3; do
    seed=$((seed + 1)); name="${d}_s$seed"
    say "run $name"
    if run_one "$name" "$seed" "$(design $d)"; then
      say "end $name: $(python3 -c "import json;v=json.load(open('$OUT/runs/$name/verdict.json'));print(v['verdict'], '; '.join(v['invalid']+v['suspect']))")"
    else
      say "run $name FAILED (see $OUT/runs/$name/load_test.log)"
    fi
  done
done

say "scoring"
for d in hmix htraffic hall; do
  SPECS=""; for r in "$OUT"/exports/${d}_s*; do [ -f "$r/flowpairs.txt" ] && n=$(basename "$r") && SPECS="$SPECS --test $n=$r:$OUT/runs/$n"; done
  [ -n "$SPECS" ] || continue
  echo "=== $d: leave-one-run-out within the design" | tee -a "$REPORT"
  python3 "$LOCAL/linkobs/score_tree.py" $SPECS 2>/dev/null | tee -a "$REPORT"
  echo "=== $d: trained on the homogeneous mainnet runs" | tee -a "$REPORT"
  python3 "$LOCAL/linkobs/score_tree.py" $(for s in 1 2 3; do echo "--train mn_s$s=$HOMO/exports/mn_s$s:$HOMO/runs/mn_s$s"; done) $SPECS 2>/dev/null | tee -a "$REPORT"
done
say "heterogeneous mainnet queue done"
