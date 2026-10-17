# Reproducing the packet collection

How to collect the flow-matching datasets: on the **localnet** (a scaled copy of the Nym network in Docker, observed at the gateways and on the wire) and on the **live network** (our own clients, observed on the wire). Everything below runs from `nym-localnet/` in this repository.

## 1. Localnet

### 1.1 What you get

A mixnet of 60 mix nodes (3 layers × 20) and 2 gateways, each in its own network namespace. Every node is mapped to a real node of Nym's active set and every pair of nodes gets the delay measured between the two real nodes (VerLoc), installed with `tc netem`. Clients created by a load generator exchange files through it; each run is recorded from two observation points:

| view | source | files per run |
|---|---|---|
| gateway | the gateways' own wire log (`MIXCORR_WIRE_DIR`, set in the compose file) | `gateway_wire/wire-<gateway>.csv.gz`: `wall_ns,direction,client,size` per Sphinx packet |
| wire | tcpdump in a sidecar container sharing each gateway's network namespace | `capture/<gateway>.frames.csv.gz` (WebSocket frames), `.segments.csv.gz` (TCP segments), `.pcap` (raw, every N-th run) |

plus `clients.csv` (who talks to whom), `messages.csv` (every file's send and receive time), `run.json` (timeline and wall-clock offset), `monitor.csv` + `verdict.json` (machine load, OK / SUSPECT / INVALID).

### 1.2 Requirements

- Linux (recommended, root or Docker group) or macOS with Docker Desktop. 10+ cores and 16 GB RAM; the collection keeps the machine below 60 % of its measured capacity.
- Docker with compose v2, Python 3.9+ with `pandas`, `numpy`, `scikit-learn`, `joblib`; Rust toolchain for the load generator.
- Disk: ~6 MB per run without pcaps; a full pcap is ~350 MB per run.

### 1.3 Build

```sh
cd nym-localnet
./localnet.sh build                                        # nym-node image (nym-localnet:latest), from the repo root
cargo build --release --manifest-path self-test/Cargo.toml # nym-load-test, nym-self-test
```

### 1.4 Delay tables (optional, already in the repo)

`netem/data/l3x20g2/` holds the node mapping and delays used for our collections (epoch 34211, VerLoc snapshots of 8–13 Oct 2026). To rebuild them from other observatory data or another epoch:

```sh
python3 latency/build_rtt.py --data ~/nym-observatory/data --epoch 34211 \
    --mixes-per-layer 20 --gateways 2 --validate 0 --out netem/data/l3x20g2
```

### 1.5 Start the netem localnet

```sh
netem/vm_sysctl.sh                                                   # once per Docker start: ARP table limits (required beyond ~30 nodes)
python3 netem/gen_compose.py --mixes-per-layer 20 --gateways 2 --out docker-compose.netem.yml
docker compose -p nym-netem -f docker-compose.netem.yml up -d        # 63 containers; first start generates node keys (~2 min)
python3 netem/apply_netem.py netem/data/l3x20g2/netem.csv --mixes-per-layer 20 --gateways 2
./self-test/target/release/nym-self-test data-netem/network.json --size 1000   # must print SUCCESS
```

Checks before any collection:

- `docker exec nym-netem-mix1 tc qdisc show dev eth0 | grep -c netem` prints 61 (a container restart wipes the rules; re-run `apply_netem.py`).
- The gateways write `data-netem/wire/wire-*.csv` (the gateway view; `MIXCORR_WIRE_DIR=/localnet/wire` is set in the compose file).
- Every day at the nodes' key rotation (~12:30 UTC) the running nodes stop delivering until restarted; the runner (1.7) repairs this by itself, a manual run needs `docker restart` of the node containers, `apply_netem.py` again, and the self-test. See `netem/README.md` for the node tuning and the ARP issue.

### 1.6 Plan the runs

Two planners write a `plan.csv` (one row per run: traffic settings per client or group, path-selection strategy, number of clients, seed), shuffled so conditions are interleaved in time:

```sh
# every client with its own NymVPN-style profile (mixing delay, sending rate, cover level, on/off switches),
# training on the app's values and test sets with unseen values
python3 sweep/design_hetero.py --out ~/flow-matching-data/hetero/plan.csv

# one condition per run: path-selection strategies x traffic factors
python3 sweep/design.py --strategies khf --out ~/flow-matching-data/khf/plan.csv
```

`design.py --session message|connection` sets the path-selection session scope (default `message`: a session is one file, as in Rahimi's model; `connection`: never reset). Both planners size each run so that the clients' total packet rate stays at `--clean-load` (0.6) × `--capacity` (1,600 packets/s, our machine; calibrate yours by raising the client count until delivery drops below 99 %).

### 1.7 Collect

```sh
python3 sweep/run_queue.py --plan ~/flow-matching-data/hetero/plan.csv --out ~/flow-matching-data/hetero/main \
    --capture-every 1 --extract-wire --keep-pcap-every 20 --min-free-gb 20
```

Per run the runner writes the config, checks netem and disk, starts tcpdump next to both gateways, runs `nym-load-test` under the resource monitor, stops tcpdump, cuts this run's slice out of the gateways' log, extracts the wire view from the pcaps and keeps the raw pcap every 20th run, and appends a row to `manifest.csv` with delivery, verdict and status (`clean` / `degraded` / `failed`). Only `clean` runs (≥ 99 % delivery, host not saturated) are used.

- `touch OUT/PAUSE` pauses after the current run (`rm` to continue); `touch OUT/STOP` stops; restarting the same command resumes where it stopped. `--retry-failed` re-runs failed and degraded runs. `--only r00001,r00002` runs a selection (pilot).
- After a key rotation, or after a run that delivered nothing, the runner restarts the nodes, re-applies netem, tests delivery and repeats the run once; if delivery cannot be restored it pauses itself.
- Keep the machine otherwise idle: a CPU spike during a run marks it degraded (it is redone later).
- Raw logs only: nothing is exported or scored during the collection.

`sweep/pack_upload.py --collection DIR --out UPLOAD_DIR` packs finished runs into zips for off-machine storage; `sweep/sync_runs.py` mirrors a collection to an ssh host and moves pcaps off the machine.

### 1.8 Process

```sh
python3 sweep/build_features.py --sweep ~/flow-matching-data/hetero/main --out ~/flow-matching-data/hetero/features \
    --views gateway,wire --workers 6 --splits
```

For every clean run and view this crops each client's streams to the sending window, pairs every initiator with every responder, and writes the drift features of `linkobs/tools/pair_features.py` per candidate pair and window (`pairs_gateway.csv`, `pairs_wire.csv`), plus `splits.csv` (train / val / test by run). The wire view's connections are matched to clients by their send times against the gateway log, for the labels only. Train and test with `~/flow-matching-data/playground/run_model.py` (see its README). Run processing while the collection is paused; it competes for CPU.

## 2. Live network (mainnet)

Our own clients on the public Nym network, paired with each other through two public entry gateways, observed on the wire at the client machine. No gateway is run or modified and only this machine's own connections are captured, so no other user's traffic is observed; the load on the network is the clients we run (10 per run, as MixMatch did).

### 2.1 Requirements

- A machine with a public internet connection, `tcpdump` and sudo. A datacenter VPS gives a cleaner link than a home line; both are valid observers.
- `nym-load-test` built from this repository (same as 1.3). The gateways grant free bandwidth to new clients; if a gateway refuses, pick another with `--seed`.

### 2.2 One run

```sh
sudo -v
linkobs/mainnet/run_wire.sh ~/flow-matching-data/mainnet/runs/m001 --seed 1 --clients 10 --duration 240
```

`run_wire.sh` picks two entry gateways from the nym-api (`pick_gateways.py`: current rewarded set, websocket port, performance ≥ 0.95, seeded), starts tcpdump on the default interface for the gateways' client ports (9000 plain `ws://`, 9001 `wss://`), runs the load test under the resource monitor, stops the capture and extracts the wire view (`capture/link.frames.csv.gz` for plain WebSocket, `capture/link.segments.csv.gz` for TLS). The run folder has the same files as a localnet run except the gateway view. Options after the output directory go to `pick_gateways.py` (`--strategy khf:1,2`, `--groups` for per-group traffic settings, `--duration`).

### 2.3 What to watch

- **Connection time.** Clients connect minutes apart on the live network; the features use the window in which all clients are up (`run.json` timeline, `client_wire_export.py --settle-s`).
- **Packet loss.** The live network loses packets (0.5–16 % in our October runs). The gateway's 70-byte confirmations (one per sent packet) and the 51-byte acks are both visible on the wire, so loss per run is `1 − acks / confirmations`; above ~2 % the k-th/k-th alignment degrades.
- **Clock.** Both ends of every pair are captured on one machine, so there is no clock offset. Capturing sender and receiver on two machines needs clocks synchronised to well under the mixing delay (chrony against the same NTP servers).
- **Ethics.** Only our own clients are involved; their traffic is indistinguishable from regular Nym traffic and is never routed for anyone else. Keep the client count small (10) and the runs short.

## 3. Layout of a collection

```
<collection>/
  plan.csv                      all planned runs
  main/manifest.csv             every finished run: settings, delivery, verdict, status
  main/runs/<run_id>/
    config.toml  clients.csv  messages.csv  run.json  load_test.log  monitor.csv  verdict.json
    gateway_wire/wire-<gateway>.csv.gz          (localnet only)
    capture/<gateway>.frames.csv.gz  <gateway>.segments.csv.gz  [<gateway>.pcap]  capture.log
  features/pairs_<view>.csv  splits.csv          (after build_features.py)
```
