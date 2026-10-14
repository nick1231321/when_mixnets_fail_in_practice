# MixMatch flow matching on the localnet

Measures how the path-selection strategies (baseline, K-HF, K/W, α-SS) and the client's mixing
profile change a **gateway adversary's** ability to match a sender's flow to a receiver's flow by
timing — the MixMatch attack (Oldenburg, Juarez, Argones Rúa, Diaz, PoPETs 2024.2) — using real
traffic on the localnet with emulated mainnet latencies ([`../netem/`](../netem/)).

Matching is done by **MixMatch's own statistical classifier** (their unmodified Octave code), not a
reimplementation. This directory only generates the traffic and converts it to their input format.

## Threat model and observation point

The adversary is the clients' **gateways**. A gateway sees every Sphinx packet crossing a
client↔gateway link — timestamp and wire size, cover traffic included, content not — and cannot
tell real from cover. That is exactly MixMatch's observation point. Our gateway logs it directly
(`MIXCORR_WIRE_DIR` set on the gateway services → `data-netem/wire/wire-*.csv`, columns
`wall_ns,direction,client,size`; see
[`../../common/nymsphinx/chunking/src/instrumentation.rs`](../../common/nymsphinx/chunking/src/instrumentation.rs)
and the gateway hooks in
[`../../gateway/src/node/client_handling/websocket/connection_handler/authenticated.rs`](../../gateway/src/node/client_handling/websocket/connection_handler/authenticated.rs)).

## Why this needs the netem localnet

A flow is matched through the **per-packet delay distribution**. On mainnet the spread comes mostly
from route diversity (routes differ 1–350 ms one way), more than from the mixing delay itself. The
ordinary localnet has ~5 ms on every route (zero route variance), so the netem variant is required
to reproduce real route latencies.

## Pipeline

1. **run** — `export_their_format.py run` launches one load test per condition (strategy × mixing
   delay) with the `nym-load-test` generator: `--pairs` matched sender↔receiver client pairs
   (`pairing = "random-pairs"`), Poisson sending, cover traffic on, `[client]
   average_packet_delay_ms` = the mixing delay. The gateways log the wire automatically. Each run
   writes `clients.csv` (maps each client's `wire_tag` → index and partner) and `run.json`. Clients
   are ephemeral, so a run's tags are unique and isolate its packets in the shared, accumulating
   gateway wire log.

2. **export** — each run is converted to MixMatch's statistical-classifier input: one folder per
   communicating pair (A = initiator, B = responder) holding the six timestamp files their
   `perform_experiment_real_data_alt_delay_characteristic.m` loads
   (`payload_{initiator,responder}_{to,from}_gateway.txt`, `ack_{initiator,responder}_from_gateway.txt`,
   ns, one per line), plus `flowpairs.txt`. Received packets are split into payload vs ack by size
   (the two dominant modes), as MixMatch splits gateway→client packets.

3. **score** — MixMatch's classifier on the exported data. It fits a **single Gaussian delay model**
   (mean μ, std σ) from the true pairs — two scalars, no training — then for each candidate pair
   (sender i, receiver j) merges the two timestamp streams, aligns them (`align_sequences`), and
   sums the per-packet log-likelihood `log N(rx − tx; μ, σ)` over data and acks
   (`gaussian_delay_sequences_match_loglikelihood`). True match = i == j. Reported: **TPR at
   FPR = 10⁻²** (their headline metric) and balanced accuracy, per sequence length.

## Usage

```sh
cd nym-localnet            # netem localnet up and delays applied (see ../netem/README.md)

# 1-2) run conditions and export each to MixMatch's format
python3 mixmatch/export_their_format.py run --out mixmatch/out/cmp \
    --strategies baseline khf:1,2 --mixing 50 --pairs 15 --duration 180
# (prints the exact octave command per run; export lands in <run_dir>/mixmatch_export)

# or export an existing run on its own
python3 mixmatch/export_their_format.py export mixmatch/out/cmp/baseline_mix50 /tmp/export_baseline

# 3) score with MixMatch's own Octave classifier (seed = 3 x mixing_ms in seconds)
octave-cli --eval "run_their_matching('/tmp/export_baseline', 0.15)"
```

Scoring needs the MixMatch statistical-classifier repo on the Octave path (their `align_sequences.m`
and `gaussian_delay_sequences_match_loglikelihood.m`, unmodified) plus a `run_their_matching.m`
driver that replicates their merge→align→score loop all-vs-all and reports TPR@FPR. `average_mixnet_delay`
(their alignment seed / delay lower bound) is the one network-specific constant — set it to 3×mixing
in seconds, e.g. 0.15 for 50 ms/hop. `normpdf` comes from octave-forge `statistics`, or drop an exact
`normpdf.m` on the path to avoid the package.

MixMatch's FPR = 10⁻² wants N(N−1) ≳ 10⁴ false pairs; with N sessions that means N ≳ 100 for a tight
estimate. Small runs (N = 15) still show the ranking but the FPR grid is coarse.

## Findings so far (3×20 netem localnet, 50 ms mixing, N = 15 pairs)

- **Validation:** the gateway-logged baseline reproduces MixMatch's regime — full-sequence
  TPR@FPR=10⁻² = 1.0, strong separation (balanced accuracy ≈ 0.94–0.97 even at length 100). μ ≈
  155 ms matches 3×50 ms mixing.
- **Strategy effect:** K-HF `1,2` does **not** degrade anonymity vs baseline — full-length
  TPR@FPR=10⁻² = 1.0 for both; at truncated lengths K-HF is if anything slightly easier to match.
  Path selection changes which nodes carry a flow, but end-to-end flow matching keys on timing,
  which is orthogonal to hop choice. The lever that moves the TPR is the mixing/timing, not the route.
