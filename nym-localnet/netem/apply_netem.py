#!/usr/bin/env python3
"""Applies (or clears) per-pair network delays on the netem localnet with `tc netem`.

Every node runs in its own network namespace (netem/gen_compose.py), so inside each node's
container we shape its egress on eth0: an htb root qdisc with one class per destination node,
each class holding a netem qdisc with that pair's one-way delay (and jitter), selected by a
u32 filter on the destination IP. Traffic to anything else (the host, i.e. clients) takes the
default class with no delay. Both directions of a pair get their own egress delay, so a
round trip adds up to the pair's RTT.

Input: netem.csv from latency/build_rtt.py (from,to,delay_ms,jitter_ms; local node ids) built
for the same layout as the compose file.

Usage:
  apply_netem.py data/l3x3g2/netem.csv                      # apply
  apply_netem.py data/l3x3g2/netem.csv --processing-ms 5    # plus a constant per hop
  apply_netem.py --clear --mixes-per-layer 3 --gateways 2   # remove all shaping
  apply_netem.py data/l3x3g2/netem.csv --show mix1          # print one node's rules
"""
import argparse
import csv
import subprocess
import sys
from collections import defaultdict

LAYERS = 3


def node_names(mixes_per_layer, gateways):
    mixes = LAYERS * mixes_per_layer
    names = {i: f"mix{i}" for i in range(1, mixes + 1)}
    for g in range(gateways):
        names[mixes + 1 + g] = "gateway" if g == 0 else f"gateway{g + 1}"
    return names


def docker_sh(container, script, check=True):
    return subprocess.run(["docker", "exec", container, "sh", "-c", script],
                          capture_output=True, text=True, check=check)


def rules_for(src, delays, ip, processing_ms, use_jitter=False):
    """tc commands shaping `src`'s egress to every destination in `delays` {dst: (ms, jitter)}."""
    lines = ["tc qdisc del dev eth0 root 2>/dev/null || true",
             "tc qdisc add dev eth0 root handle 1: htb default 9999",
             "tc class add dev eth0 parent 1: classid 1:9999 htb rate 10gbit"]
    for k, (dst, (ms, jitter)) in enumerate(sorted(delays.items()), start=1):
        d = ms + processing_ms
        # jitter is off by default: netem's random jitter reorders TCP segments, and on pairs where
        # it is comparable to the delay that collapses the congestion window of the sparse,
        # high-RTT node-to-node connections (observed as node-side flush timeouts and drops)
        netem = f"delay {d:.3f}ms" + (f" {jitter:.3f}ms distribution normal" if use_jitter and jitter > 0 else "")
        lines += [f"tc class add dev eth0 parent 1: classid 1:{k} htb rate 10gbit",
                  f"tc qdisc add dev eth0 parent 1:{k} handle {k + 10}: netem {netem}",
                  f"tc filter add dev eth0 protocol ip parent 1:0 prio 1 u32 match ip dst {ip(dst)}/32 flowid 1:{k}"]
    return lines


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("netem_csv", nargs="?")
    p.add_argument("--mixes-per-layer", type=int, default=3)
    p.add_argument("--gateways", type=int, default=2)
    p.add_argument("--subnet", default="172.30.0")
    p.add_argument("--project", default="nym-netem")
    p.add_argument("--processing-ms", type=float, default=0.0,
                   help="constant added to every hop (node processing); mainnet shows ~100 ms+ per hop")
    p.add_argument("--jitter", action="store_true",
                   help="also emulate per-pair jitter (sd/2, normal); off by default because it reorders TCP segments")
    p.add_argument("--clear", action="store_true", help="remove all shaping instead of applying")
    p.add_argument("--show", metavar="NODE", help="print the rules for one node and exit")
    a = p.parse_args()

    names = node_names(a.mixes_per_layer, a.gateways)
    ip = lambda i: f"{a.subnet}.{10 + i}"
    if a.clear:
        for i, name in names.items():
            docker_sh(f"{a.project}-{name}", "tc qdisc del dev eth0 root 2>/dev/null || true", check=False)
        print(f"cleared shaping on {len(names)} nodes")
        return
    if not a.netem_csv:
        sys.exit("netem.csv required (or --clear)")

    delays = defaultdict(dict)  # src -> {dst: (delay, jitter)}
    with open(a.netem_csv) as f:
        for r in csv.DictReader(f):
            s, d = int(r["from"]), int(r["to"])
            if s in names and d in names:
                delays[s][d] = (float(r["delay_ms"]), float(r["jitter_ms"]))
    missing = [names[i] for i in names if i not in delays]
    if missing:
        sys.exit(f"netem.csv has no rows for {missing}: layout mismatch?")

    if a.show:
        src = next(i for i, n in names.items() if n == a.show)
        print("\n".join(rules_for(src, delays[src], ip, a.processing_ms, a.jitter)))
        return

    applied, total_rules = 0, 0
    for src, name in names.items():
        script = "\n".join(rules_for(src, delays[src], ip, a.processing_ms, a.jitter))
        res = docker_sh(f"{a.project}-{name}", script, check=False)
        if res.returncode != 0:
            sys.exit(f"{name}: tc failed:\n{res.stderr}")
        applied += 1
        total_rules += len(delays[src])
    ms = [v[0] for d in delays.values() for v in d.values()]
    print(f"applied netem on {applied} nodes, {total_rules} pair rules; one-way delay "
          f"min {min(ms):.1f} / median {sorted(ms)[len(ms)//2]:.1f} / max {max(ms):.1f} ms"
          + (f" (+{a.processing_ms} ms processing per hop)" if a.processing_ms else ""))


if __name__ == "__main__":
    main()
