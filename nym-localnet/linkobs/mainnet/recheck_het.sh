#!/bin/bash
# Decisive check: does a heterogeneous run lose ~7 % again when run right after homogeneous controls (~1.5 %)?
set -u
LOCAL=$(cd "$(dirname "$0")/../.." && pwd); HERE=$LOCAL/linkobs/mainnet; BIN=$LOCAL/self-test/target/release/nym-load-test
OUT=$HOME/flow-matching-data/2026-10-16/mainnet_het; REPORT=$OUT/REPORT.txt
WAIT=$HOME/flow-matching-data/2026-10-16/mainnet_control/REPORT.txt
until grep -q "control queue done" "$WAIT" 2>/dev/null; do sleep 30; done
name=hmix_s51; run=$OUT/runs/$name; mkdir -p "$run/wire"
echo "$(date +%T) recheck run $name (H-mix, after the controls)" | tee -a "$REPORT"
python3 "$HERE/pick_gateways.py" --out "$run/config.toml" --output-dir "$run" --seed 51 --clients 10 --duration 240 --groups "A:4:15:::;B:3:50:::;C:3:100:::" >> "$REPORT" 2>&1
MIXCORR_WIRE_DIR="$run/wire" python3 "$LOCAL/resmon/resmon.py" wrap "$run" --load-test-log "$run/load_test.log" -- \
  sh -c "\"$BIN\" \"$run/config.toml\" > \"$run/load_test.log\" 2>&1" >> "$REPORT" 2>&1
python3 "$HERE/client_wire_export.py" --wire-dir "$run/wire" --run "$run" --out "$OUT/exports/$name" >> "$REPORT" 2>&1
echo "$(date +%T) recheck done" | tee -a "$REPORT"
