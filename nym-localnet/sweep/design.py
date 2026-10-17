#!/usr/bin/env python3
"""Write the run plan of the rate sweep: one row per load-test run.

Two datasets, both on the netem localnet and both kept well below the machine's capacity (no
overload: an overloaded Mac would distort the timestamps the attack relies on):
  A  main, no overload: for every path-selection strategy, the client defaults plus each of the four
     rate factors moved to its two other levels (one factor at a time): 9 settings per strategy.
  B  unseen rates, no overload: random combinations drawn between and beyond the levels of A
     (log-uniform), a random strategy per run. For testing generalisation only.

The number of clients per run follows from the load budget: one client emits
1000/send_ms + 1000/loop_ms packets per second, and a run gets as many clients as fit in
target_load x capacity (even, at least 4, at most --max-clients). Each condition gets enough runs
to reach its target number of true pairs (= clients / 2 per run).

  design.py --out plan.csv [--capacity 1600] [--clean-load 0.6] [--pairs-a 350] [--runs-b 200]
            [--strategies khf] [--seed 0]

Rows are shuffled (seeded), so conditions are interleaved in time and slow drifts of the machine
or network do not line up with any one condition.
"""
import argparse, csv, math, random

# client defaults (common/client-core/config-types/src/lib.rs) and the sweep levels
DEFAULT = dict(send_ms=20, mu_ms=15, loop_ms=200, msg_per_min=1)
LEVELS = dict(send_ms=[40, 20, 10], mu_ms=[15, 50, 150], loop_ms=[1000, 200, 50], msg_per_min=[0.5, 1, 2])
# B draws log-uniformly from these ranges (beyond A's levels on both sides)
RANGES_B = dict(send_ms=(8, 50), mu_ms=(10, 200), loop_ms=(40, 1500), msg_per_min=(0.25, 3))
STRATEGIES = {
    'khf': ['baseline', 'khf:1', 'khf:2', 'khf:3', 'khf:1,2', 'khf:1,3', 'khf:2,3', 'khf:1,2,3'],
    'kw': ['baseline', 'kw:2', 'kw:3', 'kw:5'],
    'alpha': ['baseline', 'alpha:0.2', 'alpha:0.5', 'alpha:0.8'],
}
PACKET_PAYLOAD = 1_600          # approx. bytes of message per Sphinx packet


def client_rate(send_ms, loop_ms):
    return 1000 / send_ms + 1000 / loop_ms


def clients_for(send_ms, loop_ms, budget, max_clients):
    n = int(budget / client_rate(send_ms, loop_ms)) // 2 * 2
    return max(4, min(max_clients, n))


def real_fits(send_ms, msg_per_min, message_size):
    """Real packets must take at most 90 % of the sending slots on average, or the client's
    queue grows without bound (a single file is still sent in a burst that takes a while)."""
    real_pps = msg_per_min / 60 * math.ceil(message_size / PACKET_PAYLOAD)
    return real_pps <= 0.9 * 1000 / send_ms


def settings_a():
    yield 'default', dict(DEFAULT)
    for f, levels in LEVELS.items():
        for v in levels:
            if v != DEFAULT[f]:
                yield f'{f}={v}', {**DEFAULT, f: v}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--strategies', default='khf', choices=sorted(STRATEGIES))
    ap.add_argument('--capacity', type=float, default=1600, help='machine capacity, total client packets/s (calibrate)')
    ap.add_argument('--clean-load', type=float, default=0.6, help='fraction of capacity used by A and B')
    ap.add_argument('--max-clients', type=int, default=60)
    ap.add_argument('--pairs-a', type=int, default=350, help='true pairs per condition of A')
    ap.add_argument('--runs-b', type=int, default=200)
    ap.add_argument('--duration', type=int, default=90, help='sending time per run, s')
    ap.add_argument('--warmup', type=int, default=10)
    ap.add_argument('--drain', type=int, default=60, help='max wait for files still in flight, s')
    ap.add_argument('--message-size', type=int, default=1_000_000, help='bytes per real message (file)')
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    strategies = STRATEGIES[a.strategies]
    rows = []

    def add(dataset, condition, strategy, s, load, n_runs):
        if not real_fits(s['send_ms'], s['msg_per_min'], a.message_size):
            print(f'skipped {dataset} {condition} {strategy}: real traffic exceeds 90 % of the sending slots')
            return
        clients = clients_for(s['send_ms'], s['loop_ms'], load * a.capacity, a.max_clients)
        for rep in range(n_runs or math.ceil(a.pairs_a / (clients // 2))):
            rows.append(dict(dataset=dataset, condition=condition, strategy=strategy, **s, load_factor=load,
                             clients=clients, message_size=a.message_size, pkt_per_s=round(clients * client_rate(s['send_ms'], s['loop_ms'])),
                             duration_s=a.duration, warmup_s=a.warmup, drain_s=a.drain, replicate=rep))

    for strategy in strategies:
        for name, s in settings_a():
            add('A', name, strategy, s, a.clean_load, 0)
    for k in range(a.runs_b):
        s = {f: round(math.exp(rng.uniform(math.log(lo), math.log(hi))), 2 if f == 'msg_per_min' else 0)
             for f, (lo, hi) in RANGES_B.items()}
        s = {f: (v if f == 'msg_per_min' else int(v)) for f, v in s.items()}
        add('B', f'random{k:03d}', rng.choice(strategies), s, a.clean_load, 1)

    rng.shuffle(rows)
    for i, r in enumerate(rows):
        r['run_id'] = f'r{i:05d}'
        r['seed'] = 1000 + i
    cols = ['run_id', 'dataset', 'condition', 'strategy', 'send_ms', 'mu_ms', 'loop_ms', 'msg_per_min', 'load_factor',
            'message_size', 'clients', 'pkt_per_s', 'duration_s', 'warmup_s', 'drain_s', 'replicate', 'seed']
    with open(a.out, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)

    # summary: runs, true pairs and the wall time (~105 s per 90 s run, measured in the pilot)
    per_run_s = a.duration + 15
    for ds in 'AB':
        sel = [r for r in rows if r['dataset'] == ds]
        pairs = sum(r['clients'] // 2 for r in sel)
        print(f'{ds}: {len(sel):5d} runs, {pairs:6d} true pairs, ~{len(sel) * per_run_s / 3600:6.1f} h')
    print(f'total: {len(rows)} runs, ~{len(rows) * per_run_s / 86400:.1f} days -> {a.out}')


if __name__ == '__main__':
    main()
