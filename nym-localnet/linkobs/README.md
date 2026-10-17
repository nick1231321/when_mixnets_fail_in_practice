# Flow matching from the network (link observer)

End-to-end flow matching on the netem localnet with the adversary moved **from inside the gateway to
the wire in front of it**: an ISP, a hotspot or a backbone tap that sees a client's TCP connection
to its gateway but has no Sphinx-level log. Same task as [`../mixmatch/`](../mixmatch/) (are clients
I and R talking to each other?), same scorers (MixMatch's statistical classifier, the
drift-statistics tree), different observation.

## What the observer sees

Per client connection, per direction: TCP segments carrying WebSocket frames; one frame = one
gateway message = one Sphinx packet. Payload cells and ack cells differ in size, so the observer can
label frames by size as the gateway exporter labels Sphinx packets. What it loses compared to the
gateway: exact Sphinx timestamps (it gets segment capture times, with TCP coalescing and link
jitter), the gateway's own ack emission times (still inferable: every delivered payload is acked),
the first/last-hop mix of each packet, and any notion of Nym identity (connections are anonymous;
labels are attached afterwards from the gateway wire log, for evaluation only).

**Capture position.** By default (`LINKOBS_MODE=docker`) a sidecar container per gateway shares
that gateway's network namespace and records its client WebSocket port (`9000`, `9001`) on `eth0`:
the packets on the wire into the gateway, with no host root and without the gateway's Sphinx log.
`LINKOBS_MODE=host` instead runs `sudo tcpdump` on the host loopback for the published ports
`9100/9101`.

**What the link shows on the localnet** (plain `ws://`): client->gateway frames of 2452 B (one
Sphinx packet each); gateway->client frames of 1673 B (delivered payload), 51 B (delivered ack) and
70 B (the gateway's confirmation of each packet the client sent). The gateway's wire log records the
same payload and ack messages as 1643 B and 21 B: the link adds 30 B of framing. Only the payload
and ack frames are used as cells; the confirmations are dropped (they would give the observer the
gateway's exact processing time of each sent packet).

## Pipeline

```
run_with_capture.sh CONFIG.toml OUT_DIR [--tls] [--score]
  capture.sh start        sidecar tcpdump per gateway namespace (or sudo tcpdump on lo0)
  resmon.py wrap          nym-load-test CONFIG under the resource monitor (monitor.csv, verdict.json)
  capture.sh stop
  pcap_streams.py         pcap -> events.csv  (conn, direction, t_ns, len): TCP reassembly per
                          connection/direction + WebSocket frame delimiting (plain ws://);
                          --tls: one event per segment, sizes decomposed into cells later
  link_export.py          events -> MixMatch six-file export (export_link/), frames classed by exact
                          size (payload = largest common size; ack = the common small size not tied
                          to the client's own sends, i.e. not the confirmation); connections matched to clients
                          by time-alignment with the gateway wire log (labels only)
  export_their_format.py  the gateway's own view of the same run (export_gateway/) for comparison
  --score                 run_their_matching.m (Octave) and score_tree.py on both exports
```

`link_emulate.py` derives a link-like view from existing gateway wire logs (coalescing window,
jitter, optional loss) for sensitivity sweeps without capturing:

```sh
python3 linkobs/link_emulate.py --run RUN_DIR --wire-dir data-netem/wire --out /tmp/emu --coalesce-ms 2 --jitter-ms 2
python3 linkobs/score_tree.py --test emu=/tmp/emu:RUN_DIR --test gw=EXPORT_GATEWAY:RUN_DIR
```

`score_tree.py` scores any export (gateway or link view) with the paper's Gaussian rule and the
tree (`tools/pair_features.py`, the same features as the analysis notebook): `--train` runs fit
eta/sigma and the trees, `--test` runs are scored; without `--train`, leave-one-run-out across the
test runs, or 5-fold by initiator within a single run.

## Requirements

- netem localnet up (`../netem/README.md`), `nym-load-test` built, gateways logging the wire
  (`MIXCORR_WIRE_DIR`) so that labels can be attached.
- Docker capture mode: nothing extra (the `linkobs-tcpdump` image is built on first use).
  Host mode: `sudo` for `tcpdump` (BPF devices are root-only on macOS).
- Python 3 with numpy / pandas / scikit-learn for the scorers; `pcap_streams.py` uses only the
  standard library. Octave for `run_their_matching.m`.

## Caveats

- `pcap_streams.py` reads libpcap files (`tcpdump -w`), not pcapng. TCP sequence numbers are handled
  modulo 2^32 (a connection whose initial sequence number lies near 2^32 wraps within ~20 MB).
- With `--tls`, cells are recovered from segment sizes (a*payload + b*ack); coalesced records make
  the per-cell timestamps coarser than with plain WebSocket frames. The localnet gateways serve
  plain `ws://`; a wss:// deployment is the `--tls` case.
- Connection-to-client matching needs the gateway wire log of the same run; it exists only to
  label pairs for evaluation and is not part of the attack.
