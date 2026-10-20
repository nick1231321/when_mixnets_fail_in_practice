#!/usr/bin/env python3
"""Write the run plan of the path-selection replay: the clean seen-profile runs of a finished
collection (design_hetero.py's TRAIN, VAL and TEST_seen) are repeated with the SAME clients, profiles,
pairs and seed under each path-selection strategy, so a strategy's effect on matching accuracy is
measured pair by pair against the baseline run already collected. Per-message sessions
(session = "message"): a client redraws its fixed hops for every file, as in Rahimi's model.

The plan also has
  - baseline controls: --controls of the selected runs repeated under baseline, interleaved with the
    strategy runs, to check that the localnet did not change between the two collections;
  - a homogeneous block (dataset HOMO): --homo-runs runs per strategy (baseline included) in which
    every client has the Nym defaults [mu 15, send 20, loop 200, both streams on], MixMatch's own
    setting, the anchor of the strategy comparison without any heterogeneity.

Run ids are r00000... (run_queue.py needs a letter and digits); `source_run` names the baseline twin
of a replayed run (empty for HOMO runs). Every other column is copied from the source row.

  design_replay.py --plan hetero/plan.csv --manifest hetero/main/manifest.csv --out replay/plan.csv
                   [--strategies "khf:1;khf:1,2"] [--controls 50] [--homo-runs 50]
                   [--sets TRAIN,VAL,TEST_seen] [--first-id 0]
The unseen-value test sets can be added as a second plan run into the same output directory:
  design_replay.py ... --sets TEST_mu,TEST_send_in,TEST_send_out,TEST_cover,TEST_all --controls 0 --homo-runs 0
                   --first-id 1230 --out replay/plan_unseen.csv
"""
import argparse, csv, json, random

SEEN_SETS = ['TRAIN', 'VAL', 'TEST_seen']
HOMO_PROFILE = [15, 20, 200, 1, 1]           # the Nym client's defaults: mixing 15 ms, send 20 ms, loop 200 ms
COLS = ['run_id', 'dataset', 'condition', 'strategy', 'session', 'send_ms', 'mu_ms', 'loop_ms', 'msg_per_min',
        'load_factor', 'message_size', 'clients', 'pkt_per_s', 'duration_s', 'warmup_s', 'drain_s', 'replicate',
        'seed', 'clients_spec', 'source_run']


def client_rate(c):
    mu, send, loop, poisson, cover = c
    return (1000 / send if poisson else 1.0 / 60 * 625) + (1000 / loop if cover else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plan', required=True, help='the finished collection\'s plan.csv')
    ap.add_argument('--manifest', required=True, help='its manifest.csv (only clean runs are replayed)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--strategies', default='khf:1;khf:1,2', help='strategies to replay, separated by ";"')
    ap.add_argument('--sets', default=','.join(SEEN_SETS), help='datasets of the source plan to replay (comma-separated)')
    ap.add_argument('--first-id', type=int, default=0, help='number of the first run id (to continue an earlier replay plan)')
    ap.add_argument('--controls', type=int, default=50, help='selected runs repeated under baseline')
    ap.add_argument('--homo-runs', type=int, default=50, help='homogeneous runs per strategy (0 = none)')
    ap.add_argument('--no-homo-baseline', action='store_true', help='no baseline runs in the homogeneous block (already collected)')
    ap.add_argument('--capacity', type=float, default=2500)
    ap.add_argument('--clean-load', type=float, default=0.6)
    ap.add_argument('--max-pairs', type=int, default=30)
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    strategies = [s for s in a.strategies.split(';') if s]

    status = {}
    for r in csv.DictReader(open(a.manifest)):
        status[r['run_id']] = r['status']                      # the latest attempt wins
    source = [r for r in csv.DictReader(open(a.plan))
              if r['dataset'] in a.sets.split(',') and status.get(r['run_id']) == 'clean']
    rows = []

    def replay(src, strategy, dataset=None):
        rows.append({**{k: src.get(k, '') for k in COLS}, 'dataset': dataset or src['dataset'],
                     'condition': strategy, 'strategy': strategy, 'session': 'message',
                     'source_run': src['run_id']})

    for src in source:
        for s in strategies:
            replay(src, s)
    for src in rng.sample(source, min(a.controls, len(source))):
        replay(src, 'baseline', dataset='CONTROL')

    # homogeneous block: as many default clients as fit in the budget
    budget = a.clean_load * a.capacity
    pairs = min(a.max_pairs, int(budget // (2 * client_rate(HOMO_PROFILE))))
    for s in ([] if a.no_homo_baseline else ['baseline']) + strategies:
        for k in range(a.homo_runs):
            rows.append(dict(run_id='', dataset='HOMO', condition=s, strategy=s, session='message',
                             send_ms=HOMO_PROFILE[1], mu_ms=HOMO_PROFILE[0], loop_ms=HOMO_PROFILE[2],
                             msg_per_min=1.0, load_factor=a.clean_load, message_size=1_000_000,
                             clients=2 * pairs, pkt_per_s=round(2 * pairs * client_rate(HOMO_PROFILE)),
                             duration_s=90, warmup_s=10, drain_s=180, replicate=k, seed=7000 + k,
                             clients_spec=json.dumps([HOMO_PROFILE] * (2 * pairs), separators=(',', ':')),
                             source_run=''))
    rng.shuffle(rows)
    for k, r in enumerate(rows, start=a.first_id):
        r['run_id'] = f'r{k:05d}'
    with open(a.out, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=COLS); w.writeheader(); w.writerows(rows)
    for key in sorted({(r['dataset'], r['strategy']) for r in rows}):
        sel = [r for r in rows if (r['dataset'], r['strategy']) == key]
        print(f'{key[0]:10s} {key[1]:10s} {len(sel):4d} runs, {sum(int(r["clients"]) // 2 for r in sel):5d} true pairs')
    print(f'total {len(rows)} runs, ~{len(rows) * 2.4 / 60:.0f} h at 2.4 min/run -> {a.out}')


if __name__ == '__main__':
    main()
