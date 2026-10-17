#!/usr/bin/env python3
"""Turn the raw logs of the rate sweep (run_queue.py) into the pair-feature tables of the tree.

For every finished, clean run and every observation point (--views):
  gateway  the gateways' wire log  (sent 2413 B, delivered payload 1643 B, ack 21 B)
  client   the clients' own log    (sent 2452 B, delivered payload 1673 B, ack 51 B; the gateway's
                                    70 B confirmations and the 28/40 B handshake are not used)
each client's three streams (sent, received payload, received acks) are cropped to the run's
sending window [warmup_end, send_end] (run.json timeline + wall_offset_ns) and every initiator is
paired with every responder of the run, exactly as before: one row per candidate pair x window
(L = 100, 500, 0 = whole flow) with the b1-b4 and b7 features of linkobs/tools/pair_features.py
(b6, the per-client profile features, stays empty). The run's plan settings are added as columns.

Runs are cached (OUT/cache/<view>/<run>.csv.gz), so re-running after more runs were collected only
processes the new ones. The tables are OUT/pairs_<view>.csv. With --splits, OUT/splits.csv assigns
every run to train / val / test (70 / 15 / 15 within each condition x strategy cell of dataset A,
dataset B all test), for run_model.py --splits.

  build_features.py --sweep ~/flow-matching-data/2026-10-17-rate-sweep/main --out OUT
                    [--views gateway,client] [--workers 2] [--splits] [--include-degraded]

It runs at low priority (nice 15), but each worker still takes a core: while the collection is
running keep --workers small, or the collection's runs may be flagged as degraded.
"""
import argparse, csv, json, os, random, sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd

LOCAL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LOCAL / 'linkobs/tools'))
import pair_features as pf                      # noqa: E402
from profile_features import load_run_labels    # noqa: E402

SIZES = {'gateway': dict(sent=2413, pay=1643, ack=21), 'client': dict(sent=2452, pay=1673, ack=51)}
PLAN_COLS = ['dataset', 'condition', 'strategy', 'send_ms', 'mu_ms', 'loop_ms', 'msg_per_min', 'message_size',
             'clients']


def sending_window(run_dir):
    """Wall-clock [warmup_end, send_end] in seconds, or None for runs without the clock offset."""
    t = json.loads((run_dir / 'run.json').read_text())['timeline_ns']
    if 'wall_offset_ns' not in t:
        return None
    off = t['wall_offset_ns']
    return (t['warmup_end'] + off) / 1e9, (t['send_end'] + off) / 1e9


def load_streams(run_dir, view, clients):
    """{client index: {'sent', 'pay', 'ack'}} sorted wall-clock seconds, cropped to the sending window."""
    tags = {}
    with (run_dir / 'clients.csv').open() as fh:
        for r in csv.DictReader(fh):
            tags[r['wire_tag']] = int(r['client'])
    frames = [pd.read_csv(f, dtype={'client': str}) for f in sorted((run_dir / f'{view}_wire').glob('wire-*.csv*'))]
    if not frames:
        raise ValueError(f'no {view} wire log')
    w = pd.concat(frames)
    w = w[w['client'].isin(tags)]
    win = sending_window(run_dir)
    t = w['wall_ns'].to_numpy(np.int64) / 1e9
    keep = np.ones(len(w), bool) if win is None else (t >= win[0]) & (t <= win[1])
    w, t = w[keep], t[keep]
    size, direction, client = w['size'].to_numpy(), w['direction'].to_numpy(), w['client'].map(tags).to_numpy()
    sz = SIZES[view]
    streams = {}
    for c in clients:
        mine = client == c
        streams[c] = {
            'sent': np.sort(t[mine & (direction == 'to_gw') & (size == sz['sent'])]),
            'pay': np.sort(t[mine & (direction == 'from_gw') & (size == sz['pay'])]),
            'ack': np.sort(t[mine & (direction == 'from_gw') & (size == sz['ack'])]),
        }
    empty = [c for c, s in streams.items() if min(len(s['sent']), len(s['pay']), len(s['ack'])) < 200]
    if empty:
        raise ValueError(f'clients {empty} have (almost) no {view} traffic in the window: wrong sizes?')
    return streams, win is not None


def process_run(run_dir, view, plan_row, cache):
    os.nice(15)
    run_dir = Path(run_dir)
    clients, sessions = load_run_labels(run_dir)
    streams, cropped = load_streams(run_dir, view, clients)
    inits = [{'idx': a, **streams[a], 'sess': k} for k, (a, b) in enumerate(sessions)]
    resps = [{'idx': b, **streams[b], 'sess': k} for k, (a, b) in enumerate(sessions)]
    rows = []
    for I in inits:
        for R in resps:
            rows += pf.pair_rows(run_dir.name, I, R, int(I['sess'] == R['sess']), {}, {})
    df = pd.DataFrame(rows)
    for k in PLAN_COLS[::-1]:
        df.insert(1, k if k != 'dataset' else 'experiment', plan_row[k])
    df.insert(1, 'view', view)
    df.insert(2, 'cropped', cropped)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    return run_dir.name, view, len(sessions), len(df)


def write_splits(runs, out, seed):
    """70 / 15 / 15 by run within each condition x strategy cell of A (at least one run in train);
    every B run is test (unseen rates)."""
    rng = random.Random(seed); rows = []
    for (ds, cond, strat), g in runs.groupby(['dataset', 'condition', 'strategy']):
        ids = sorted(g['run_id']); rng.shuffle(ids)
        if ds == 'B':
            rows += [(r, 'test') for r in ids]; continue
        n = len(ids); n_te = int(round(0.15 * n)) if n >= 3 else 0; n_va = int(round(0.15 * n)) if n >= 3 else 0
        rows += [(r, 'test') for r in ids[:n_te]] + [(r, 'val') for r in ids[n_te:n_te + n_va]] + \
                [(r, 'train') for r in ids[n_te + n_va:]]
    s = pd.DataFrame(rows, columns=['dataset', 'split']).merge(
        runs.rename(columns={'run_id': 'dataset', 'dataset': 'set'})[['dataset', 'set', 'condition', 'strategy']], on='dataset')
    s.insert(0, 'source', 'rate_sweep')
    s.to_csv(out, index=False)
    print(f'splits -> {out}: {s["split"].value_counts().to_dict()}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sweep', required=True, help='the runner\'s output dir (with manifest.csv and runs/)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--views', default='gateway,client')
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--include-degraded', action='store_true')
    ap.add_argument('--runs', help='comma-separated run ids (default: all finished clean runs)')
    ap.add_argument('--splits', action='store_true', help='also write OUT/splits.csv')
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()
    sweep, out = Path(a.sweep).expanduser(), Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    man = pd.read_csv(sweep / 'manifest.csv').drop_duplicates('run_id', keep='last')
    ok = ['clean', 'degraded'] if a.include_degraded else ['clean']
    runs = man[man['status'].isin(ok) & man['run_id'].map(lambda r: (sweep / 'runs' / r / 'DONE').exists())]
    if a.runs:
        runs = runs[runs['run_id'].isin(a.runs.split(','))]
    views = a.views.split(',')
    jobs = [(r, v) for r in runs.itertuples() for v in views
            if not (out / 'cache' / v / f'{r.run_id}.csv.gz').exists()]
    print(f'{len(runs)} runs selected; {len(jobs)} (run, view) jobs to build, the rest cached')
    failed = []
    with ProcessPoolExecutor(a.workers) as ex:
        futs = {ex.submit(process_run, sweep / 'runs' / r.run_id, v, r._asdict(),
                          out / 'cache' / v / f'{r.run_id}.csv.gz'): (r.run_id, v) for r, v in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                run, view, n_pairs, n_rows = f.result()
                print(f'[{i}/{len(jobs)}] {run} {view}: {n_pairs} true pairs, {n_rows} rows', flush=True)
            except Exception as err:
                failed.append(futs[f]); print(f'[{i}/{len(jobs)}] {futs[f]} FAILED: {err}', flush=True)
    for v in views:
        parts = [out / 'cache' / v / f'{r}.csv.gz' for r in runs['run_id']]
        parts = [p for p in parts if p.exists()]
        if parts:
            table = pd.concat(pd.read_csv(p) for p in parts)
            table.to_csv(out / f'pairs_{v}.csv', index=False)
            print(f'pairs_{v}.csv: {table["dataset"].nunique()} runs, {int(table[table["L"] == 0]["label"].sum())} '
                  f'true pairs, {len(table)} rows')
    if a.splits:
        write_splits(runs, out / 'splits.csv', a.seed)
    if failed:
        print(f'{len(failed)} jobs failed: {failed}')


if __name__ == '__main__':
    main()
