#!/usr/bin/env python3
"""Score exports (gateway view or link view) with MixMatch's statistical rule and the drift-statistics
tree, so the two observation points can be compared on the same runs.

  --train NAME=EXPORT:RUN ...   runs used to fit eta/sigma and the trees (optional)
  --test  NAME=EXPORT:RUN ...   runs to score
Without --train, each test run is scored by leave-one-run-out across the test runs (>= 2 runs), or,
with a single run, by 5-fold CV grouped by initiator within the run (optimistic but informative).
Reports TPR at FPR = 1e-2 and AUC at 100 / 500 observations and full length.
"""
import argparse, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold, LeaveOneGroupOut

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
import pair_features as pf   # noqa: E402
from profile_features import FILES, load_run_labels   # noqa: E402
warnings.filterwarnings('ignore')
HGB = lambda: HistGradientBoostingClassifier(max_iter=150, learning_rate=0.08, random_state=0)


def build(specs):
    rows = []
    for spec in specs:
        name, rest = spec.split('=', 1) if '=' in spec else (Path(spec.split(':')[0]).name, spec)
        exp_dir, run_dir = rest.split(':', 1); exp_dir = Path(exp_dir)
        run_key = str(Path(run_dir).resolve())
        clients, sessions = load_run_labels(run_dir)
        names = exp_dir.joinpath('flowpairs.txt').read_text().split()
        inits, resps = [], []
        for k, sess in enumerate(names):
            d = exp_dir / sess
            st = {key: np.sort(np.loadtxt(d / fn, dtype=np.float64, ndmin=1) / 1e9) for key, fn in FILES.items()}
            ai, bi = sessions[int(sess.split('_')[1])] if sess.startswith('session_') else sessions[k]
            inits.append({'idx': ai, 'sent': st['initiator_to'], 'pay': st['initiator_from'], 'ack': st['initiator_ack'], 'sess': k})
            resps.append({'idx': bi, 'sent': st['responder_to'], 'pay': st['responder_from'], 'ack': st['responder_ack'], 'sess': k})
        for I in inits:
            for R in resps:
                new = pf.pair_rows(name, I, R, int(I['sess'] == R['sess']), {}, {})
                for r in new:
                    r['run'] = run_key          # exports of the same run are VIEWS of the same pairs
                rows += new
        print(f'[{name}] {len(inits)}x{len(resps)} pairs', file=sys.stderr)
    return pd.DataFrame(rows)


def tpr_at(s, y): return float((s[y == 1] > np.percentile(s[y == 0], 99)).mean())


def paper(train, test):
    t = train[train['label'] == 1]; s = np.zeros(len(test))
    for sn in ('data', 'ack'):
        m, sd = t[f'b1_{sn}_mean'], t[f'b1_{sn}_std']; eta = m.mean(); s2 = (sd ** 2 + (m - eta) ** 2).mean()
        s += -((test[f'b1_{sn}_std'] ** 2 + (test[f'b1_{sn}_mean'] - eta) ** 2) / (2 * s2)).to_numpy()
    return s


def blocks(df):
    pref = lambda p: [c for c in df.columns if c.startswith(p)]
    b3w = [c for c in pref('b3_') if c.endswith(('_eta_best', '_win_var'))]
    return {'tree b1': pref('b1_'), 'tree all': pref('b1_') + pref('b2_') + b3w + pref('b4_') + pref('b7_')}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--train', action='append', default=[]); ap.add_argument('--test', action='append', required=True)
    a = ap.parse_args()
    test = build(a.test); train = build(a.train) if a.train else None
    for L in (100, 500, 0):
        te = test[test['L'] == L].reset_index(drop=True); y = te['label'].to_numpy()
        if train is not None:
            tr = train[train['L'] == L]
            scores = {'paper': paper(tr, te)}
            for k, cols in blocks(te).items():
                scores[k] = HGB().fit(tr[cols].to_numpy(float), tr['label']).predict_proba(te[cols].to_numpy(float))[:, 1]
            mode = 'train->test'
        else:
            # several exports of the SAME run are different views of the same pairs: never put one
            # view of a pair in training and another in test; score each view on its own
            scores = {'paper': np.zeros(len(te))}; preds = {k: np.zeros(len(te)) for k in blocks(te)}
            multi_run = te['run'].nunique() > 1
            mode = 'leave-one-run-out' if multi_run else '5-fold by initiator within the run, per view'
            for view in (['all'] if multi_run else sorted(te['dataset'].unique())):
                sel = np.ones(len(te), bool) if multi_run else (te['dataset'] == view).to_numpy()
                sub = te[sel]; ys = y[sel]; idx = np.where(sel)[0]
                groups = sub['run'].to_numpy() if multi_run else sub['I'].to_numpy()
                splitter = LeaveOneGroupOut() if multi_run else GroupKFold(5)
                for tr_i, te_i in splitter.split(sub, ys, groups):
                    scores['paper'][idx[te_i]] = paper(sub.iloc[tr_i], sub.iloc[te_i])
                    for k, cols in blocks(te).items():
                        preds[k][idx[te_i]] = HGB().fit(sub.iloc[tr_i][cols].to_numpy(float), ys[tr_i]).predict_proba(sub.iloc[te_i][cols].to_numpy(float))[:, 1]
            scores.update(preds)
        print(f'L={"full" if L == 0 else L:>4} ({mode}; {int(y.sum())} true / {int((y == 0).sum())} impostor pair-windows)')
        for ds in sorted(te['dataset'].unique()):
            m = (te['dataset'] == ds).to_numpy()
            print(f'  {ds:20s} ' + '  '.join(f'{k}: TPR@1e-2={tpr_at(s[m], y[m]):.3f} AUC={roc_auc_score(y[m], s[m]):.3f}' for k, s in scores.items()))


if __name__ == '__main__':
    main()
