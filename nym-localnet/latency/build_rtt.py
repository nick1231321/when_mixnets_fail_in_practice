#!/usr/bin/env python3
"""Builds the RTTs of a localnet network from the mainnet RTTs (VerLoc) of one epoch's active set.

Each localnet node is mapped to a mainnet node of the epoch: localnet layer l takes mainnet layer l
mixnodes (sampled if the localnet has fewer per layer), the localnet gateways take sampled mainnet
entry gateways (a localnet gateway is both entry and exit). Every pair of mapped nodes then gets
one RTT, the same in both directions:

  measured-both  both nodes measured each other: the direction with the smaller minimum RTT
                 (the larger one is usually inflated by a slow measuring node)
  measured-one   only one node measured the other: that measurement
  standin        neither did: one end is replaced by the node whose measured RTTs to all common
                 peers are most like its own ("stand-in"), and the stand-in's RTT is used.
                 profile_error_ms is the median difference between the two over their common
                 peers, i.e. the expected error of the value.

VerLoc snapshots are combined per direction by the median of each statistic over the snapshots.

Outputs in --out:
  nodes.csv     localnet id, name, role, mapped mainnet node id, identity, country, ip
  rtt.csv       from,to (localnet ids), mainnet ids, min/mean/max/sd RTT in ms, source,
                stand-in and its profile error; one row per direction
  netem.csv     from,to, delay_ms, jitter_ms: the one-way delay to add on `from`'s egress
                towards `to` (RTT/2 of --stat), and its jitter (sd/2)
  summary.json  inputs, mapping, counts per source, profile errors, hold-out validation

Usage (where the observatory data is, e.g. amazone):
  python3 build_rtt.py --data ~/nym-observatory/data --epoch 34155 \\
      --mixes-per-layer 20 --gateways 10 --out out/epoch34155
  python3 build_rtt.py --data ~/nym-observatory/data --epoch best ...   # best-covered epoch
"""

import argparse
import csv
import glob
import gzip
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

LAYERS = 3
STATS = ("min", "mean", "max", "sd")
MIN_COMMON_PEERS = 50


def load(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def load_verloc(data):
    """{(a, b): [min, mean, max, sd]} measured by a towards b, median over all snapshots."""
    per_direction = {}
    snapshots = sorted(glob.glob(f"{data}/verloc/*.json.gz"))
    if not snapshots:
        raise SystemExit(f"no VerLoc snapshots in {data}/verloc")
    for path in snapshots:
        v = load(path)
        by_identity = {identity: int(node) for node, identity in v["node_identities"].items()}
        for src, row in v["verloc"].items():
            for peer, *values in row.get("results") or []:
                dst = by_identity.get(peer)
                if dst is None or dst == int(src) or not values[1]:
                    continue
                per_direction.setdefault((int(src), dst), []).append(values)
    return {k: list(np.median(np.array(x, dtype=float), axis=0)) for k, x in per_direction.items()}, snapshots


def load_described(data):
    """node id -> (identity, country, ip) from the latest `described` snapshot."""
    nodes = {}
    for n in load(sorted(glob.glob(f"{data}/described/*.json.gz"))[-1])["nodes"]:
        desc = n["description"]
        host = desc["host_information"]
        nodes[n["node_id"]] = (
            host["keys"]["ed25519"],
            (desc.get("auxiliary_details") or {}).get("location") or "",
            (host.get("ip_address") or [""])[0],
        )
    return nodes


def epoch_files(data):
    return sorted(glob.glob(f"{data}/epoch/*.json.gz"))


def active_set(path):
    rs = load(path)["rewarded_set"]
    return rs["epoch_id"], {k: rs[k] for k in ("layer1", "layer2", "layer3", "entry_gateways", "exit_gateways")}


def coverage(rs, known):
    links = [(a, b) for x, y in (("entry_gateways", "layer1"), ("layer1", "layer2"), ("layer2", "layer3"),
                                 ("layer3", "exit_gateways"))
             for a in rs[x] for b in rs[y] if a != b]
    return sum(known(a, b) for a, b in links) / len(links)


def pick_epoch(data, wanted, known):
    files = epoch_files(data)
    if wanted == "best":
        scored = [(coverage(active_set(f)[1], known), f) for f in files]
        return max(scored)[1]
    for f in files:
        if active_set(f)[0] == int(wanted):
            return f
    raise SystemExit(f"epoch {wanted} not collected; have {[active_set(f)[0] for f in files]}")


class Rtts:
    """Pair RTTs over all mainnet nodes, with stand-ins for unmeasured pairs."""

    def __init__(self, directed):
        self.directed = directed
        ids = sorted({n for pair in directed for n in pair})
        self.index = {n: i for i, n in enumerate(ids)}
        self.ids = np.array(ids)
        # symmetric matrix of the minimum RTT (smaller direction), NaN where unmeasured
        n = len(ids)
        self.min = np.full((n, n), np.nan)
        for (a, b), values in directed.items():
            i, j = self.index[a], self.index[b]
            if np.isnan(self.min[i, j]) or values[0] < self.min[i, j]:
                self.min[i, j] = self.min[j, i] = values[0]
        self.measured = ~np.isnan(self.min)

    def known(self, a, b):
        return a in self.index and b in self.index and self.measured[self.index[a], self.index[b]]

    def measured_pair(self, a, b):
        """(values, source) of a measured pair, or None."""
        ab, ba = self.directed.get((a, b)), self.directed.get((b, a))
        if ab and ba:
            return (ab if ab[0] <= ba[0] else ba), "measured-both"
        if ab or ba:
            return (ab or ba), "measured-one"
        return None

    def profile_error(self, node, candidates, exclude):
        """Median |RTT(node, p) - RTT(s, p)| over common peers p, for each candidate s."""
        i = self.index[node]
        rows = self.min[[self.index[s] for s in candidates]]
        diff = np.abs(rows - self.min[i])
        for e in exclude:
            if e in self.index:
                diff[:, self.index[e]] = np.nan
        common = np.sum(~np.isnan(diff), axis=1)
        with np.errstate(all="ignore"):
            err = np.nanmedian(np.where(common[:, None] > 0, diff, 0.0), axis=1)
        err[common < MIN_COMMON_PEERS] = np.inf
        return err

    def standin(self, a, b, hidden=frozenset()):
        """Best (values, stand-in, replaced node, profile error) for the unmeasured pair a-b."""
        best = None
        for x, y in ((a, b), (b, a)):  # replace x, keep y
            if x not in self.index or y not in self.index:
                continue
            j = self.index[y]
            cands = [int(s) for s in self.ids[self.measured[j]]
                     if s not in (x, y) and frozenset((s, y)) not in hidden]
            if not cands:
                continue
            err = self.profile_error(x, cands, exclude=(x, y))
            k = int(np.argmin(err))
            if np.isfinite(err[k]) and (best is None or err[k] < best[3]):
                s = cands[k]
                values = self.measured_pair(s, y)[0]
                best = (values, s, x, float(err[k]))
        return best


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", required=True, help="observatory data directory (verloc/, epoch/, described/)")
    parser.add_argument("--epoch", default="best", help="epoch id, or 'best' for the best-covered collected epoch")
    parser.add_argument("--mixes-per-layer", type=int, default=20)
    parser.add_argument("--gateways", type=int, default=10)
    parser.add_argument("--stat", choices=("min", "mean"), default="mean",
                        help="RTT statistic for the netem delay (default mean; jitter is sd/2 either way)")
    parser.add_argument("--validate", type=int, default=500,
                        help="measured pairs to hide and re-fill with stand-ins to check the method (0: skip)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    directed, snapshots = load_verloc(args.data)
    rtts = Rtts(directed)
    described = load_described(args.data)
    epoch_file = pick_epoch(args.data, args.epoch, rtts.known)
    epoch_id, rs = active_set(epoch_file)

    # localnet node -> mainnet node
    mapping = []  # (local id, name, role, mainnet id)
    for layer in range(1, LAYERS + 1):
        pool = rs[f"layer{layer}"]
        if args.mixes_per_layer > len(pool):
            raise SystemExit(f"layer {layer} has only {len(pool)} mixnodes in epoch {epoch_id}")
        chosen = pool if args.mixes_per_layer == len(pool) else rng.sample(pool, args.mixes_per_layer)
        for k, node in enumerate(chosen):
            local = (layer - 1) * args.mixes_per_layer + k + 1
            mapping.append((local, f"mix{local}", f"layer{layer}", node))
    gateways = rs["entry_gateways"]
    if args.gateways > len(gateways):
        raise SystemExit(f"only {len(gateways)} entry gateways in epoch {epoch_id}")
    for j, node in enumerate(rng.sample(gateways, args.gateways), start=1):
        mapping.append((LAYERS * args.mixes_per_layer + j, f"gw{j}", "gateway", node))
    by_local = {m[0]: m for m in mapping}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "nodes.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["local_id", "name", "role", "mainnet_id", "identity", "country", "ip"])
        for local, name, role, node in mapping:
            w.writerow([local, name, role, node, *described.get(node, ("", "", ""))])

    counts, errors, rows = {}, [], []
    pair_values = {}
    for i, (la, _, _, a) in enumerate(mapping):
        for lb, _, _, b in mapping[i + 1:]:
            found = rtts.measured_pair(a, b)
            if found:
                values, source = found
                standin, error = "", ""
            else:
                fill = rtts.standin(a, b)
                if fill is None:
                    raise SystemExit(f"no stand-in for {a}-{b}: neither node has enough measured peers")
                values, s, replaced, error = fill
                source, standin = "standin", f"{s} for {replaced}"
                errors.append(error)
                error = round(error, 3)
            counts[source] = counts.get(source, 0) + 1
            pair_values[(la, lb)] = values
            for x, y, mx, my in ((la, lb, a, b), (lb, la, b, a)):
                rows.append([x, y, mx, my, *(round(float(v), 4) for v in values), source, standin, error])

    with (out / "rtt.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["from", "to", "from_mainnet", "to_mainnet", "min_ms", "mean_ms", "max_ms", "sd_ms",
                    "source", "standin", "profile_error_ms"])
        w.writerows(sorted(rows))
    col = STATS.index(args.stat)
    with (out / "netem.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["from", "to", "delay_ms", "jitter_ms"])
        for (la, lb), values in sorted(pair_values.items()):
            delay, jitter = round(values[col] / 2, 3), round(values[3] / 2, 3)
            w.writerow([la, lb, delay, jitter])
            w.writerow([lb, la, delay, jitter])

    # hold-out validation: hide measured pairs among the mapped nodes' links, re-fill, compare
    validation = None
    if args.validate:
        mainnet = [m[3] for m in mapping]
        pool = [(a, b) for i, a in enumerate(mainnet) for b in mainnet[i + 1:] if rtts.known(a, b)]
        sample = rng.sample(pool, min(args.validate, len(pool)))
        abs_err, rel_err, profile = [], [], []
        for a, b in sample:
            i, j = rtts.index[a], rtts.index[b]
            truth = rtts.min[i, j]
            rtts.min[i, j] = rtts.min[j, i] = np.nan
            rtts.measured[i, j] = rtts.measured[j, i] = False
            fill = rtts.standin(a, b, hidden=frozenset({frozenset((a, b))}))
            rtts.min[i, j] = rtts.min[j, i] = truth
            rtts.measured[i, j] = rtts.measured[j, i] = True
            if fill:
                abs_err.append(abs(fill[0][0] - truth))
                rel_err.append(abs(fill[0][0] - truth) / truth)
                profile.append(fill[3])
        validation = {
            "pairs": len(abs_err),
            "abs_error_ms": {"median": round(float(np.median(abs_err)), 2), "p90": round(float(np.percentile(abs_err, 90)), 2)},
            "rel_error": {"median": round(float(np.median(rel_err)), 4), "p90": round(float(np.percentile(rel_err, 90)), 4)},
            # error of the re-filled value by the profile error of its stand-in
            "by_profile_error": {},
        }
        abs_err, profile = np.array(abs_err), np.array(profile)
        for lo, hi in ((0, 1), (1, 2), (2, 5), (5, np.inf)):
            sel = (profile >= lo) & (profile < hi)
            if sel.any():
                validation["by_profile_error"][f"{lo}-{hi} ms"] = {
                    "pairs": int(sel.sum()), "abs_error_median_ms": round(float(np.median(abs_err[sel])), 2),
                    "abs_error_p90_ms": round(float(np.percentile(abs_err[sel], 90)), 2)}

    total = sum(counts.values())
    summary = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "epoch": epoch_id,
        "epoch_file": Path(epoch_file).name,
        "verloc_snapshots": [Path(s).name for s in snapshots],
        "mixes_per_layer": args.mixes_per_layer,
        "gateways": args.gateways,
        "seed": args.seed,
        "netem_stat": args.stat,
        "pairs": total,
        "sources": counts,
        "standin_profile_error_ms": ({"median": round(float(np.median(errors)), 2), "max": round(float(max(errors)), 2)}
                                     if errors else None),
        "validation": validation,
        "mapping": {name: node for _, name, _, node in mapping},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    print(f"epoch {epoch_id}: {len(mapping)} nodes ({LAYERS}x{args.mixes_per_layer} mixnodes, {args.gateways} gateways), "
          f"{total} pairs -> {out}")
    for source in ("measured-both", "measured-one", "standin"):
        print(f"  {source:14s} {counts.get(source, 0):6d} ({100 * counts.get(source, 0) / total:.2f}%)")
    if errors:
        print(f"  stand-in profile error: median {summary['standin_profile_error_ms']['median']} ms, "
              f"max {summary['standin_profile_error_ms']['max']} ms")
    if validation:
        print(f"  hold-out ({validation['pairs']} measured pairs re-filled): error median "
              f"{validation['abs_error_ms']['median']} ms / {100 * validation['rel_error']['median']:.1f}%, "
              f"p90 {validation['abs_error_ms']['p90']} ms / {100 * validation['rel_error']['p90']:.1f}%")
        for bucket, v in validation["by_profile_error"].items():
            print(f"    stand-in profile error {bucket:9s}: {v['pairs']:4d} pairs, value error median "
                  f"{v['abs_error_median_ms']} ms, p90 {v['abs_error_p90_ms']} ms")


if __name__ == "__main__":
    main()
