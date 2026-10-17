#!/bin/sh
# Usage: nym-localnet-node.sh <mix|gateway> <name> <index>
# Ports: mixnet 10000+i, verloc 20000+i, http 30000+i; gateways (i >= 10) also get
# clients ws 9000+(i-10) and LP 41254+i / 51254+i. Keep in sync with build_topology.py.
# All nodes share one network namespace and announce 127.0.0.1, so the
# generated topology is reachable both between nodes and from the host.
# The netem localnet (netem/gen_compose.py) instead gives every node its own namespace and
# IP, passed in as PUBLIC_IP; the default keeps the shared-namespace behaviour.
set -e
KIND=$1; NAME=$2; IDX=$3
ID="${NAME}-localnet"
FIRST_GATEWAY=${FIRST_GATEWAY:-10}
PUBLIC_IP=${PUBLIC_IP:-127.0.0.1}
COMMON="--unsafe-disable-replay-protection --local"

if [ ! -f "/root/.nym/nym-nodes/${ID}/config/config.toml" ]; then
    if [ "$KIND" = "mix" ]; then
        EXTRA=""
    else
        EXTRA="--mode entry-gateway --mode exit-gateway --entry-bind-address=0.0.0.0:$((9000 + IDX - FIRST_GATEWAY))"
    fi
    nym-node run --id "$ID" --init-only $COMMON $EXTRA \
        --mixnet-bind-address=0.0.0.0:$((10000 + IDX)) \
        --verloc-bind-address=0.0.0.0:$((20000 + IDX)) \
        --http-bind-address=0.0.0.0:$((30000 + IDX)) \
        --http-access-token=lala \
        --public-ips "$PUBLIC_IP" \
        --accept-operator-terms-and-conditions \
        --output=json \
        --bonding-information-output="/localnet/${NAME}.json"
fi

CONFIG="/root/.nym/nym-nodes/${ID}/config/config.toml"
# The netem localnet (netem/) emulates intercontinental RTTs between nodes while most node
# pairs see only sparse traffic. With the defaults (500 ms egress flush timeout, 10 s
# reconnection backoff, 192-packet buffer) nodes then drop connections and packets. The buffer
# stays bounded (256): at 61 peers x 2 KB a large buffer lets a node grow to hundreds of MB
# under a retransmission burst, and 62 of those OOM-kill the Docker VM.
# NETEM_TUNING=1 makes forwarding tolerant of high-RTT links; it changes robustness, not
# the forwarding delay. Appended once (the section is not in the generated config).
if [ "${NETEM_TUNING:-0}" = "1" ] && ! grep -q '^\[mixnet.debug\]' "$CONFIG"; then
    cat >> "$CONFIG" <<'TOML'

[mixnet.debug]
initial_connection_timeout = '5s'
connection_idle_timeout = '3600s'
connection_write_timeout = '5s'
maximum_connection_buffer_size = 256
packet_forwarding_initial_backoff = '500ms'
packet_forwarding_maximum_backoff = '5s'

# VerLoc's 12-hourly round (100 packets x every peer, all nodes in sync) saturates the VM on a
# 60-node layout and is irrelevant to the experiments. The struct has no serde default, so every
# field must be given: only packets_per_node and testing_interval differ from the defaults.
[verloc.debug]
packets_per_node = 1
connection_timeout = '5s'
packet_timeout = '1500ms'
delay_between_packets = '50ms'
tested_nodes_batch_size = 50
testing_interval = '8760h'
retry_timeout = '30m'
TOML
fi

# All nodes share one network namespace, so each gateway needs its own LP ports.
# (nym-node ignores --lp-*-bind-address, so set them in the config directly.)
if [ "$KIND" = "gateway" ]; then
    CONFIG="/root/.nym/nym-nodes/${ID}/config/config.toml"
    sed -i -e "s|^control_bind_address = .*|control_bind_address = '[::]:$((41254 + IDX))'|" \
           -e "s|^data_bind_address = .*|data_bind_address = '[::]:$((51254 + IDX))'|" "$CONFIG"
fi

echo "Waiting for /localnet/network.json..."
while [ ! -f /localnet/network.json ]; do sleep 1; done
exec nym-node run --id "$ID" $COMMON --accept-operator-terms-and-conditions
