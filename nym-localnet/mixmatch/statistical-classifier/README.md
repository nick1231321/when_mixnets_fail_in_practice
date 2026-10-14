# MixMatch statistical classifier (original) + our conversion pipeline

This is the **original MixMatch statistical flow-matcher**, used unmodified to score our localnet
experiments. We do not reimplement the attack; we convert our data into the format it expects and
run their code.

## What is original vs. ours

- [`upstream/`](upstream/) — a **verbatim copy** of the authors' repository, unmodified:
  <https://github.com/mixnet-correlation/mixmatch_statistical_classifier> (commit `7167670`,
  2024-01-11), the statistical classifier from *MixMatch: Flow Matching for Mixnet Traffic*
  (Oldenburg, Juarez, Argones Rúa, Diaz, PoPETs 2024.2). It keeps the authors' own `LICENSE`
  (**GNU GPLv3**) and `README.md`; those files are under their license, not this repo's.
  The matching itself is `upstream/align_sequences.m` and
  `upstream/gaussian_delay_sequences_match_loglikelihood.m`, driven by
  `upstream/perform_experiment_real_data_alt_delay_characteristic.m`.

- `run_their_matching.m` — **ours**, a thin driver. It calls the upstream functions **unmodified**
  through the same merge → sort → `align_sequences` → `gaussian_delay_..._loglikelihood` loop as
  their `perform_experiment_real_data_alt_delay_characteristic.m`, but reads our exported sessions,
  fits μ/σ on the true pairs, scores all-vs-all, and reports **TPR@FPR=10⁻²** + balanced accuracy
  instead of writing their `.oct` result files. The matching algorithm is entirely theirs.

- `normpdf.m` — **ours**, a one-line shim giving the standard Gaussian pdf, so the upstream code
  runs without installing octave-forge `statistics`. Same definition the toolbox provides; it does
  not change the algorithm. (Install the `statistics` package instead if you prefer.)

## How our data reaches their code

The attack observes, per communicating pair, six timestamp sequences at the gateways. Our gateways
log the wire ([`../../../common/nymsphinx/chunking/src/instrumentation.rs`](../../../common/nymsphinx/chunking/src/instrumentation.rs)),
and [`../export_their_format.py`](../export_their_format.py) converts one run into their input:

```
run's gateway wire logs + clients.csv
          │  export_their_format.py
          ▼
<export_dir>/
  flowpairs.txt                      # list of session folders
  session_0000/
    payload_initiator_to_gateway.txt     payload_responder_to_gateway.txt
    payload_initiator_from_gateway.txt   payload_responder_from_gateway.txt
    ack_initiator_from_gateway.txt       ack_responder_from_gateway.txt
  session_0001/ ...
```

These are exactly the files their `perform_experiment_real_data_alt_delay_characteristic.m` loads
(timestamps in nanoseconds, one per line).

## How to use

```sh
# 1) generate + export a run (from nym-localnet/, netem localnet up; see ../../netem/README.md)
python3 mixmatch/export_their_format.py run --out mixmatch/out/cmp \
    --strategies baseline khf:1,2 --mixing 50 --pairs 15 --duration 180
#   -> exports to mixmatch/out/cmp/<cond>/mixmatch_export and prints the octave command per run

# or export an existing run on its own:
python3 mixmatch/export_their_format.py export mixmatch/out/cmp/baseline_mix50 /tmp/export_baseline

# 2) score it with the original classifier (needs octave; macOS: brew install octave)
cd mixmatch/statistical-classifier
octave-cli --eval "run_their_matching('/tmp/export_baseline', 0.15)"
```

`run_their_matching(database_path, average_mixnet_delay)`:
- `database_path` — the export dir (the one with `flowpairs.txt`).
- `average_mixnet_delay` — their one network-specific constant (alignment seed / delay lower bound),
  in **seconds**. Set it to `3 × mixing_ms / 1000`, e.g. `0.15` for 50 ms/hop. (Their file hardcodes
  `0.354` for their own topology's propagation; on our emulated network the right value is the mixing
  term — it shifts the estimated μ but not the full-length result.)

It prints the estimated μ/σ and, for sequence lengths 100 / 500 / full, `TPR@FPR=1e-2` and balanced
accuracy. True matches are the diagonal of the all-vs-all score matrix.

## Running their full upstream experiment instead

To run their original driver (with their train/val/test partition files and `.oct` output), add the
three `flowpairs_{train,val,test}.txt` partition files to the export dir and call
`upstream/perform_experiment_real_data_alt_delay_characteristic.m` directly (see `upstream/README.md`).
`run_their_matching.m` is the shortcut for the all-vs-all TPR without that scaffolding.
