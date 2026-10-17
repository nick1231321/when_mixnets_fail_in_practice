#!/usr/bin/env python3
"""Write the run plan of the heterogeneous-pairs experiment: every client of a run draws its own value
of every traffic setting NymVPN lets a user change, so the two partners of a pair generally differ.

NymVPN's mixnet tuning (nym-vpn-client, nym-vpn-lib-types/src/service.rs; the core accepts the ranges
in brackets):
  packet mixing profile      average_packet_delay              slider 0-200 ms        (0-200)
  send traffic continuously  poisson on/off + sending interval 10 / 20 / 30 ms        (5-50)
  background cover traffic   loop cover on/off + interval      200 / 40 / 20 / 10 ms  (0-200)

TRAIN, VAL and TEST_seen use app values only (the toggles are off for a share of the clients). Each
other test set makes ONE setting unseen: its initiators take an unseen value of that setting (allowed
by NymVPN but not used in training), its responders draw from unseen + seen, the other settings stay
seen. TEST_all makes every setting unseen. Since the app's mixing slider spans 0-200 ms, unseen
mixing delays are necessarily in between the trained ones; sending intervals can also lie outside.

A run is filled with pairs until its clients' packet rates reach --clean-load x --capacity (no
overload). `clients_spec` lists [mu, send, loop, poisson, cover] per client, initiators first, for
the load test's split-pairs pairing (client i <-> i + n/2).

  design_hetero.py --out plan.csv [--pairs TRAIN=3000,VAL=500,TEST_seen=500,...]
"""
import argparse, csv, json, random

SEEN = dict(mu=[0, 15, 50, 100, 200], send=[10, 20, 30], loop=[200, 40, 20, 10])
UNSEEN = dict(mu=[5, 30, 75, 150, 180], send_in=[15, 25], send_out=[5, 7, 40, 50], loop=[100, 60, 15])
OFF_SHARE = 0.15                 # share of seen clients with "send continuously" / "background cover" off
MSG_PER_MIN, MESSAGE_SIZE = 1.0, 1_000_000
REAL_PPS = MSG_PER_MIN / 60 * 625    # a 1 MB file per minute, ~625 packets each, when Poisson sending is off
TESTS = ['TEST_seen', 'TEST_mu', 'TEST_send_in', 'TEST_send_out', 'TEST_cover', 'TEST_all']


def client_rate(c):
    mu, send, loop, poisson, cover = c
    return (1000 / send if poisson else REAL_PPS) + (1000 / loop if cover else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--pairs', default='TRAIN=3000,VAL=500,' + ','.join(f'{t}=500' for t in TESTS))
    ap.add_argument('--capacity', type=float, default=1600)
    ap.add_argument('--clean-load', type=float, default=0.6)
    ap.add_argument('--max-pairs', type=int, default=20)
    ap.add_argument('--duration', type=int, default=90)
    ap.add_argument('--warmup', type=int, default=10)
    ap.add_argument('--drain', type=int, default=60)
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    budget = a.clean_load * a.capacity

    def client(unseen=(), initiator=False):
        """[mu, send, loop, poisson, cover]; `unseen` names the settings drawn from unseen values
        (the initiator always unseen, the responder from unseen + seen)."""
        def val(name, seen, extra):
            if name in unseen:
                return rng.choice(extra if initiator else extra + seen)
            return rng.choice(seen)
        mu = val('mu', SEEN['mu'], UNSEEN['mu'])
        send_unseen = UNSEEN['send_in'] if 'send_in' in unseen else UNSEEN['send_out']
        send = val('send_in' if 'send_in' in unseen else 'send_out', SEEN['send'], send_unseen)
        loop = val('loop', SEEN['loop'], UNSEEN['loop'])
        sending_unseen = bool({'send_in', 'send_out'} & set(unseen))
        poisson = True if sending_unseen else rng.random() >= OFF_SHARE
        cover = True if 'loop' in unseen else rng.random() >= OFF_SHARE
        return [mu, send, loop, int(poisson), int(cover)]

    unseen_of = {'TRAIN': (), 'VAL': (), 'TEST_seen': (), 'TEST_mu': ('mu',), 'TEST_send_in': ('send_in',),
                 'TEST_send_out': ('send_out',), 'TEST_cover': ('loop',),
                 'TEST_all': ('mu', rng.choice(['send_in', 'send_out']), 'loop')}
    rows = []
    for item in a.pairs.split(','):
        ds, target = item.split('='); target = int(target); got = 0
        while got < target:
            inits, resps, load = [], [], 0.0
            while len(inits) < a.max_pairs:
                u = unseen_of[ds] if ds != 'TEST_all' else ('mu', rng.choice(['send_in', 'send_out']), 'loop')
                i, r = client(u, True), client(u, False)
                add = client_rate(i) + client_rate(r)
                if load + add > budget and len(inits) >= 2:
                    break
                inits.append(i); resps.append(r); load += add
            got += len(inits)
            rows.append(dict(dataset=ds, condition=ds, strategy='baseline', send_ms='mixed', mu_ms='mixed',
                             loop_ms='mixed', msg_per_min=MSG_PER_MIN, load_factor=a.clean_load,
                             message_size=MESSAGE_SIZE, clients=2 * len(inits), pkt_per_s=round(load),
                             duration_s=a.duration, warmup_s=a.warmup, drain_s=a.drain, replicate=len(rows),
                             clients_spec=json.dumps(inits + resps, separators=(',', ':'))))
    rng.shuffle(rows)
    for k, r in enumerate(rows):
        r['run_id'] = f'h{k:05d}'; r['seed'] = 5000 + k
    cols = ['run_id', 'dataset', 'condition', 'strategy', 'send_ms', 'mu_ms', 'loop_ms', 'msg_per_min', 'load_factor',
            'message_size', 'clients', 'pkt_per_s', 'duration_s', 'warmup_s', 'drain_s', 'replicate', 'seed',
            'clients_spec']
    with open(a.out, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
    per_run_s = a.duration + 45                     # sending + drain + connecting + wire extraction
    for ds in unseen_of:
        sel = [r for r in rows if r['dataset'] == ds]
        print(f'{ds:14s} {len(sel):4d} runs, {sum(r["clients"] // 2 for r in sel):5d} true pairs, '
              f'~{sum(r["clients"] for r in sel) / max(len(sel), 1) / 2:.1f} pairs/run')
    print(f'total {len(rows)} runs, {sum(r["clients"] // 2 for r in rows)} true pairs, '
          f'~{len(rows) * per_run_s / 3600:.0f} h -> {a.out}')


if __name__ == '__main__':
    main()
