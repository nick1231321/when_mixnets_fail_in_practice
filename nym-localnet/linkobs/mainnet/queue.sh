#!/bin/bash
# Run a live-network plan (design_mainnet.py) with run_wire.sh, one run after the other, resumably.
#
#   queue.sh PLAN.csv OUT_DIR            (sudo -v first: tcpdump needs it)
#
# A run whose OUT_DIR/runs/<run_id>/clients.csv exists is skipped, so the queue can be restarted and a
# failed run (no clients.csv) is redone. OUT_DIR/exclude_gateways.txt, one node id per line, keeps
# gateways that refuse connections out of the selection (the redone run then differs from its baseline
# twin in its gateway pair; note it).
# Touch OUT_DIR/PAUSE to pause between runs, OUT_DIR/STOP to stop. Each finished run is appended to
# OUT_DIR/manifest.csv with its verdict, delivery, loss on the wire (1 - acks / confirmations) and the
# number of labelled connections. The raw pcap (~0.5 GB per 10-client run) is kept for every
# KEEP_PCAP_EVERY-th run (default 5) and deleted otherwise once the wire view is extracted; the queue
# stops when less than MIN_FREE_GB (default 5) are free.
set -u
plan=${1:?PLAN.csv}; out=${2:?OUT_DIR}; mkdir -p "$out/runs"
HERE=$(cd "$(dirname "$0")" && pwd)
KEEP=${KEEP_PCAP_EVERY:-5}; MINFREE=${MIN_FREE_GB:-5}
log () { echo "[$(date -u +%m-%d\ %H:%M:%S)] $*" | tee -a "$out/queue.log"; }
[ -f "$out/manifest.csv" ] || echo "run_id,dataset,seed,clients,verdict,delivered_pct,p50_ms,p95_ms,sent,confirmations,acks,loss_pct,connections,labelled,pcap_kept" > "$out/manifest.csv"

n=0
tail -n +2 "$plan" | while IFS=, read -r run_id dataset seed clients duration drain strategy groups session; do
  n=$((n + 1)); run=$out/runs/$run_id
  [ -f "$run/clients.csv" ] && continue                          # done (a failed run has no clients.csv and is redone)
  while [ -f "$out/PAUSE" ]; do sleep 30; done
  [ -f "$out/STOP" ] && { log "STOP file found: stopping"; break; }
  free=$(df -BG "$out" | awk 'NR==2 {print $4+0}')
  [ "$free" -lt "$MINFREE" ] && { log "only ${free} GB free: stopping"; break; }
  log "$run_id $dataset seed $seed, $clients clients, $strategy (session ${session:-connection})"
  rm -rf "$run"
  strategy=${strategy//+/,}                                   # "khf:1+2" in the plan = khf:1,2
  excl=""; [ -f "$out/exclude_gateways.txt" ] && excl="--exclude $(tr '\n' ',' < "$out/exclude_gateways.txt" | sed 's/,$//')"   # gateways that refuse connections
  session=${session%$'\r'}; groups=${groups%$'\r'}            # a CRLF plan leaves a \r on the last field
  "$HERE/run_wire.sh" "$run" --seed "$seed" --clients "$clients" --duration "$duration" --drain "$drain" \
      --strategy "$strategy" --groups "$groups" --session "${session:-connection}" $excl > "$run.log" 2>&1 < /dev/null
  if [ ! -f "$run/clients.csv" ]; then log "   -> FAILED (see $run.log)"; sleep 60; continue; fi
  kept=0
  if [ $((n % KEEP)) -eq 0 ]; then kept=1; else rm -f "$run/capture/link.pcap"; fi
  summary=$(python3 - "$run" <<'EOF'
import sys, json, pandas as pd
run = sys.argv[1]
v = json.load(open(f'{run}/verdict.json'))['verdict']
msg = pd.read_csv(f'{run}/messages.csv'); lat = (msg.t_received_ns - msg.t_submit_ns) / 1e6
deliv, p50, p95 = round(100 * msg.t_received_ns.notna().mean(), 1), round(lat.quantile(.5)), round(lat.quantile(.95))
f = pd.read_csv(f'{run}/capture/link.frames.csv.gz'); g = f.groupby(['direction', 'len']).size()
sent, conf, ack = (int(g.get(k, 0)) for k in (('to_gw', 2452), ('from_gw', 70), ('from_gw', 51)))
loss = round(100 * (1 - ack / conf), 2) if conf else ''
try:
    lab = len(pd.read_csv(f'{run}/capture/link.identities.csv'))
except Exception:
    lab = 0
print(f'{v},{deliv},{p50},{p95},{sent},{conf},{ack},{loss},{f.conn.nunique()},{lab}')
EOF
)
  echo "$run_id,$dataset,$seed,$clients,$summary,$kept" >> "$out/manifest.csv"
  log "   -> $(echo "$summary" | cut -d, -f1): delivered $(echo "$summary" | cut -d, -f2)%, loss $(echo "$summary" | cut -d, -f8)%, $(echo "$summary" | cut -d, -f10) labelled connections"
done
log "queue finished"
