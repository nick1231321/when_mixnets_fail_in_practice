#!/usr/bin/env python3
"""Client-side wire log -> MixMatch six-file export (the link observer's view of our own clients).

With MIXCORR_WIRE_DIR set in the load-test process, every websocket message a client sends to or
receives from its gateway is logged (wall_ns, direction, client tag, size), the same format and tags as
the gateway-side wire log. That is what a tap on the client's own network link sees. This script splits
each client's received messages into payload and ack cells by size (dropping the gateway's per-send
confirmations, see ../link_export.py) and writes one session folder per communicating pair.

Usage: client_wire_export.py --wire-dir DIR --run RUN_DIR --out EXPORT_DIR
"""
import argparse, csv, sys
from collections import defaultdict
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent.parent / 'mixmatch'))
from link_export import split_cells, dominant_sizes   # noqa: E402
from export_their_format import write_times             # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--wire-dir', required=True); ap.add_argument('--run', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--settle-s', type=float, default=5.0, help='seconds after the last client starts receiving')
    a = ap.parse_args()
    tags, partner = {}, {}
    with (Path(a.run) / 'clients.csv').open() as f:
        for r in csv.DictReader(f):
            tags[r['wire_tag']] = int(r['client']); partner[int(r['client'])] = int(r['partner']) if r['partner'] else None
    ev = defaultdict(lambda: defaultdict(list))   # client index -> direction -> [(t, size)]
    for wf in sorted(Path(a.wire_dir).glob('wire-*.csv')):
        with wf.open() as f:
            for r in csv.DictReader(f):
                c = tags.get(r['client'])
                if c is not None:
                    ev[c][r['direction']].append((int(r['wall_ns']) / 1e9, int(r['size'])))
    if not ev:
        raise SystemExit('no wire events for this run (was MIXCORR_WIRE_DIR set for the load test?)')
    for c in ev.values():
        for d in c.values():
            d.sort()
    pay_size, ack_size = dominant_sizes(ev, False)
    print(f'client-side message sizes: payload {pay_size} B, ack {ack_size} B; sent sizes '
          f'{sorted({ln for c in ev.values() for _, ln in c["to_gw"]})[:5]}', file=sys.stderr)
    streams = {}
    for c, dirs in ev.items():
        sent = np.array(sorted(t for t, _ in dirs.get('to_gw', [])))
        pay, ack = split_cells(dirs.get('from_gw', []), False, pay_size, ack_size)
        streams[c] = (sent, np.sort(pay), np.sort(ack))
    # Crop every stream to the period when ALL clients are fully up. On the live network clients
    # connect minutes apart, and a client can send for a while before anything comes back; those
    # unmatched packets at the start would offset the k-th/k-th pairing by thousands of positions,
    # far beyond the alignment search. Window: 5 s after the last client starts receiving, until the
    # first client stops sending.
    t_from = max(p[0] for _, p, _ in streams.values() if len(p)) + a.settle_s
    t_to = min(s[-1] for s, _, _ in streams.values() if len(s))
    if t_to - t_from < 30:
        raise SystemExit(f'common window too short ({t_to - t_from:.0f} s)')
    crop = lambda x: x[(x >= t_from) & (x <= t_to)]
    streams = {c: tuple(crop(x) for x in v) for c, v in streams.items()}
    print(f'common window: {t_to - t_from:.0f} s (all clients up)', file=sys.stderr)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True); names = []
    sessions = [(x, y) for x, y in partner.items() if y is not None and partner.get(y) == x and x < y]
    for k, (ai, bi) in enumerate(sessions):
        if ai not in streams or bi not in streams:
            print(f'session {k}: client {ai} or {bi} logged nothing, skipped', file=sys.stderr); continue
        folder = out / f'session_{k:04d}'; folder.mkdir(exist_ok=True)
        for role, c in (('initiator', ai), ('responder', bi)):
            s, p, q = streams[c]
            write_times(folder / f'payload_{role}_to_gateway.txt', s)
            write_times(folder / f'payload_{role}_from_gateway.txt', p)
            write_times(folder / f'ack_{role}_from_gateway.txt', q)
        names.append(folder.name)
    (out / 'flowpairs.txt').write_text('\n'.join(names) + '\n')
    print(f'exported {len(names)} sessions to {out} (client-link view)', file=sys.stderr)


if __name__ == '__main__':
    main()
