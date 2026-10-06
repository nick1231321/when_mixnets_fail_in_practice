# Nym localnet: path selection self-tests

A Docker-based local mixnet (9 mixnodes in 3 layers, 2 gateways) plus a self-test client that
sends a message to itself through it. The client uses one of the path selection strategies
from *"When Mixnets Fail: Evaluating, Quantifying, and Mitigating the Impact of Adversarial
Nodes in Mix Networks"* (Rahimi, NDSS 2026). A verification script checks from the logs that
every route follows the configuration.

```
mixnodes  1..9   layer 1: 1 2 3   layer 2: 4 5 6   layer 3: 7 8 9
gateways  10, 11
```

## Quick start

```sh
cd nym-localnet
./localnet.sh build                                   # build the node image (once)
./localnet.sh up                                      # start the nodes, write data/network.json
cargo build --release --manifest-path self-test/Cargo.toml

./self-test/target/release/nym-self-test data/network.json --strategy khf:1,2
./localnet.sh verify --quick                          # check the routing of every strategy
```

| Command | What it does |
|---|---|
| `./localnet.sh build` | Builds the Docker image of the nodes. |
| `./localnet.sh up` | Starts the localnet and waits until `data/network.json` and the gateway (port 9000) are ready. |
| `./localnet.sh down` | Stops it and deletes its state (`data/`). |
| `./localnet.sh ps` / `logs [service]` | Container status / logs. |
| `./localnet.sh test` | Runs the default self-test once. |
| `./localnet.sh verify [...]` | Runs the verification script (see [Verifying the routing](#verifying-the-routing)). |

Path selection is implemented in the client only. After changing it, rebuild the self-test
(`cargo build --release ...`); the Docker image does not need rebuilding.

The `topology` container rewrites `data/network.json` whenever the nodes rotate their sphinx
keys, and the self-test reloads it on every topology refresh.

## The self-test

`self-test/target/release/nym-self-test` connects an ephemeral client, sends one message to its
own address and waits up to 60 s for it to come back. It prints `SUCCESS` (exit code 0) or
`FAILURE` (exit code 1).

`--strategy` picks the path selection strategy:

| `--strategy` | Strategy |
|---|---|
| `baseline` (default) | Baseline: an independent, uniformly random route per packet (Nym's default). |
| `khf:LAYERS`, e.g. `khf:1` or `khf:1,2` | K-Hops Fixed: the nodes on the given layers (comma-separated, 1–3) are picked once per session and reused by every packet; the other layers are picked per packet. |
| `kw:K`, e.g. `kw:2` | K/W: K nodes per layer are preselected per session, and every packet picks among them. |
| `alpha:ALPHA`, e.g. `alpha:0.8` | α-Sticky Selection: with probability α (in [0, 1]) a packet reuses a route already used in the session, otherwise it gets a new one. |

### Arguments

| Argument | Default | Meaning |
|---|---|---|
| `[network.json]` | `data/network.json` | Topology file (positional). |
| `--strategy STRATEGY` | `baseline` | Path selection strategy, see above. |
| `--size BYTES` | a short greeting | Message size. A sphinx packet carries ~2 KB, so `--size 100000` spreads the message over ~50 packets of the session. |
| `--real-routing R` | `strategy` | Routing of real traffic. |
| `--real-ack-routing R` | `strategy` | Routing of the acks of real traffic. |
| `--cover-routing R` | `strategy` | Routing of loop cover traffic. |
| `--cover-ack-routing R` | `strategy` | Routing of the acks of loop cover traffic. |

## Routing of each traffic class

A client sends four classes of packets. Each class has its own flag, set to one of:

- **`strategy`** (default): the route follows the path selection strategy, in the session of
  the packet. The packet is counted in the session and shapes its state (the fixed nodes of
  K-HF, the preselected nodes of K/W, the leading set of α-SS).
- **`baseline`**: an independent, uniformly random route, as in unmodified Nym. The session is
  left untouched: the packet is not counted and does not change the strategy's state.

| Flag | Traffic class | Packets | Session | Route ends at | Log kind |
|---|---|---|---|---|---|
| `--real-routing` | Real traffic | Forward data packets to the recipient | recipient | recipient's gateway | `real` |
| | | Reply SURBs carried in the message, used by the recipient to answer | recipient | our gateway | `reply-surb` |
| `--real-ack-routing` | Acks of real traffic | SURB-ACK inside each data packet, sent back by the recipient's gateway to confirm delivery | recipient | our gateway | `real-ack` |
| `--cover-routing` | Loop cover traffic | Dummy packets the client sends to itself at random intervals, so that real traffic does not stand out | our own address | our gateway | `cover` |
| `--cover-ack-routing` | Acks of loop cover traffic | SURB-ACK inside each cover packet (it needs one to look like a real packet; the client discards it) | our own address | our gateway | `cover-ack` |

Notes:

- All classes that use the strategy share **one session per peer**. The self-test sends to its
  own address, so its real traffic, acks and cover traffic all share a single session.
- Reply SURBs are the return path of real traffic, so they follow `--real-routing`.
- With the Baseline strategy no selector is created, so the routing flags have no effect.
- Two kinds of route never use the strategy: acks of replies we send through someone else's
  SURBs (that peer is anonymous, so there is no session), and replies themselves (the SURB's
  creator chose that route).

### Examples

```sh
cd nym-localnet
BIN=./self-test/target/release

# K-HF on layers 1 and 2 for every packet (all routing defaults)
$BIN/nym-self-test data/network.json --strategy khf:1,2

# the strategy only on real traffic; acks and cover traffic random as in plain Nym
$BIN/nym-self-test data/network.json --strategy khf:1,2 \
  --real-ack-routing baseline --cover-routing baseline --cover-ack-routing baseline

# the strategy only on cover traffic and its acks
$BIN/nym-self-test data/network.json --strategy kw:2 --real-routing baseline --real-ack-routing baseline

# α-SS, ~50 data packets, cover acks random
$BIN/nym-self-test data/network.json --strategy alpha:0.5 --size 100000 --cover-ack-routing baseline

# unmodified Nym routing
$BIN/nym-self-test data/network.json
```

In the SDK, the same settings are builder methods:

```rust
use nym_sdk::mixnet::{MixnetClientBuilder, PathSelectionStrategy, Routing};

let client = MixnetClientBuilder::new_ephemeral()
    .path_selection_strategy(PathSelectionStrategy::KHopsFixed { fixed_layers: vec![1, 2] })
    .real_routing(Routing::Strategy)          // the default for all four
    .real_ack_routing(Routing::Baseline)
    .cover_routing(Routing::Strategy)
    .cover_ack_routing(Routing::Baseline)
    .build()?;
```

## Reading the logs

By default the self-test prints the strategy, the routing of each class and every new session:

```
Path selection strategy: KHopsFixed { fixed_layers: [1, 2] }
Routing: real: strategy, real-ack: baseline, cover: strategy, cover-ack: strategy
```

To see every chosen route, enable debug logging for the path selection:

```sh
RUST_LOG=warn,nym_topology::path_selection=debug $BIN/nym-self-test data/network.json --strategy khf:1,2
```

| Message | Meaning |
|---|---|
| `routes will use KHopsFixed { ... }; routing real: strategy, ...` | The client's selector was created (not logged for Baseline). |
| `new session 8xTqLm3a: K-HF fixed nodes per layer [Some(2), Some(5), None]` | First packet of a session; the session's state (K-HF fixed nodes, K/W preselected nodes, α-SS α). `8xTqLm3a` is the start of the peer's address. |
| `session 8xTqLm3a packet #12 (real): K-HF route [2, 5, 8] (fixed [...])` | A route chosen by the strategy: the session's 12th counted packet, a real data packet, through mixnodes 2 → 5 → 8. |
| `session 8xTqLm3a packet #13 (real-ack): [roll 0.2103 < α=0.5 → reuse] α-SS reused route [2, 6, 7] (leading set: 9 routes)` | α-SS also prints its roll and decision. |
| `session 8xTqLm3a (cover): baseline route [3, 4, 8] (routing configured as baseline, session untouched)` | A class set to `baseline`: random route, no packet number. |
| `session 8xTqLm3a: fixed node 3 on layer 1 left the topology, replacing it with 1` | K-HF / K/W replaced a node that disappeared from `network.json`. |

To follow one class: `... 2>&1 | grep "(cover-ack)"`.

## Verifying the routing

`verify_path_selection.py` (or `./localnet.sh verify`) runs the self-test with many
configurations and checks every route in their debug logs. It needs only Python 3, the
localnet up and the self-test built in release mode (`--build` builds it first).

```sh
./localnet.sh verify --quick              # one parameter per strategy, 19 runs (~2 min)
./localnet.sh verify                      # every parameter, 61 runs
./localnet.sh verify --combos all         # all 16 routing configs per parameter, 161 runs
./localnet.sh verify --only khf-1.2       # runs whose name contains "khf-1.2"
./localnet.sh verify --list               # print the runs and their command lines
./localnet.sh verify --quick -v           # also print every passed check
```

| Option | Default | Meaning |
|---|---|---|
| `--quick` | off | One parameter per strategy (K-HF `1,2`, K/W `2`, α `0.5`) instead of all of them (K-HF `1`, `2`, `1,2`, `1,2,3`; K/W `1`, `2`, `5`; α `0.0`, `0.5`, `1.0`). |
| `--combos single\|all` | `single` | Routing configs per parameter. `single`: all `strategy`, each class alone on `baseline`, all `baseline` (6). `all`: every combination (16). |
| `--only TEXT` | | Only runs whose name contains TEXT. |
| `--size BYTES` | 20000 | Message size passed to every run. |
| `--timeout SECS` | 120 | Time limit per run. |
| `--require-delivery` | off | Also fail runs whose message did not come back. |
| `--build` | off | `cargo build --release` the self-test first. |
| `--bin-dir`, `--topology`, `--log-dir` | `self-test/target/release`, `data/network.json`, `verify-logs` | Locations. |
| `--max-errors N` | 10 | Errors printed per run. |
| `-v` | off | Print every passed check. |

Run names encode the configuration: `khf-1.2_real-s_realack-b_cover-s_coverack-s` is K-HF on
layers 1,2 with `--real-ack-routing baseline` and the rest on `strategy`. Each run's full output
is saved to `verify-logs/<run name>.log`.

For every run it checks:

- the client reports the expected strategy and the routing of each class;
- every route uses one node of each layer of `data/network.json`;
- every route line has the routing of its class's flag (`real` and `reply-surb` follow
  `--real-routing`, `real-ack` follows `--real-ack-routing`, and so on);
- each session is created once, before its first route, and numbers its packets 1, 2, 3, … with
  no gaps. This proves that `baseline` routes are not counted. With every class on `baseline`,
  no session is created at all;
- **K-HF**: the session fixes exactly the configured layers, and every strategy route uses the
  fixed nodes (following replacements). With at least 30 `baseline` routes, they must not all
  go through the fixed nodes;
- **K/W**: each layer preselects `min(K, layer size)` distinct nodes (with a warning when K is
  larger), and every route stays within them;
- **α-SS**: the first route is new without a roll; every roll agrees with its decision; new
  routes were not yet in the leading set and reused ones were; the printed leading set size is
  right. It also reports the observed reuse rate next to α;
- **Baseline strategy**: nothing is logged by the path selection.

Each run prints `PASS` or `FAIL`, route counts per class and statistics. The script exits with
code 1 if any run fails.

### Delivery failures

Whether the message came back is reported separately from the routing checks. A mixnode that is
stopped but still in `network.json` drops every packet routed through it. With strategies that
pin nodes (K-HF, K/W with small K, α-SS with α = 1), a session pinned to that node loses all of
its packets and retransmissions, so the run reports `NOT delivered` while its routing passes.
This is the availability cost of fixing nodes. Start the node again
(`docker compose start mix3`) if every run should deliver, or use `--require-delivery` to count
these runs as failures.
