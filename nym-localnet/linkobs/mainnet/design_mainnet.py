#!/usr/bin/env python3
"""Write the run plan of the live-network collection (queue.sh runs it with run_wire.sh).

Two kinds of runs, interleaved so that the network's time-varying loss hits both alike:
  homo    every client with the SDK defaults (the live network's own settings), as MixMatch's live runs
  hetero  every client with its own NymVPN-allowed values of mixing delay, sending interval and loop
          cover interval, drawn from the same seen values as the localnet plan (sweep/design_hetero.py),
          one group per client, so the two partners of a pair generally differ
Every run: 10 clients (5 random pairs) through two entry gateways picked by the seed, 1 MB per client
per minute, baseline routing. Seeds are distinct so every run has its own gateway pair and pairing.

  design_mainnet.py --out plan.csv [--runs 200] [--clients 10] [--duration 240]

With --replay PLAN.csv the rows of a finished plan are repeated with the same seed, clients and
profiles under each of --strategies (separated by ";"), with per-message sessions, so the strategies
are compared on the same configurations; run ids are <strategy tag>_<source run id>.
"""
import argparse, csv, random

SEEN = dict(mu=[0, 15, 50, 100, 200], send=[10, 20, 30], loop=[200, 40, 20, 10])
MSG_PER_MIN, MESSAGE_SIZE = 1.0, 1_000_000


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--runs', type=int, default=200)
    ap.add_argument('--clients', type=int, default=10)
    ap.add_argument('--duration', type=int, default=240)
    ap.add_argument('--drain', type=int, default=120)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--replay', help='a finished plan.csv to repeat under --strategies')
    ap.add_argument('--strategies', default='khf:1;khf:1,2', help='strategies for --replay, separated by ";"')
    a = ap.parse_args()
    rng = random.Random(a.seed)
    rows = []
    if a.replay:
        for src in csv.DictReader(open(a.replay)):
            for s in a.strategies.split(';'):
                tag = s.replace(':', '').replace(',', '')
                # queue.sh splits the plan at commas, so a strategy's comma is written as "+" (khf:1+2)
                rows.append({**src, 'run_id': f'{tag}_{src["run_id"]}', 'strategy': s.replace(',', '+'), 'session': 'message'})
        rng.shuffle(rows)
        cols = ['run_id', 'dataset', 'seed', 'clients', 'duration_s', 'drain_s', 'strategy', 'groups', 'session']
        with open(a.out, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
        print(f'{len(rows)} runs ({len(rows) // len(a.strategies.split(";"))} per strategy), '
              f'~{len(rows) * (int(rows[0]["duration_s"]) + 180) / 3600:.0f} h -> {a.out}')
        return
    for k in range(a.runs):
        kind = 'homo' if k % 2 == 0 else 'hetero'
        if kind == 'homo':
            groups = f'homo:{a.clients}::::{MSG_PER_MIN}:{MESSAGE_SIZE}'
        else:
            groups = ';'.join(f'c{i}:1:{rng.choice(SEEN["mu"])}:{rng.choice(SEEN["send"])}:{rng.choice(SEEN["loop"])}'
                              f':{MSG_PER_MIN}:{MESSAGE_SIZE}' for i in range(a.clients))
        rows.append(dict(run_id=f'm{k + 1:04d}', dataset=kind, seed=1000 + k, clients=a.clients,
                         duration_s=a.duration, drain_s=a.drain, strategy='baseline', groups=groups,
                         session='connection'))
    with open(a.out, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f'{len(rows)} runs ({len(rows) // 2} homo, {len(rows) - len(rows) // 2} hetero), '
          f'{len(rows) * a.clients // 2} true pairs, ~{len(rows) * (a.duration + 180) / 3600:.0f} h -> {a.out}')


if __name__ == '__main__':
    main()
