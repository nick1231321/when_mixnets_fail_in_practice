# Mainnet RTTs for the localnet

`build_rtt.py` gives every pair of localnet nodes the round-trip time of a real mainnet node pair,
for emulating the network delays of mainnet (e.g. with `tc netem`).

## Data

The input is the data collected by [`../observatory/collect.py`](../observatory/collect.py):

- `verloc/`: VerLoc RTTs, measured every 12 h by every node to every other node
- `epoch/`: the active set of every epoch
- `described/`: node identities, countries and IPs

It lives on amazone in `~/nym-observatory/data`. Run the script there, or copy the data first:

```sh
rsync -a amazone:nym-observatory/data/ observatory-data/
```

## Usage

```sh
python3 build_rtt.py --data ~/nym-observatory/data --epoch 34155 \
    --mixes-per-layer 20 --gateways 10 --out out/epoch34155
```

The options:

- `--epoch best` picks the collected epoch whose routes have the most measured links.
- `--stat min|mean` picks the RTT statistic used for the netem delay (default `mean`).
- `--seed` chooses which mainnet nodes are sampled.
- `--validate N` sets the number of measured pairs used in the hold-out check (default 500).

Python 3 with numpy is needed. A run takes about 1–2 minutes.

## Mapping

The localnet uses the node ids of [`../distributed/deploy.py`](../distributed/deploy.py). Mixnode
`i` sits on layer `(i-1) // mixes_per_layer + 1`, and the gateways follow the mixnodes.

- **Mixnodes:** localnet layer `l` takes the epoch's mainnet layer `l` mixnodes, sampled if there
  are fewer per layer.
- **Gateways:** a localnet gateway acts as both entry and exit, and each one takes a sampled
  mainnet entry gateway.

## How each RTT is chosen

Every pair of mapped nodes gets one RTT, used for both directions:

| `source` | Meaning |
|---|---|
| `measured-both` | Both nodes measured each other. The direction with the smaller minimum RTT is used, because a slow measuring node inflates its own measurements. |
| `measured-one` | Only one node measured the other. Its measurement is used; both directions agree within about 2% (median). |
| `standin` | Neither node measured the other. One end is replaced by the node whose RTTs to all common peers are most similar to its own (its "stand-in"), and the stand-in's RTT is used. |

For a stand-in, `profile_error_ms` is the median RTT difference between the node and its stand-in
over their common peers.

VerLoc snapshots are combined per direction by taking the median of each statistic.

## Outputs

| File | Contents |
|---|---|
| `nodes.csv` | Localnet id, name and role, plus the mapped mainnet node's id, identity, country and IP |
| `rtt.csv` | One row per direction: min/mean/max/sd RTT (ms), source, stand-in and its profile error |
| `netem.csv` | `delay_ms` and `jitter_ms` (half of the RTT statistic and of its sd) to add on the egress of `from` towards `to`; the delays of both directions add up to the RTT |
| `summary.json` | Inputs, mapping, counts per source, stand-in errors and the hold-out validation |

## Validation

The hold-out check hides measured pairs, fills them with stand-ins and compares the result with the
measurement. For epoch 34155 (3×20 mixnodes, 10 gateways):

- **Sources:** 2,011 pairs measured both ways, 388 measured one way, 16 stand-ins (0.66%). The
  stand-ins' profile error is 0.32 ms median and 3.4 ms at most.
- **Hold-out (500 pairs):** the re-filled value is off by 0.5 ms (0.9%) median and 5.2 ms (8.7%)
  at p90.
- **The profile error predicts the value error:** stand-ins with a profile error under 1 ms give
  a median value error of 0.5 ms, and those with 2–5 ms give 7 ms.
