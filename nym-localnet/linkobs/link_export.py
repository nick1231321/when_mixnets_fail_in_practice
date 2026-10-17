#!/usr/bin/env python3
"""Link-observer events (pcap_streams.py output) -> MixMatch's six-file export, so that the whole
matching pipeline (their Octave classifier, the pair features, the tree) runs unchanged on
network-level observations.

The observer sees anonymous connections. It classifies each frame as a payload cell or an ack cell
by size (the two dominant frame sizes of the gateway->client direction, exactly as the gateway
exporter does with Sphinx sizes), and in --tls mode splits coalesced TCP segments into cells by
decomposing the segment size into a*payload + b*ack.

Ground truth is attached afterwards and only for labels: each connection is matched to a load-test
client by comparing its client->gateway frame times with that client's send times in the gateway
wire log (fraction of wire events with a frame within +-tol).  The attack itself never reads the
wire log; it is used here to know which connection is whose, so pairs can be formed and scored.

Usage:
  link_export.py --events events.csv --run RUN_DIR --wire-dir WIRE_DIR --out EXPORT_DIR [--tol-ms 5]
"""
import argparse, csv, sys
from collections import defaultdict, Counter
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'mixmatch'))
from export_their_format import write_times, _size_threshold   # noqa: E402  (the gateway exporter's helpers)


def load_events(path):
    ev = defaultdict(lambda: defaultdict(list))      # conn -> direction -> [(t, len)]
    with open(path) as fh:
        for r in csv.DictReader(fh):
            ev[r['conn']][r['direction']].append((int(r['t_ns']) / 1e9, int(r['len'])))
    for c in ev.values():
        for d in c.values():
            d.sort()
    return ev


def split_cells(events, tls, pay_size, ack_size):
    """Return (payload_times, ack_times) for the gateway->client direction.  Plain ws: one frame =
    one message; only frames of exactly the payload or ack size are cells (the gateway also sends
    other frames, e.g. a confirmation for every packet the client sent, which are dropped).
    TLS: a segment of n bytes is a*pay + b*ack (+ slack); emit a payload and b ack cells."""
    pay, ack = [], []
    if not tls:
        for t, ln in events:
            if ln == pay_size:
                pay.append(t)
            elif ln == ack_size:
                ack.append(t)
        return pay, ack
    for t, ln in events:
        best = None
        for a in range(ln // pay_size + 1):
            rest = ln - a * pay_size
            b = round(rest / ack_size)
            slack = abs(rest - b * ack_size)
            if b >= 0 and (best is None or slack < best[0]):
                best = (slack, a, b)
        if best is None:
            continue
        _, a, b = best
        pay += [t] * a; ack += [t] * b
    return pay, ack


def dominant_sizes(ev, tls, n_pair=3000):
    """(payload size, ack size) of the gateway->client direction.  Payload = the most common large
    frame size.  Among the common small sizes, the gateway answers every client send with a
    confirmation, in order, one link round trip later; a delivered ack instead comes back after a
    mixnet round trip with random delays.  So pairing the k-th small frame with the k-th send, the
    lag is nearly constant for confirmations and widely spread for acks: the ack size is the common
    small size with the widest spread (median over clients of the lag's interquartile range).
    (A "within a few ms of a send" test is not enough: on a remote gateway the confirmation round
    trip is tens of ms, and at ~50 sends/s both sizes then look equally tied to the sends.)"""
    sizes = Counter(ln for c in ev.values() for t, ln in c.get('from_gw', []))
    common = [s for s, n in sizes.most_common(8) if n > 0.2 * sizes.most_common(1)[0][1]]
    if len(common) < 2:
        raise SystemExit(f'cannot find the frame sizes (seen {sizes.most_common(6)})')
    pay = max(common)
    small = [s for s in common if s < pay / 4] or sorted(common)[:1]
    if tls or len(small) == 1:
        return pay, small[0]
    up = Counter(ln for c in ev.values() for t, ln in c.get('to_gw', [])).most_common(1)[0][0]
    spread = {}
    for s in small:
        iqrs = []
        for c in list(ev.values())[:20]:
            sent = np.array([t for t, ln in c.get('to_gw', []) if ln == up])
            ts = np.array([t for t, ln in c.get('from_gw', []) if ln == s])
            n = min(len(sent), len(ts), n_pair)
            if n < 50:
                continue
            lag = ts[:n] - sent[:n]
            iqrs.append(np.percentile(lag, 75) - np.percentile(lag, 25))
        spread[s] = float(np.median(iqrs)) if iqrs else 0.0
    ack = max(spread, key=spread.get)
    print('small frame sizes, spread (IQR) of the k-th frame after the k-th send: %s -> ack = %d B'
          % ({k: f'{v * 1e3:.1f} ms' for k, v in spread.items()}, ack), file=sys.stderr)
    return pay, ack


def wire_send_times(wire_dir, tags):
    """client index -> sorted send times (s) from the gateway wire logs, for the run's wire tags."""
    out = defaultdict(list)
    for wf in sorted(Path(wire_dir).glob('wire-*.csv')):
        with wf.open() as f:
            for r in csv.DictReader(f):
                c = tags.get(r['client'])
                if c is not None and r['direction'] == 'to_gw':
                    out[c].append(int(r['wall_ns']) / 1e9)
    return {c: np.sort(v) for c, v in out.items()}


def match_connections(ev, wire, tol):
    """Greedy assignment connection -> client by the fraction of a client's wire send times that
    have a client->gateway frame within tol seconds."""
    scores = []
    for conn, dirs in ev.items():
        ft = np.array([t for t, _ in dirs.get('to_gw', [])])
        if not len(ft):
            continue
        for c, wt in wire.items():
            if abs(len(wt) - len(ft)) > 0.2 * max(len(wt), 1):
                continue
            idx = np.searchsorted(ft, wt)
            lo = np.abs(wt - ft[np.clip(idx - 1, 0, len(ft) - 1)])
            hi = np.abs(wt - ft[np.clip(idx, 0, len(ft) - 1)])
            frac = float((np.minimum(lo, hi) <= tol).mean())
            scores.append((frac, conn, c))
    scores.sort(reverse=True)
    conn_of, used = {}, set()
    for frac, conn, c in scores:
        if conn in conn_of or c in used:
            continue
        if frac < 0.8:
            break
        conn_of[conn], used = c, used | {c}
    return conn_of, {k: v for v, k, _ in [(s[0], s[1], s[2]) for s in scores if s[1] in conn_of and conn_of[s[1]] == s[2]]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--events', required=True); ap.add_argument('--run', required=True)
    ap.add_argument('--wire-dir', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--tol-ms', type=float, default=5.0); ap.add_argument('--tls', action='store_true')
    a = ap.parse_args()
    ev = load_events(a.events)
    pay_size, ack_size = dominant_sizes(ev, a.tls)
    print(f'frame sizes: payload {pay_size} B, ack {ack_size} B', file=sys.stderr)

    # ground truth (labels only): wire tags -> client index -> partner
    tags, partner = {}, {}
    with (Path(a.run) / 'clients.csv').open() as f:
        for r in csv.DictReader(f):
            tags[r['wire_tag']] = int(r['client']); partner[int(r['client'])] = int(r['partner']) if r['partner'] else None
    wire = wire_send_times(a.wire_dir, tags)
    conn_of, frac_of = match_connections(ev, wire, a.tol_ms / 1e3)
    print(f'matched {len(conn_of)} of {len(ev)} connections to {len(tags)} clients '
          f'(median match fraction {np.median(list(frac_of.values())) if frac_of else float("nan"):.3f})', file=sys.stderr)

    # per-client streams from the link view
    streams = {}
    for conn, c in conn_of.items():
        sent = np.array(sorted(t for t, _ in ev[conn].get('to_gw', [])))
        pay, ack = split_cells(ev[conn].get('from_gw', []), a.tls, pay_size, ack_size)
        streams[c] = (sent, np.sort(pay), np.sort(ack))

    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    names = []
    sessions = [(x, y) for x, y in partner.items() if y is not None and partner.get(y) == x and x < y]
    for k, (ai, bi) in enumerate(sessions):
        if ai not in streams or bi not in streams:
            print(f'session {k}: client {ai} or {bi} has no matched connection, skipped', file=sys.stderr)
            continue
        folder = out / f'session_{k:04d}'; folder.mkdir(exist_ok=True)
        a_sent, a_pay, a_ack = streams[ai]; b_sent, b_pay, b_ack = streams[bi]
        write_times(folder / 'payload_initiator_to_gateway.txt', a_sent)
        write_times(folder / 'payload_initiator_from_gateway.txt', a_pay)
        write_times(folder / 'ack_initiator_from_gateway.txt', a_ack)
        write_times(folder / 'payload_responder_to_gateway.txt', b_sent)
        write_times(folder / 'payload_responder_from_gateway.txt', b_pay)
        write_times(folder / 'ack_responder_from_gateway.txt', b_ack)
        names.append(folder.name)
    (out / 'flowpairs.txt').write_text('\n'.join(names) + '\n')
    with (out / 'connections.csv').open('w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['conn', 'client', 'match_fraction', 'frames_to_gw', 'frames_from_gw'])
        for conn, c in sorted(conn_of.items(), key=lambda kv: kv[1]):
            w.writerow([conn, c, f'{frac_of.get(conn, float("nan")):.3f}', len(ev[conn].get('to_gw', [])), len(ev[conn].get('from_gw', []))])
    print(f'exported {len(names)} sessions to {out} (link-observer view)', file=sys.stderr)


if __name__ == '__main__':
    main()
