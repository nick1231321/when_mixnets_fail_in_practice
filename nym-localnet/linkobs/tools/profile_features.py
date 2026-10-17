#!/usr/bin/env python3
"""Gateway-observable per-client profile features from MixMatch six-file exports.

Features are computed ONLY from the six timestamp files of a session (what the client's own gateway
logs). Labels (mixing delay, cover/sending intervals, real traffic) are joined afterwards from the
run metadata (run.json, clients.csv, messages.csv) by client index, using the exporter's pair order:
session_k = k-th mutual pair (a, b) with a < b, walking clients.csv in client order; initiator = a.

Usage:
  profile_features.py --out features.csv NAME=EXPORT_DIR:RUN_DIR [NAME=EXPORT_DIR:RUN_DIR ...]
"""
import argparse, csv, json, sys
from pathlib import Path
import numpy as np

FILES = {
    'initiator_to': 'payload_initiator_to_gateway.txt', 'initiator_from': 'payload_initiator_from_gateway.txt',
    'initiator_ack': 'ack_initiator_from_gateway.txt', 'responder_to': 'payload_responder_to_gateway.txt',
    'responder_from': 'payload_responder_from_gateway.txt', 'responder_ack': 'ack_responder_from_gateway.txt',
}
BIN = 0.010                 # histogram bin, s
L_LOOP, L_ACK = 1.5, 3.0    # max lag, s
NL_LOOP, NL_ACK = int(L_LOOP / BIN), int(L_ACK / BIN)
WINDOWS = [10, 30, 60, 240]  # s; the last one = whole session
SUB_T = 5.0                 # sub-window for time-resolved loop fraction
FRAG_PAYLOAD = 2048         # bytes of application data per regular packet (approx.)


# ----------------------------------------------------------------------------- histogram (CCF)
LAG_MIN = 0.03   # s; lags below this hold an instrumentation coincidence spike, not mixnet echoes


def ccf(sent, arr, t0, T, nl):
    """Two-sided histogram of (arrival - send) for sends in [t0, t0+T): returns (H_pos, H_neg) with
    H_pos[k] = pairs at lag +k bins (k = 0..nl-1) and H_neg[k] = pairs at lag -k bins. Echoes only
    exist at positive lags, while slow co-modulation of the send and receive rates is symmetric in
    lag, so the negative side is a bump-free estimate of the floor. Computed as the cross-correlation
    of the binned count series; arrivals are taken from [t0 - L, t0 + T + L]."""
    nb = int(round(T / BIN))
    x = np.histogram(sent, bins=nb, range=(t0, t0 + nb * BIN))[0].astype(float)
    y = np.histogram(arr, bins=nb + 2 * nl, range=(t0 - nl * BIN, t0 + (nb + nl) * BIN))[0].astype(float)
    H = np.correlate(y, x, mode='valid')              # [j] = sum_n y[n+j] x[n], lag = (j - nl) bins
    return H[nl:2 * nl], H[nl::-1][:nl]


def bump(H2, n_sent):
    """Floor-subtracted, per-send-normalised histogram and its summary (area, centre, width...).
    Floor = smoothed mirror (negative-lag) histogram. Moments are taken over the bump's support
    (around the peak, above 10% of peak) so that noise at far lags does not inflate the width."""
    H, Hneg = H2
    nl = len(H)
    floor = np.convolve(np.pad(Hneg, 5, mode='edge'), np.ones(11) / 11, mode='valid')  # ~100 ms smoothing of the mirror
    c = (H - floor) / max(n_sent, 1)
    c[:int(LAG_MIN / BIN)] = 0
    floor = float(floor.mean())
    tau = (np.arange(nl) + 0.5) * BIN
    sm = np.convolve(np.clip(c, 0, None), np.ones(5) / 5, mode='same')
    pk = int(np.argmax(sm))
    thr = 0.1 * sm[pk]
    lo, hi = pk, pk
    while lo > 0 and sm[lo - 1] > thr:
        lo -= 1
    while hi < nl - 1 and sm[hi + 1] > thr:
        hi += 1
    cp = np.clip(c[lo:hi + 1], 0, None)
    tot = cp.sum()
    out = {'area': float(c.sum()), 'area_support': float(tot), 'floor_per_send': float(floor / max(n_sent, 1)),
           'mode': float(tau[pk]), 'support_lo': float(tau[lo]), 'support_hi': float(tau[hi])}
    if tot > 0:
        t = tau[lo:hi + 1]
        centre = float((t * cp).sum() / tot)
        out['centre'] = centre
        out['width'] = float(np.sqrt(((t - centre) ** 2 * cp).sum() / tot))
        cdf = np.cumsum(cp) / tot
        for q in (10, 50, 90):
            out[f'q{q}'] = float(t[min(np.searchsorted(cdf, q / 100), len(t) - 1)])
    else:
        for k in ('centre', 'width', 'q10', 'q50', 'q90'):
            out[k] = np.nan
    return c, out


def fir_kernel(sent, arr, t0, T, nl, ridge=1e-3):
    """Least-squares echo kernel: fit y(t) = c + sum_k b_k x(t-k) with x = binned sends, y = binned
    arrivals; b_k = echoes per send at lag k. Unlike the raw cross-histogram this stays unbiased when
    the send process is autocorrelated (the receive stream is mostly a delayed copy of the send
    stream, so any slow structure in sends leaks into the histogram floor asymmetrically)."""
    from scipy.linalg import solve_toeplitz
    nb = int(round(T / BIN))
    x = np.histogram(sent, bins=nb + nl, range=(t0 - nl * BIN, t0 + nb * BIN))[0].astype(float)
    y = np.histogram(arr, bins=nb, range=(t0, t0 + nb * BIN))[0].astype(float)
    xc, yc = x - x.mean(), y - y.mean()
    xs = xc[nl:]
    r = np.array([np.dot(yc, xc[nl - k:nl - k + nb]) for k in range(nl)])
    R = np.array([np.dot(xs, xc[nl - j:nl - j + nb]) for j in range(nl)])
    R[0] += ridge * R[0]
    b = solve_toeplitz(R, r)
    b[:int(LAG_MIN / BIN)] = 0
    return b


def kernel_stats(c):
    """Area / centre / width / quantiles of a (floor-free) echo kernel over its support."""
    nl = len(c)
    tau = (np.arange(nl) + 0.5) * BIN
    cp = np.clip(c, 0, None)
    sm = np.convolve(cp, np.ones(5) / 5, mode='same')
    pk = int(np.argmax(sm))
    thr = 0.1 * sm[pk]
    lo, hi = pk, pk
    while lo > 0 and sm[lo - 1] > thr:
        lo -= 1
    while hi < nl - 1 and sm[hi + 1] > thr:
        hi += 1
    seg, t = cp[lo:hi + 1], tau[lo:hi + 1]
    tot = seg.sum()
    out = {'area': float(c.sum()), 'area_support': float(tot), 'mode': float(tau[pk]),
           'support_lo': float(tau[lo]), 'support_hi': float(tau[hi])}
    if tot > 0:
        cen = float((t * seg).sum() / tot)
        out['centre'] = cen
        out['width'] = float(np.sqrt(((t - cen) ** 2 * seg).sum() / tot))
        cdf = np.cumsum(seg) / tot
        for q in (10, 50, 90):
            out[f'q{q}'] = float(t[min(np.searchsorted(cdf, q / 100), len(t) - 1)])
    else:
        for k in ('centre', 'width', 'q10', 'q50', 'q90'):
            out[k] = np.nan
    return out


def downsample(v, f=2):
    n = len(v) // f * f
    return v[:n].reshape(-1, f).sum(1)


def iat_stats(t, prefix):
    d = np.diff(t)
    if len(d) < 5:
        return {f'{prefix}_mean': np.nan, f'{prefix}_cv': np.nan, f'{prefix}_p50': np.nan, f'{prefix}_p95': np.nan}
    return {f'{prefix}_mean': float(d.mean()), f'{prefix}_cv': float(d.std() / d.mean()),
            f'{prefix}_p50': float(np.percentile(d, 50)), f'{prefix}_p95': float(np.percentile(d, 95))}


def window_features(sent, pay, ack, t0, T):
    w = lambda a: a[(a >= t0) & (a < t0 + T)]
    s, p, k = w(sent), w(pay), w(ack)
    n = len(s)
    f = {'n_sent': n, 'n_pay': len(p), 'n_ack': len(k),
         'r_in': n / T, 'r_out_pay': len(p) / T, 'r_out_ack': len(k) / T,
         'loss': 1 - len(k) / max(n, 1), 'pay_ack_ratio': len(p) / max(len(k), 1), 'net_flow': (len(p) - n) / T}
    f.update(iat_stats(s, 'iat_in'))
    f.update(iat_stats(p, 'iat_out'))
    # burstiness of received payloads (Fano factor of 100 ms counts)
    cnt = np.histogram(p, bins=int(T / 0.1), range=(t0, t0 + T))[0]
    f['fano_out'] = float(cnt.var() / cnt.mean()) if cnt.mean() > 0 else np.nan
    # loop echo and ack echo histograms (arrivals may extend past the window by the max lag)
    Hl = ccf(s, pay, t0, T, NL_LOOP)
    Ha = ccf(s, ack, t0, T, NL_ACK)
    cl, bl = bump(Hl, n)
    ca, ba = bump(Ha, n)
    f.update({f'loop_{k}': v for k, v in bl.items()})
    f.update({f'ack_{k}': v for k, v in ba.items()})
    # least-squares echo kernels (unbiased under autocorrelated sends) + their summaries
    kl = fir_kernel(s, pay, t0, T, NL_LOOP)
    ka = fir_kernel(s, ack, t0, T, NL_ACK)
    sl, sa = kernel_stats(kl), kernel_stats(ka)
    f.update({f'firloop_{k}': v for k, v in sl.items()})
    f.update({f'firack_{k}': v for k, v in sa.items()})
    # derived closed-form estimates (from the kernel fit)
    f['p_hat'] = sl['area']
    f['loop_rate_hat'] = f['r_in'] * sl['area']
    f['real_sent_hat'] = f['r_in'] * (1 - sl['area'])
    f['real_recv_hat'] = f['r_out_pay'] - f['r_in'] * sl['area']
    f['mu_hat_loop_width'] = sl['width'] / np.sqrt(3)
    f['mu_hat_ack_width'] = sa['width'] / np.sqrt(6)
    # time-resolved loop fraction over 5 s sub-windows
    if T >= 6 * SUB_T:
        ps = []
        for j in range(int(T / SUB_T)):
            ss = w(sent); ss = ss[(ss >= t0 + j * SUB_T) & (ss < t0 + (j + 1) * SUB_T)]
            if len(ss) < 20:
                continue
            ps.append(float(fir_kernel(ss, pay, t0 + j * SUB_T, SUB_T, NL_LOOP, ridge=1e-2).sum()))
        ps = np.array(ps)
        f.update({'p_t_min': float(ps.min()), 'p_t_mean': float(ps.mean()), 'p_t_std': float(ps.std()),
                  'p_t_frac_low': float((ps < 0.9).mean())} if len(ps) else
                 {'p_t_min': np.nan, 'p_t_mean': np.nan, 'p_t_std': np.nan, 'p_t_frac_low': np.nan})
    else:
        f.update({'p_t_min': np.nan, 'p_t_mean': np.nan, 'p_t_std': np.nan, 'p_t_frac_low': np.nan})
    # vectors stored at 20 ms resolution
    vec = {**{f'ccf_loop_{i:03d}': float(v) for i, v in enumerate(downsample(cl))},
           **{f'ccf_ack_{i:03d}': float(v) for i, v in enumerate(downsample(ca))},
           **{f'fir_loop_{i:03d}': float(v) for i, v in enumerate(downsample(kl))},
           **{f'fir_ack_{i:03d}': float(v) for i, v in enumerate(downsample(ka))}}
    return f, vec


# ----------------------------------------------------------------------------- labels
def load_run_labels(run_dir):
    run_dir = Path(run_dir)
    cfg = json.loads((run_dir / 'run.json').read_text())['config']
    base = cfg.get('client', {})
    groups = {g['name']: g for g in cfg.get('groups', [])}
    clients = {}
    with (run_dir / 'clients.csv').open() as fh:
        for r in csv.DictReader(fh):
            c = int(r['client'])
            g = groups.get(r['group'], {})
            pick = lambda k: g.get(k) if g.get(k) is not None else base.get(k)
            clients[c] = {
                'group': r['group'], 'strategy': r['strategy'],
                'partner': int(r['partner']) if r['partner'] else None,
                'mu_d_ms': pick('average_packet_delay_ms'),
                'send_interval_ms': pick('message_sending_average_delay_ms'),
                'loop_interval_ms': pick('loop_cover_traffic_average_delay_ms'),
                'message_size': pick('message_size') or cfg.get('message_size'),
                'msgs_per_min': pick('messages_per_client_per_min') or cfg.get('messages_per_client_per_min'),
            }
    # actual real traffic per client over the run (bytes/s), from the load-test message log
    sent_b, recv_b = {}, {}
    tmin, tmax = None, None
    mpath = run_dir / 'messages.csv'
    if mpath.exists():
        with mpath.open() as fh:
            for r in csv.DictReader(fh):
                s, d, sz = int(r['sender']), int(r['receiver']), int(r['size'])
                sent_b[s] = sent_b.get(s, 0) + sz
                recv_b[d] = recv_b.get(d, 0) + sz
                t = int(r['t_submit_ns'])
                tmin = t if tmin is None else min(tmin, t)
                tmax = t if tmax is None else max(tmax, t)
    dur = cfg.get('duration_s') or ((tmax - tmin) / 1e9 if tmin else None)
    for c in clients:
        clients[c]['real_sent_pps'] = sent_b.get(c, 0) / FRAG_PAYLOAD / dur if dur else np.nan
        clients[c]['real_recv_pps'] = recv_b.get(c, 0) / FRAG_PAYLOAD / dur if dur else np.nan
    # the exporter's session order: mutual pairs (a, b), a < b, in client order
    sessions = [(a, v['partner']) for a, v in sorted(clients.items())
                if v['partner'] is not None and clients.get(v['partner'], {}).get('partner') == a and a < v['partner']]
    return clients, sessions


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('specs', nargs='+', help='NAME=EXPORT_DIR:RUN_DIR')
    ap.add_argument('--out', required=True)
    ap.add_argument('--windows', default=','.join(map(str, WINDOWS)))
    a = ap.parse_args()
    windows = [int(x) for x in a.windows.split(',')]
    rows, vecs = [], []
    for spec in a.specs:
        name, rest = spec.split('=', 1)
        exp_dir, run_dir = rest.split(':', 1)
        exp_dir = Path(exp_dir)
        clients, sessions = load_run_labels(run_dir)
        names = exp_dir.joinpath('flowpairs.txt').read_text().split()
        if len(names) != len(sessions):
            print(f'[{name}] WARNING: {len(names)} exported sessions vs {len(sessions)} mutual pairs in clients.csv',
                  file=sys.stderr)
        for k, sess in enumerate(names):
            d = exp_dir / sess
            st = {key: np.sort(np.loadtxt(d / fn, dtype=np.float64, ndmin=1) / 1e9) for key, fn in FILES.items()}
            t_start = min(v.min() for v in st.values() if len(v))
            t_end = max(v.max() for v in st.values() if len(v))
            pair = sessions[k] if k < len(sessions) else (None, None)
            for role, cidx in (('initiator', pair[0]), ('responder', pair[1])):
                sent, pay, ack = st[f'{role}_to'], st[f'{role}_from'], st[f'{role}_ack']
                lab = clients.get(cidx, {}) if cidx is not None else {}
                # keep send windows L_ACK away from both session edges, so that arrivals exist on
                # both sides of every lag (the mirrored floor needs the negative-lag side too)
                lo_t, hi_t = t_start + L_ACK, t_end - L_ACK
                for T in windows:
                    full = T >= (hi_t - lo_t)
                    Tw = (hi_t - lo_t) if full else T
                    starts = [lo_t] if full else np.arange(lo_t, hi_t - T, T)
                    for t0 in starts:
                        f, vec = window_features(sent, pay, ack, float(t0), float(Tw))
                        if f['n_sent'] < 50:
                            continue
                        row = {'dataset': name, 'session': sess, 'role': role, 'client': cidx,
                               'window_s': T, 'T_actual': Tw, 't0': float(t0 - t_start),
                               **{f'label_{k2}': v for k2, v in lab.items()}, **f}
                        rows.append(row)
                        vecs.append(vec)
            print(f'[{name}] {sess}: {len(rows)} rows so far', file=sys.stderr)
    keys = list(rows[0].keys()) + list(vecs[0].keys())
    with open(a.out, 'w', newline='') as fh:
        wr = csv.DictWriter(fh, fieldnames=keys)
        wr.writeheader()
        for r, v in zip(rows, vecs):
            wr.writerow({**r, **v})
    print(f'wrote {len(rows)} rows x {len(keys)} cols to {a.out}')


if __name__ == '__main__':
    main()
