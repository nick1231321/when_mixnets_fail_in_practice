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

Nothing is run here: the plan is executed by run_queue.py.

  design_hetero.py --out plan.csv [--pairs TRAIN=3000,VAL=500,TEST_seen=500,...]
"""
import argparse, csv, json, random

# ----------------------------------------------------------------------------- the profile values
# SEEN: the values the NymVPN app offers; the only values in TRAIN, VAL and TEST_seen.
#   mu    mixing delay per hop, ms (the app's slider; 0 = no mixing delay)
#   send  sending interval of the continuous stream, ms (10 / 20 / 30 ms = the app's 2 / 1 / 0.7 Mbps)
#   loop  loop cover interval, ms (200 = Base, 40 / 20 / 10 = 5x / 10x / 20x more cover traffic)
SEEN = dict(mu=[0, 15, 50, 100, 200], send=[10, 20, 30], loop=[200, 40, 20, 10])
# UNSEEN: values the Nym client accepts but that never appear in training; one test set each.
#   send_in   sending intervals between the trained ones (TEST_send_in)
#   send_out  sending intervals outside the trained range, faster or slower (TEST_send_out)
UNSEEN = dict(mu=[5, 30, 75, 150, 180], send_in=[15, 25], send_out=[5, 7, 40, 50], loop=[100, 60, 15])
OFF_SHARE = 0.15                 # share of seen clients with "send continuously" / "background cover" off
MSG_PER_MIN, MESSAGE_SIZE = 1.0, 1_000_000                     # every client: one 1 MB file per minute on average
REAL_PPS = MSG_PER_MIN / 60 * 625    # a 1 MB file per minute, ~625 packets each, when Poisson sending is off
TESTS = ['TEST_seen', 'TEST_mu', 'TEST_send_in', 'TEST_send_out', 'TEST_cover', 'TEST_all']


def client_rate(c):
    """Packets per second a client puts on its gateway link, used to keep each run below capacity.
    With continuous (Poisson) sending on, the client sends 1000/send packets/s whatever it has to send
    (cover fills the gaps); off, only its real file traffic (REAL_PPS). Loop cover adds 1000/loop."""
    mu, send, loop, poisson, cover = c
    return (1000 / send if poisson else REAL_PPS) + (1000 / loop if cover else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True, help='where to write the plan (plan.csv)')
    ap.add_argument('--pairs', default='TRAIN=3000,VAL=500,' + ','.join(f'{t}=500' for t in TESTS),
                    help='true pairs to collect per dataset, SET=N,SET=N,... (runs are added until each target is reached)')
    ap.add_argument('--capacity', type=float, default=1600,
                    help='packets/s the machine handles before delivery drops (measured by calibration; 2500 on the c3d-standard-16 VM)')
    ap.add_argument('--clean-load', type=float, default=0.6,
                    help='fraction of --capacity a run may use, so no run overloads the machine')
    ap.add_argument('--max-pairs', type=int, default=20,
                    help='at most this many pairs per run, even if the budget would allow more (30 on the VM)')
    ap.add_argument('--duration', type=int, default=90, help='sending time of a run, s (the first --warmup s are not used)')
    ap.add_argument('--warmup', type=int, default=10, help='seconds at the start of sending that are discarded (clients settle)')
    ap.add_argument('--drain', type=int, default=180, help='max wait for files still in flight, s (a slow sender needs ~30 s per queued 1 MB file)')
    ap.add_argument('--seed', type=int, default=0, help='random seed: the same seed writes the same plan')
    a = ap.parse_args()
    rng = random.Random(a.seed)
    budget = a.clean_load * a.capacity                           # packets/s allowed per run (0.6 x 2500 = 1500 on the VM)

    def client(unseen=(), initiator=False):
        """[mu, send, loop, poisson, cover]; `unseen` names the settings drawn from unseen values
        (the initiator always unseen, the responder from unseen + seen)."""
        def val(name, seen, extra):
            # one setting's value: a seen value, unless this test set makes the setting unseen
            if name in unseen:
                return rng.choice(extra if initiator else extra + seen)
            return rng.choice(seen)
        mu = val('mu', SEEN['mu'], UNSEEN['mu'])
        # the sending interval's unseen values depend on the test set: in between, or outside
        send_unseen = UNSEEN['send_in'] if 'send_in' in unseen else UNSEEN['send_out']
        send = val('send_in' if 'send_in' in unseen else 'send_out', SEEN['send'], send_unseen)
        loop = val('loop', SEEN['loop'], UNSEEN['loop'])
        # the on/off switches: off for OFF_SHARE of the clients, but always on when the test set is
        # about that stream (an unseen sending interval or cover interval of a switched-off stream
        # would not be visible at all)
        sending_unseen = bool({'send_in', 'send_out'} & set(unseen))
        poisson = True if sending_unseen else rng.random() >= OFF_SHARE
        cover = True if 'loop' in unseen else rng.random() >= OFF_SHARE
        return [mu, send, loop, int(poisson), int(cover)]

    # which settings are unseen in each dataset. TEST_all draws its sending variant (in between or
    # outside) anew for every pair in the loop below; this entry is not used for it, but the draw
    # here is kept so that the same --seed keeps writing the same plan.
    unseen_of = {'TRAIN': (), 'VAL': (), 'TEST_seen': (), 'TEST_mu': ('mu',), 'TEST_send_in': ('send_in',),
                 'TEST_send_out': ('send_out',), 'TEST_cover': ('loop',),
                 'TEST_all': ('mu', rng.choice(['send_in', 'send_out']), 'loop')}
    rows = []
    for item in a.pairs.split(','):                            # one dataset at a time, e.g. "TRAIN=3000"
        ds, target = item.split('='); target = int(target); got = 0
        while got < target:                                    # add runs until the dataset has its pairs
            # fill one run: add pairs (an initiator and a responder) while the run stays within budget
            inits, resps, load = [], [], 0.0
            while len(inits) < a.max_pairs:
                u = unseen_of[ds] if ds != 'TEST_all' else ('mu', rng.choice(['send_in', 'send_out']), 'loop')
                i, r = client(u, True), client(u, False)
                add = client_rate(i) + client_rate(r)
                if load + add > budget and len(inits) >= 2:    # full (a run always gets at least 2 pairs)
                    break
                inits.append(i); resps.append(r); load += add
            got += len(inits)
            # one plan row = one run. The per-client values are in clients_spec; send_ms / mu_ms /
            # loop_ms say "mixed" (they are used by the homogeneous plans of design.py)
            rows.append(dict(dataset=ds, condition=ds, strategy='baseline', send_ms='mixed', mu_ms='mixed',
                             loop_ms='mixed', msg_per_min=MSG_PER_MIN, load_factor=a.clean_load,
                             message_size=MESSAGE_SIZE, clients=2 * len(inits), pkt_per_s=round(load),
                             duration_s=a.duration, warmup_s=a.warmup, drain_s=a.drain, replicate=len(rows),
                             clients_spec=json.dumps(inits + resps, separators=(',', ':'))))
    # shuffle so the datasets are interleaved in time: the machine's and network's state over the
    # day affects TRAIN and every TEST set alike. Then number the runs and give each its own seed.
    rng.shuffle(rows)
    for k, r in enumerate(rows):
        r['run_id'] = f'h{k:05d}'; r['seed'] = 5000 + k
    cols = ['run_id', 'dataset', 'condition', 'strategy', 'send_ms', 'mu_ms', 'loop_ms', 'msg_per_min', 'load_factor',
            'message_size', 'clients', 'pkt_per_s', 'duration_s', 'warmup_s', 'drain_s', 'replicate', 'seed',
            'clients_spec']
    with open(a.out, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)

    # summary: runs and true pairs per dataset, and a rough duration (the VM takes ~2.4 min per run)
    per_run_s = a.duration + 45                     # sending + drain + connecting + wire extraction
    for ds in unseen_of:
        sel = [r for r in rows if r['dataset'] == ds]
        print(f'{ds:14s} {len(sel):4d} runs, {sum(r["clients"] // 2 for r in sel):5d} true pairs, '
              f'~{sum(r["clients"] for r in sel) / max(len(sel), 1) / 2:.1f} pairs/run')
    print(f'total {len(rows)} runs, {sum(r["clients"] // 2 for r in rows)} true pairs, '
          f'~{len(rows) * per_run_s / 3600:.0f} h -> {a.out}')


if __name__ == '__main__':
    main()
