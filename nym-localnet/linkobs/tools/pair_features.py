#!/usr/bin/env python3
"""Pair-level (flow matching) features from MixMatch six-file exports, gateway-observable only.

One row = one candidate pair (initiator I, responder R) of the same run x one window of L merged
observations. Merged flows exactly as MixMatch: data_tx = I.sent U R.sent, data_rx = I.recv_pay U
R.recv_pay, ack_tx = data_rx, ack_rx = I.recv_ack U R.recv_ack. Label = 1 iff I and R are partners.

Feature blocks (each for data and ack streams):
  b1_*   drift stats at MixMatch's fixed-seed alignment (their align_sequences, seed = 0.15 s)
  b2_*   drift stats at the variance-minimising alignment (seed-free)
  b3_*   alignment scalars (best shift, mean delay there, min variance, sharpness, seed/best var ratio)
  b4_*   count balance: N_sent(t) - N_recv(t + eta) over the window's time span
  b6_*   per-endpoint profile features (loop/ack echo kernel centre & width, r_in) joined from the
         client profile table, plus |centre_I - centre_R|
  b7_*   raw data-stream drift at best alignment, downsampled to 25 values

Usage: pair_features.py --profiles profile_features.csv --out pairs.csv NAME=EXPORT:RUN ...
"""
import argparse, csv, sys
from pathlib import Path
import numpy as np
import pandas as pd
from profile_features import FILES, load_run_labels

SEED_DELAY = 0.15          # MixMatch driver's average_mixnet_delay for 50 ms/hop, s
N_ALIGN = 3000             # observations used for the variance-minimising search
SHIFT_RANGE = 200          # +- packets searched
LENGTHS = {100: 10, 500: 3}  # window length -> max number of non-overlapping windows per pair
LAGS_DS = 25


# ----------------------------------------------------------------------------- alignment
def _pairs_at(s1, s2, d):
    """Aligned (s1, s2) slices at integer shift d (s2 index = s1 index + d)."""
    if d >= 0:
        n = min(len(s1), len(s2) - d)
        return s1[:n], s2[d:d + n]
    n = min(len(s1) + d, len(s2))
    return s1[-d:-d + n], s2[:n]


def align_fixed_seed(s1, s2, avg_delay):
    """Faithful port of MixMatch's align_sequences.m (binary search on the shift until the mean
    difference first reaches avg_delay, then step up)."""
    d, gap = 0, 64
    md = lambda dd: float(np.mean(_pairs_at(s1, s2, dd)[1] - _pairs_at(s1, s2, dd)[0]))
    while gap >= 1:
        d = d + gap if md(d) < avg_delay else d - gap
        gap //= 2
    while md(d) < avg_delay:
        d += 1
    return _pairs_at(s1, s2, d), d


def align_min_var(s1, s2):
    """Shift minimising the drift variance over the first N_ALIGN observations."""
    a, b = s1[:N_ALIGN + SHIFT_RANGE], s2[:N_ALIGN + 2 * SHIFT_RANGE]
    best, bestd, var = None, 0, {}
    for d in range(-SHIFT_RANGE, SHIFT_RANGE + 1):
        x, y = _pairs_at(a, b, d)
        if len(x) < 200:
            continue
        v = float(np.var(y - x))
        var[d] = v
        if best is None or v < best:
            best, bestd = v, d
    ds = np.array(sorted(var)); vs = np.array([var[k] for k in ds])
    # sharpness: curvature of var(d) around the minimum (relative), second-best gap
    i = int(np.where(ds == bestd)[0][0])
    curv = (vs[max(i - 5, 0)] + vs[min(i + 5, len(vs) - 1)] - 2 * vs[i]) / (vs[i] + 1e-12)
    far = vs[np.abs(ds - bestd) > 20]
    gap2 = float((far.min() - vs[i]) / (vs[i] + 1e-12)) if len(far) else np.nan
    return _pairs_at(s1, s2, bestd), bestd, best, float(curv), gap2, float(var.get(0, np.nan))


def drift_stats(x, y, prefix):
    d = y - x
    k = np.arange(len(d))
    f = {f'{prefix}_mean': d.mean(), f'{prefix}_std': d.std(), f'{prefix}_mad': np.median(np.abs(d - np.median(d))),
         f'{prefix}_min': d.min(), f'{prefix}_max': d.max()}
    for q in (5, 25, 50, 75, 95):
        f[f'{prefix}_p{q}'] = np.percentile(d, q)
    f[f'{prefix}_slope'] = np.polyfit(k, d, 1)[0] if len(d) > 2 else np.nan
    dc = d - d.mean()
    f[f'{prefix}_ac1'] = float(np.dot(dc[:-1], dc[1:]) / (np.dot(dc, dc) + 1e-12))
    f[f'{prefix}_range'] = d.max() - d.min()
    return f


def count_balance(sent, recv, t_a, t_b, eta, prefix):
    ts = np.linspace(t_a, t_b, 20)
    c = np.searchsorted(sent, ts) - np.searchsorted(recv, ts + eta)
    c = c - c[0]
    return {f'{prefix}_range': float(c.max() - c.min()), f'{prefix}_std': float(c.std()),
            f'{prefix}_final': float(c[-1]), f'{prefix}_maxabs': float(np.abs(c).max()),
            f'{prefix}_slope': float(np.polyfit(ts - t_a, c, 1)[0])}


# ----------------------------------------------------------------------------- per pair
def pair_rows(name, I, R, label, prof_I, prof_R):
    data_tx = np.sort(np.concatenate([I['sent'], R['sent']]))
    data_rx = np.sort(np.concatenate([I['pay'], R['pay']]))
    ack_tx = data_rx
    ack_rx = np.sort(np.concatenate([I['ack'], R['ack']]))
    streams = {'data': (data_tx, data_rx), 'ack': (ack_tx, ack_rx)}
    al = {}
    for sn, (tx, rx) in streams.items():
        (fx, fy), d_seed = align_fixed_seed(tx, rx, SEED_DELAY)
        (bx, by), d_best, vmin, curv, gap2, v0 = align_min_var(tx, rx)
        al[sn] = dict(fx=fx, fy=fy, bx=bx, by=by, d_seed=d_seed, d_best=d_best, vmin=vmin, curv=curv, gap2=gap2, v0=v0,
                      tx=tx, rx=rx)
    b6 = {}
    for tag, p in (('I', prof_I), ('R', prof_R)):
        for k in ('firloop_centre', 'firloop_width', 'firack_centre', 'firack_width', 'r_in', 'loop_centre'):
            b6[f'b6_{tag}_{k}'] = p.get(k, np.nan)
    b6['b6_dcentre'] = abs(b6['b6_I_firloop_centre'] - b6['b6_R_firloop_centre'])
    b6['b6_dack'] = abs(b6['b6_I_firack_centre'] - b6['b6_R_firack_centre'])
    rows = []
    for L, nmax in LENGTHS.items():
        nwin = min(nmax, min(len(al['data']['bx']), len(al['ack']['bx']), len(al['data']['fx']), len(al['ack']['fx'])) // L)
        for w in range(nwin):
            sl = slice(w * L, (w + 1) * L)
            f = {'dataset': name, 'I': I['idx'], 'R': R['idx'], 'label': label, 'L': L, 'win': w}
            for sn in ('data', 'ack'):
                a = al[sn]
                f.update(drift_stats(a['fx'][sl], a['fy'][sl], f'b1_{sn}'))
                f.update(drift_stats(a['bx'][sl], a['by'][sl], f'b2_{sn}'))
                eta = float(np.mean(a['by'][sl] - a['bx'][sl]))
                f.update({f'b3_{sn}_dbest': a['d_best'], f'b3_{sn}_dseed': a['d_seed'], f'b3_{sn}_eta_best': eta,
                          f'b3_{sn}_vmin': a['vmin'], f'b3_{sn}_curv': a['curv'], f'b3_{sn}_gap2': a['gap2'],
                          f'b3_{sn}_v0_over_vmin': a['v0'] / (a['vmin'] + 1e-12),
                          f'b3_{sn}_win_var': float(np.var(a['by'][sl] - a['bx'][sl]))})
                t_a, t_b = float(a['bx'][sl][0]), float(a['bx'][sl][-1])
                f.update(count_balance(a['tx'], a['rx'], t_a, t_b, eta, f'b4_{sn}'))
            f.update(b6)
            d = al['data']['by'][sl] - al['data']['bx'][sl]
            ds = np.interp(np.linspace(0, len(d) - 1, LAGS_DS), np.arange(len(d)), d)
            f.update({f'b7_{i:02d}': float(v) for i, v in enumerate(ds)})
            rows.append(f)
    # full-length window (all aligned observations)
    f = {'dataset': name, 'I': I['idx'], 'R': R['idx'], 'label': label, 'L': 0, 'win': 0}
    for sn in ('data', 'ack'):
        a = al[sn]
        n = min(len(a['fx']), len(a['bx']))
        f.update(drift_stats(a['fx'][:n], a['fy'][:n], f'b1_{sn}'))
        f.update(drift_stats(a['bx'][:n], a['by'][:n], f'b2_{sn}'))
        eta = float(np.mean(a['by'][:n] - a['bx'][:n]))
        f.update({f'b3_{sn}_dbest': a['d_best'], f'b3_{sn}_dseed': a['d_seed'], f'b3_{sn}_eta_best': eta,
                  f'b3_{sn}_vmin': a['vmin'], f'b3_{sn}_curv': a['curv'], f'b3_{sn}_gap2': a['gap2'],
                  f'b3_{sn}_v0_over_vmin': a['v0'] / (a['vmin'] + 1e-12), f'b3_{sn}_win_var': float(np.var(a['by'][:n] - a['bx'][:n]))})
        f.update(count_balance(a['tx'], a['rx'], float(a['bx'][0]), float(a['bx'][n - 1]), eta, f'b4_{sn}'))
    f.update(b6)
    d = al['data']['by'][:n] - al['data']['bx'][:n]
    ds = np.interp(np.linspace(0, len(d) - 1, LAGS_DS), np.arange(len(d)), d)
    f.update({f'b7_{i:02d}': float(v) for i, v in enumerate(ds)})
    rows.append(f)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('specs', nargs='+')
    ap.add_argument('--profiles', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    prof = pd.read_csv(a.profiles, usecols=['dataset', 'client', 'window_s', 'firloop_centre', 'firloop_width',
                                             'firack_centre', 'firack_width', 'r_in', 'loop_centre'])
    prof = prof[prof['window_s'] == 240].set_index(['dataset', 'client'])
    all_rows = []
    for spec in a.specs:
        name, rest = spec.split('=', 1)
        exp_dir, run_dir = rest.split(':', 1)
        exp_dir = Path(exp_dir)
        clients, sessions = load_run_labels(run_dir)
        names = exp_dir.joinpath('flowpairs.txt').read_text().split()
        inits, resps = [], []
        for k, sess in enumerate(names):
            d = exp_dir / sess
            st = {key: np.sort(np.loadtxt(d / fn, dtype=np.float64, ndmin=1) / 1e9) for key, fn in FILES.items()}
            ai, bi = sessions[k]
            inits.append({'idx': ai, 'sent': st['initiator_to'], 'pay': st['initiator_from'], 'ack': st['initiator_ack'], 'sess': k})
            resps.append({'idx': bi, 'sent': st['responder_to'], 'pay': st['responder_from'], 'ack': st['responder_ack'], 'sess': k})
        getp = lambda c: prof.loc[(name, c)].to_dict() if (name, c) in prof.index else {}
        for I in inits:
            for R in resps:
                all_rows += pair_rows(name, I, R, int(I['sess'] == R['sess']), getp(I['idx']), getp(R['idx']))
        print(f'[{name}] {len(inits)}x{len(resps)} pairs -> {len(all_rows)} rows so far', file=sys.stderr)
    pd.DataFrame(all_rows).to_csv(a.out, index=False)
    print(f'wrote {len(all_rows)} rows to {a.out}')


if __name__ == '__main__':
    main()
