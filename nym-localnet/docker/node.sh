#!/bin/sh
# Usage: nym-localnet-node.sh <mix|gateway> <name> <index>
# Ports: mixnet 10000+i, verloc 20000+i, http 30000+i; gateways (i >= 10) also get
# clients ws 9000+(i-10) and LP 41254+i / 51254+i. Keep in sync with build_topology.py.
# All nodes share one network namespace and announce 127.0.0.1, so the
# generated topology is reachable both between nodes and from the host.
set -e
KIND=$1; NAME=$2; IDX=$3
ID="${NAME}-localnet"
FIRST_GATEWAY=10
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
        --public-ips 127.0.0.1 \
        --accept-operator-terms-and-conditions \
        --output=json \
        --bonding-information-output="/localnet/${NAME}.json"
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
