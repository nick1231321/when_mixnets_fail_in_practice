#!/bin/bash
# Capture the client<->gateway links of the netem localnet from the network observer's position.
#
# Two modes (LINKOBS_MODE):
#   docker (default)  a sidecar container per gateway shares that gateway's network namespace and
#                     records its client WebSocket port on eth0, i.e. the packets on the wire into
#                     the gateway. No host root needed; the gateway's Sphinx log is not used.
#   host              sudo tcpdump on the host interface (lo0) for the published ports 9100/9101.
#
#   capture.sh start OUT_DIR      start capturing; pcaps go to OUT_DIR/<gateway>.pcap (docker) or
#                                 OUT_DIR/link.pcap (host)
#   capture.sh stop  OUT_DIR      stop and flush
#
# Docker mode needs the tiny image linkobs-tcpdump (built on first use from alpine + tcpdump).
set -eu
cmd=${1:?start|stop}; out=${2:?output dir}
MODE=${LINKOBS_MODE:-docker}
# gateway container -> client WebSocket port inside the container (see docker-compose.netem.yml)
GATEWAYS=${LINKOBS_GATEWAYS:-"nym-netem-gateway:9000 nym-netem-gateway2:9001"}
mkdir -p "$out"; out=$(cd "$out" && pwd)

ensure_image () {
  docker image inspect linkobs-tcpdump >/dev/null 2>&1 && return
  d=$(mktemp -d); printf 'FROM alpine:3.20\nRUN apk add --no-cache tcpdump\n' > "$d/Dockerfile"
  docker build -q -t linkobs-tcpdump "$d" >/dev/null; rm -rf "$d"
}

case "$MODE:$cmd" in
  docker:start)
    ensure_image
    for gp in $GATEWAYS; do
      gw=${gp%%:*}; port=${gp##*:}
      docker rm -f "linkobs-cap-$gw" >/dev/null 2>&1 || true
      # -U: write each packet as it arrives (nothing lost if stopped abruptly); -s: bytes kept per packet
      # (LINKOBS_SNAPLEN, default 0 = whole packets; 96 keeps the headers only)
      docker run -d --name "linkobs-cap-$gw" --net "container:$gw" -v "$out:/cap" linkobs-tcpdump \
        tcpdump -i eth0 -s "${LINKOBS_SNAPLEN:-0}" -B 16384 -nn -U -w "/cap/$gw.pcap" "tcp port $port" >/dev/null
    done
    sleep 2
    for gp in $GATEWAYS; do
      gw=${gp%%:*}
      [ "$(docker inspect -f '{{.State.Running}}' "linkobs-cap-$gw")" = true ] || { docker logs "linkobs-cap-$gw"; exit 1; }
    done
    echo "capturing in $(echo $GATEWAYS | wc -w | tr -d ' ') gateway namespaces -> $out/*.pcap";;
  docker:stop)
    for gp in $GATEWAYS; do
      gw=${gp%%:*}
      docker stop -t 5 "linkobs-cap-$gw" >/dev/null 2>&1 || true
      echo "$gw: $(docker logs "linkobs-cap-$gw" 2>&1 | grep -E 'packets (captured|dropped by kernel)' | tr '\n' ' ')"
      docker rm "linkobs-cap-$gw" >/dev/null 2>&1 || true
    done;;
  host:start)
    filter="tcp port 9100 or tcp port 9101"
    sudo tcpdump -i "${LINKOBS_IFACE:-lo0}" -s "${LINKOBS_SNAPLEN:-0}" -B 65536 -nn -U -w "$out/link.pcap" "$filter" >"$out/link.pcap.log" 2>&1 &
    echo $! > "$out/link.pcap.pid"; sleep 2
    kill -0 "$(cat "$out/link.pcap.pid")" 2>/dev/null || { cat "$out/link.pcap.log"; exit 1; }
    echo "capturing ${LINKOBS_IFACE:-lo0} ($filter) -> $out/link.pcap";;
  host:stop)
    pid=$(cat "$out/link.pcap.pid"); sudo kill -INT "$pid" 2>/dev/null || true
    while kill -0 "$pid" 2>/dev/null; do sleep 0.5; done; rm -f "$out/link.pcap.pid"
    grep -E 'packets (captured|dropped by kernel)' "$out/link.pcap.log" | tr '\n' ' '; echo;;
  *) echo "usage: [LINKOBS_MODE=docker|host] capture.sh start|stop OUT_DIR"; exit 2;;
esac
