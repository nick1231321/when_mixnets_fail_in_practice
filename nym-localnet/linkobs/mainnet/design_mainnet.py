#!/usr/bin/env python3
"""Write the run plan of the live-network collection (queue.sh runs it with run_wire.sh).

The plan crosses three traffic patterns with two settings, all runs interleaved and shuffled so that
the network's time-varying loss hits every kind alike:
  oneway  shuffled pairs, one side sends 1 MB files, the other only receives      (60 runs per settings)
  sync    shuffled pairs, both sides submit their files at the same moments        (40)
  random  every client sends, every file to a client drawn anew among the others  (40), one file
          every 20 s on average instead of one a minute
  homo    every client with the SDK defaults (the live network's own settings)
  hetero  every client with its own NymVPN-allowed values of mixing delay, sending interval and loop
          cover interval, one group per client, so the two partners of a pair generally differ
In a third of the runs of every kind two clients are marked as hidden: they take part in the run but
the adversary does not see their links, so their partners must be reported as outside the set.
Every run: 10 clients through two entry gateways picked by the seed, baseline routing. Seeds are
distinct so every run has its own gateway pair and pairing.

  design_mainnet.py --out plan.csv [--clients 10] [--duration 240]
  design_mainnet.py --out plan.csv --classic [--runs 200]       the old plan: homo/hetero pairs, both sides send

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
    ap.add_argument('--classic', action='store_true', help='the old two-kind plan (random-pairs, no hidden clients)')
    ap.add_argument('--hidden', type=int, default=2, help='clients hidden from the adversary in the marked runs')
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
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore', lineterminator='\n'); w.writeheader(); w.writerows(rows)
        print(f'{len(rows)} runs ({len(rows) // len(a.strategies.split(";"))} per strategy), '
              f'~{len(rows) * (int(rows[0]["duration_s"]) + 180) / 3600:.0f} h -> {a.out}')
        return
    def groups_spec(settings, msg_per_min):
        if settings == 'homo':
            return f'homo:{a.clients}::::{msg_per_min}:{MESSAGE_SIZE}'
        return ';'.join(f'c{i}:1:{rng.choice(SEEN["mu"])}:{rng.choice(SEEN["send"])}:{rng.choice(SEEN["loop"])}'
                        f':{msg_per_min}:{MESSAGE_SIZE}' for i in range(a.clients))

    if a.classic:
        for k in range(a.runs):
            kind = 'homo' if k % 2 == 0 else 'hetero'
            rows.append(dict(run_id=f'm{k + 1:04d}', dataset=kind, seed=1000 + k, clients=a.clients,
                             duration_s=a.duration, drain_s=a.drain, strategy='baseline',
                             groups=groups_spec(kind, MSG_PER_MIN), session='connection', pairing='random-pairs', hidden=''))
    else:
        # pattern, pairing mode of the load test, runs per settings, of which with hidden clients, files per minute
        KINDS = [('oneway', 'random-pairs-oneway', 60, 20, MSG_PER_MIN),
                 ('sync', 'random-pairs-sync', 40, 15, MSG_PER_MIN),
                 ('random', 'random-recipient', 40, 15, 3.0)]
        kinds = []
        for pattern, pairing, runs, with_hidden, rate in KINDS:
            for settings in ('homo', 'hetero'):
                kinds += [(pattern, pairing, settings, rate, j < with_hidden) for j in range(runs)]
        rng.shuffle(kinds)
        for k, (pattern, pairing, settings, rate, hide) in enumerate(kinds):
            hidden = ';'.join(str(c) for c in sorted(rng.sample(range(a.clients), a.hidden))) if hide else ''
            rows.append(dict(run_id=f'm{k + 1:04d}', dataset=f'{pattern}-{settings}', seed=1000 + k, clients=a.clients,
                             duration_s=a.duration, drain_s=a.drain, strategy='baseline',
                             groups=groups_spec(settings, rate), session='connection', pairing=pairing, hidden=hidden))
    with open(a.out, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator='\n'); w.writeheader(); w.writerows(rows)
    by_kind = {}
    for r in rows:
        by_kind[r['dataset']] = by_kind.get(r['dataset'], 0) + 1
    print(f'{len(rows)} runs, {sum(1 for r in rows if r["hidden"])} with hidden clients, '
          f'~{len(rows) * (a.duration + 180) / 3600:.0f} h -> {a.out}')
    print('  ' + ', '.join(f'{k} {n}' for k, n in sorted(by_kind.items())))


if __name__ == '__main__':
    main()
