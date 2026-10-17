#!/usr/bin/env python3
"""Emulate a network observer's view from the gateway wire logs, for sensitivity sweeps before (or
alongside) real captures: what does TCP/TLS on the client<->gateway link do to the Sphinx-level
timing the gateway exporter uses?

Two effects are modelled per client and direction:
  --coalesce-ms W   cells closer than W ms are delivered in one TCP segment: they all take the
                    time of the last one (the segment leaves when the last cell is queued)
  --jitter-ms S     per-event Gaussian timing noise of std S ms (link + capture jitter)
Optionally --loss-pct P drops a fraction of events (an observer that misses segments).

Writes a standard six-file export; run the same scoring as for the gateway view and compare.

Usage: link_emulate.py --run RUN_DIR --wire-dir WIRE_DIR --out EXPORT_DIR [--coalesce-ms 2 --jitter-ms 2 --loss-pct 0 --seed 0]
"""
import argparse, sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'mixmatch'))
from export_their_format import load_sessions, write_times   # noqa: E402


def transform(times, coalesce, jitter, loss, rng):
    t = np.sort(np.asarray(times, float))
    if loss > 0 and len(t):
        t = t[rng.random(len(t)) >= loss]
    if coalesce > 0 and len(t) > 1:
        # group consecutive events closer than the window; the group takes its last time
        gaps = np.diff(t)
        starts = np.r_[0, np.where(gaps > coalesce)[0] + 1]
        ends = np.r_[starts[1:], len(t)]
        t = np.repeat(t[ends - 1], ends - starts)
    if jitter > 0 and len(t):
        t = t + rng.normal(0, jitter, len(t))
    return np.sort(t)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', required=True); ap.add_argument('--wire-dir', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--coalesce-ms', type=float, default=2.0); ap.add_argument('--jitter-ms', type=float, default=2.0)
    ap.add_argument('--loss-pct', type=float, default=0.0); ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    sessions, streams = load_sessions(a.run, a.wire_dir)
    tr = lambda x: transform(x, a.coalesce_ms / 1e3, a.jitter_ms / 1e3, a.loss_pct / 100, rng)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    names = []
    for k, (ai, bi) in enumerate(sessions):
        folder = out / f'session_{k:04d}'; folder.mkdir(exist_ok=True)
        a_sent, a_pay, a_ack = streams[ai]; b_sent, b_pay, b_ack = streams[bi]
        write_times(folder / 'payload_initiator_to_gateway.txt', tr(a_sent))
        write_times(folder / 'payload_initiator_from_gateway.txt', tr(a_pay))
        write_times(folder / 'ack_initiator_from_gateway.txt', tr(a_ack))
        write_times(folder / 'payload_responder_to_gateway.txt', tr(b_sent))
        write_times(folder / 'payload_responder_from_gateway.txt', tr(b_pay))
        write_times(folder / 'ack_responder_from_gateway.txt', tr(b_ack))
        names.append(folder.name)
    (out / 'flowpairs.txt').write_text('\n'.join(names) + '\n')
    print(f'emulated link view (coalesce {a.coalesce_ms} ms, jitter {a.jitter_ms} ms, loss {a.loss_pct}%): '
          f'{len(names)} sessions -> {out}', file=sys.stderr)


if __name__ == '__main__':
    main()
