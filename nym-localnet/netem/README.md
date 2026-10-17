# Localnet with emulated mainnet latencies (netem)

The ordinary localnet ([`../docker-compose.yml`](../docker-compose.yml)) puts every node in one
network namespace on `127.0.0.1`, so all routes have the same ~5 ms latency. The experiments on
latency and flow matching need the real spread of route latencies, which comes from *pairwise*
node-to-node delays. This variant gives every node its own network namespace and IP and shapes
each node's egress with `tc netem`, one delay per destination node, taken from the real mainnet
RTT matrix (VerLoc, epoch 34211).

It is a separate compose project (`nym-netem`, its own volume and `data-netem/` dir, gateway
websockets on `127.0.0.1:9100+`), so it runs next to the ordinary localnet without touching it.

## Files

| file | role |
|---|---|
| `gen_compose.py` | writes `docker-compose.netem.yml` for a layout (L×M mixnodes + G gateways): bridge subnet `172.30.0.0/24`, node *i* at `172.30.0.(10+i)`, `NET_ADMIN` on every node, `NETEM_TUNING=1` and `FIRST_GATEWAY` in the environment, `tcp_slow_start_after_idle=0` |
| `vm_sysctl.sh` | raises the Docker VM's neighbour (ARP) table limits — **required beyond ~30 nodes**, see below |
| `apply_netem.py` | installs / clears the `tc` rules in every node container from a `netem.csv` (jitter off unless `--jitter`) |
| `data/<layout>/` | `netem.csv`, `nodes.csv`, `rtt.csv`, `summary.json` from [`../latency/build_rtt.py`](../latency/build_rtt.py) for that layout |

The node scripts [`../docker/node.sh`](../docker/node.sh) and
[`../docker/build_topology.py`](../docker/build_topology.py) are shared with the ordinary localnet;
the compose file configures them through `PUBLIC_IP`, `FIRST_GATEWAY`, `NETEM_TUNING` and
`LOCALNET_*` environment variables, and their defaults keep the ordinary behaviour. The topology
announces each node's bridge IP as `mix_host` (node-to-node) and `127.0.0.1` plus the published
websocket port for the gateways' client entry, so clients on the host (self-test, load generator)
connect as before.

## Usage

```sh
cd nym-localnet
# 0. once per Docker start, before any large layout: neighbour table limits (see below)
netem/vm_sysctl.sh
# 1. real delays for the layout (where the observatory data is, e.g. amazone)
python3 latency/build_rtt.py --data ~/nym-observatory/data --epoch 34211 \
    --mixes-per-layer 20 --gateways 2 --validate 0 --out netem/data/l3x20g2
# 2. compose file and start
python3 netem/gen_compose.py --mixes-per-layer 20 --gateways 2 --out docker-compose.netem.yml
docker compose -p nym-netem -f docker-compose.netem.yml up -d
# 3. delays (idempotent; --processing-ms adds a constant per hop; --clear removes them)
python3 netem/apply_netem.py netem/data/l3x20g2/netem.csv --mixes-per-layer 20 --gateways 2
# 4. check: every route's latency against the sum of its hops
./self-test/target/release/nym-route-latency --topology data-netem/network.json --max-packets 400
# stop
docker compose -p nym-netem -f docker-compose.netem.yml down -v && rm -rf data-netem
```

`apply_netem.py` shapes only traffic between nodes; traffic to the host (clients) is untouched.
Each direction of a pair gets its own one-way delay (RTT/2 of `--stat`), so a round trip adds
up to the pair's RTT. Jitter is off by default: netem's random jitter reorders TCP segments,
and on pairs where it is comparable to the delay that collapses the congestion window of the
sparse node-to-node connections.

## Neighbour table limit (why `vm_sysctl.sh` is required)

The Linux kernel keeps **one** neighbour (ARP) table across all network namespaces, capped by
`net.ipv4.neigh.default.gc_thresh3` (default 1024). With every node in its own namespace, N
nodes create about N² entries: 62 nodes → ~3,800, far over the cap. The table overflows
(`neighbour: arp_cache: neighbor table overflow!` in the VM's `dmesg`), ARP resolution fails,
and the kernel silently drops packets between nodes. The symptom is distinctive and misleading:
every node forwards exactly what it receives and counts **no** drops, yet about half of the
packets vanish at every hop (0.5³ ≈ 12 % end-to-end delivery), the clients retransmit massively
and latencies reach tens of seconds — with or without netem. The 3×3 layout (~120 entries) never
hits it, which is why it worked while 3×20 did not.

`vm_sysctl.sh` sets `gc_thresh1/2/3` to 4096/8192/16384 (IPv4 and IPv6) from a privileged
container in the VM's network namespace; the values last until Docker Desktop restarts.

## Node tuning (`NETEM_TUNING=1`)

`node.sh` appends two sections to each node's config:

- `[mixnet.debug]` makes forwarding tolerant of high-RTT links with sparse per-pair traffic:
  egress flush timeout 500 ms → 5 s, reconnection backoff 10 s → 0.5 s, idle timeout 5 min →
  1 h, connection buffer 192 → **256** packets. With the defaults, a few slow flushes over a
  400 ms-RTT link tear a connection down and blackhole that pair for 10 s. The buffer is kept
  small on purpose: at 61 peers × 2 KB a large buffer (2048 was tried) lets a node grow to
  300 MB+ under a retransmission burst, and 62 such nodes OOM-kill the Docker VM (17 kills
  observed, nodes restarting mid-experiment and losing their `tc` rules).
- `[verloc.debug]` makes VerLoc trivial: `packets_per_node = 1`, `testing_interval = 8760h`.
  All 62 nodes boot together, so their 12-hourly VerLoc rounds (100 echo packets × every peer)
  run in sync and saturate the VM — ~500 % CPU at boot and again 12 h later, which degraded
  the conditions running at that moment. With the trivial round the layout idles at ~25 %.
  The struct has no serde default, so the section must list all seven fields (the other five
  at their defaults) or the config fails to parse.

The tuning changes robustness and background load, not the forwarding delay. Clients should
likewise use a longer ack wait (`ack_wait_addition_s = 10` in the load generator; built into
`nym-route-latency`): the client default of ~1.6 s is shorter than the ack round trip of a long
intercontinental route.

## Validation

**3×20 mixnodes + 2 gateways, epoch 34211, under a 20-client load** (10 sender/receiver pairs,
cover traffic on, 15 ms mixing):

- route sweep of 300 of the 8,000 routes while loaded: measured loop latency vs. the sum of
  the four emulated hops — correlation **0.9999**, offset **+4.3 ms** (client↔gateway hop and
  processing), std **1.7 ms**; no retransmissions;
- the load itself: **100 %** delivery, **0** retransmitted fragments, 0 node flush timeouts,
  per-packet delay mean 210 ms with a **within-flow std of 98 ms** (route variance + mixing +
  modest queueing); CPU of the 63 containers 274 % of 10 cores under load, 67 % idle.

**3×3 + 2 gateways** (earlier, no load): correlation 0.9987 over all 27 routes, offset +7.3 ms,
std 4.9 ms; route latency 155–410 ms (4–7 ms without netem).

Node processing on mainnet is much larger than on the localnet (measured mainnet loops run
2–3× the pure RTT, ~100 ms+ per hop); emulate it with `--processing-ms` when it matters.
