#!/usr/bin/env python3
"""Localnet flow-matching data pipeline for the MixMatch statistical classifier.

Two steps, no scoring here -- matching is done by MixMatch's own Octave code (PoPETs 2024.2),
run on the exported data:

  run     launch one load test per condition (strategy x mixing delay) with the nym-load-test
          generator; the gateways log every client<->gateway Sphinx packet (timestamp+size, cover
          included) to data-netem/wire/wire-*.csv. Each run writes clients.csv (wire_tag -> client,
          partner) + run.json, then is exported to MixMatch's format automatically.

  export  convert one run's gateway wire logs + clients.csv into MixMatch's statistical-classifier
          input: a folder per communicating pair (A=initiator, B=responder) with the six timestamp
          files their perform_experiment_real_data_alt_delay_characteristic.m loads (ns, one/line):
              payload_initiator_to_gateway.txt     A -> gateway  (ingress, payload-size)
              payload_initiator_from_gateway.txt   gateway -> A  (egress payload)
              ack_initiator_from_gateway.txt       gateway -> A  (egress ack)
              payload_responder_to_gateway.txt     B -> gateway
              payload_responder_from_gateway.txt   gateway -> B  (egress payload)
              ack_responder_from_gateway.txt       gateway -> B  (egress ack)
          plus flowpairs.txt listing the session folders.

Then score with their classifier, e.g.:
  octave-cli --eval "run_their_matching('<export_dir>', 0.15)"   # seed = 3 x mixing_ms (seconds)

Usage:
  export_their_format.py export RUN_DIR OUT_DIR [--wire-dir DIR]
  export_their_format.py run --out out/cmp --strategies baseline khf:1,2 --mixing 50 \\
      --pairs 15 --duration 180 [--export-root EXPORT_DIR]
"""
import argparse
import csv
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
LOCALNET = HERE.parent
BINARY = LOCALNET / "self-test" / "target" / "release" / "nym-load-test"


# --------- load wire-level streams per session from the gateway wire logs + clients.csv ---------
def load_sessions(run_dir, wire_dir):
    """Return (sessions, streams): sessions = list of (A, B) client-index pairs; streams[c] =
    (sent_times, recv_payload_times, recv_ack_times) in seconds. Received packets are split into
    payload vs ack by size, as MixMatch splits gateway->client packets."""
    run_dir = Path(run_dir)
    # this run's clients: wire_tag -> client index, and its partner. The wire tag matches the tag
    # the gateway logs (both hash the client's identity key). Clients are ephemeral, so a run's
    # tags are unique and isolate its packets in the shared, accumulating gateway wire log.
    idx_of_tag, partner = {}, {}
    with (run_dir / "clients.csv").open() as f:
        for r in csv.DictReader(f):
            idx_of_tag[r["wire_tag"]] = int(r["client"])
            partner[int(r["client"])] = int(r["partner"]) if r["partner"] else None

    sent, recv = defaultdict(list), defaultdict(list)  # client -> [t]; recv -> [(t, size)]
    wire_files = sorted(Path(wire_dir).glob("wire-*.csv")) if wire_dir else []
    if not wire_files:
        raise SystemExit(f"no gateway wire logs (wire-*.csv) in {wire_dir}; is MIXCORR_WIRE_DIR set on the gateways?")
    for wf in wire_files:
        with wf.open() as f:
            for r in csv.DictReader(f):
                c = idx_of_tag.get(r["client"])
                if c is None:
                    continue
                t = int(r["wall_ns"]) / 1e9  # seconds
                if r["direction"] == "to_gw":
                    sent[c].append(t)
                else:
                    recv[c].append((t, int(r["size"])))

    all_sizes = [sz for v in recv.values() for _, sz in v]
    threshold = _size_threshold(all_sizes)
    recv_data, recv_ack = defaultdict(list), defaultdict(list)
    for c, pkts in recv.items():
        for t, sz in pkts:
            (recv_data if sz >= threshold else recv_ack)[c].append(t)

    sessions = []
    for a, b in partner.items():
        if b is not None and partner.get(b) == a and a < b:
            sessions.append((a, b))
    streams = {}
    for c in idx_of_tag.values():
        streams[c] = (np.sort(sent[c]), np.sort(recv_data[c]), np.sort(recv_ack[c]))
    return sessions, streams


def _size_threshold(sizes):
    """Midpoint between the two most common packet sizes (payload vs ack). Falls back to a split
    that leaves everything as payload."""
    if not sizes:
        return 0
    common = [s for s, _ in Counter(sizes).most_common(2)]
    return (min(common) + max(common)) / 2 if len(common) == 2 else min(sizes) - 1


# --------- export one run to MixMatch's statistical-classifier input format ---------
def write_times(path, seconds):
    # their parser loads these and multiplies by 1e-9, so write nanoseconds
    ns = (np.asarray(seconds) * 1e9).astype(np.int64)
    path.write_text("\n".join(str(int(x)) for x in ns) + ("\n" if len(ns) else ""))


def export_run(run_dir, out_dir, wire_dir):
    sessions, streams = load_sessions(run_dir, wire_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for k, (ai, bi) in enumerate(sessions):
        folder = out / f"session_{k:04d}"
        folder.mkdir(exist_ok=True)
        a_sent, a_data, a_ack = streams[ai]
        b_sent, b_data, b_ack = streams[bi]
        write_times(folder / "payload_initiator_to_gateway.txt", a_sent)
        write_times(folder / "payload_initiator_from_gateway.txt", a_data)
        write_times(folder / "ack_initiator_from_gateway.txt", a_ack)
        write_times(folder / "payload_responder_to_gateway.txt", b_sent)
        write_times(folder / "payload_responder_from_gateway.txt", b_data)
        write_times(folder / "ack_responder_from_gateway.txt", b_ack)
        names.append(folder.name)
    (out / "flowpairs.txt").write_text("\n".join(names) + "\n")
    print(f"exported {len(names)} sessions to {out} (MixMatch statistical-classifier format)")
    return len(names)


def cmd_export(a):
    export_run(a.run_dir, a.out_dir, a.wire_dir or str(LOCALNET / "data-netem" / "wire"))


# --------- running conditions (the gateways log the wire automatically) ---------
def cmd_run(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for mixing in a.mixing:
        for strat in a.strategies:
            tag = f"{strat.replace(':', '-').replace(',', '.')}_mix{mixing}"
            run_dir = out / tag
            if not (run_dir / "clients.csv").exists() or a.rerun:
                run_dir.mkdir(parents=True, exist_ok=True)
                cfg = run_dir / "config.toml"
                cfg.write_text(f"""topology = "{Path(a.topology).resolve()}"
output_dir = "{run_dir.resolve()}"
duration_s = {a.duration}
warmup_s = {a.warmup}
drain_s = {a.drain}
message_size = {a.message_bytes}
messages_per_client_per_min = {a.rate_per_min}
pairing = "random-pairs"
seed = {a.seed}
[client]
loop_cover_traffic = true
poisson_sending = true
average_packet_delay_ms = {mixing}
ack_wait_addition_s = 10
[[group]]
name = "{tag}"
count = {2 * a.pairs}
strategy = "{strat}"
""")
                print(f"[{tag}] running {2*a.pairs} clients for {a.duration}s ...", flush=True)
                proc = subprocess.run([str(BINARY), str(cfg)], capture_output=True, text=True)
                (run_dir / "load_test.log").write_text(proc.stdout + proc.stderr)
                if not (run_dir / "clients.csv").exists():
                    print(f"[{tag}] FAILED")
                    continue
            export_dir = Path(a.export_root) / tag if a.export_root else run_dir / "mixmatch_export"
            n = export_run(run_dir, export_dir, a.wire_dir)
            seed = 3 * mixing / 1000.0
            print(f"[{tag}] score with: octave-cli --eval \"run_their_matching('{export_dir}', {seed})\"", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="convert one run to MixMatch's input format")
    e.add_argument("run_dir")
    e.add_argument("out_dir")
    e.add_argument("--wire-dir", default=None)
    e.set_defaults(func=cmd_export)

    r = sub.add_parser("run", help="launch load tests and export them")
    r.add_argument("--topology", default=str(LOCALNET / "data-netem" / "network.json"))
    r.add_argument("--out", required=True)
    r.add_argument("--strategies", nargs="+", default=["baseline", "khf:1,2"])
    r.add_argument("--mixing", nargs="+", type=int, default=[50])
    r.add_argument("--pairs", type=int, default=15)
    r.add_argument("--duration", type=int, default=180)
    r.add_argument("--warmup", type=int, default=5)
    r.add_argument("--drain", type=int, default=30)
    r.add_argument("--message-bytes", type=int, default=50_000)
    r.add_argument("--rate-per-min", type=float, default=6.0)
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--wire-dir", default=str(LOCALNET / "data-netem" / "wire"),
                   help="where the gateways write their wire logs (/localnet/wire -> data-netem/wire)")
    r.add_argument("--export-root", default=None,
                   help="export each run under here (default: <run_dir>/mixmatch_export)")
    r.add_argument("--rerun", action="store_true")
    r.set_defaults(func=cmd_run)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
