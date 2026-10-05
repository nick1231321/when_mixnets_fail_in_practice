"""Build network.json for the Docker localnet.

Node layout (index -> ports), all announced on 127.0.0.1:
  mixnodes  1..9   mixnet 10000+i, layer = (i-1)//3 + 1   (3 per layer)
  gateways  10, 11 mixnet 10000+i, clients ws 9000+(i-10)

Usage: build_topology.py <localnet_dir>
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import base58

HOST = "127.0.0.1"
# must match node.sh / docker-compose.yml
LAYERS = 3
MIXES_PER_LAYER = 3
NUM_GATEWAYS = 2
FIRST_GATEWAY = LAYERS * MIXES_PER_LAYER + 1
NODES_ROOT = Path("/root/.nym/nym-nodes")


def sphinx_key(name):
    config = NODES_ROOT / f"{name}-localnet" / "config" / "config.toml"
    out = subprocess.run(
        ["nym-node", "node-details", "--config-file", str(config), "--output=json"],
        check=True, capture_output=True, text=True,
    ).stdout
    b58 = json.loads(out)["x25519_primary_sphinx_key"]["public_key"]
    return list(base58.b58decode(b58))


def node_version():
    out = subprocess.run(["nym-node", "--version"], capture_output=True, text=True).stdout
    tokens = [t for t in out.split() if t[:1].isdigit()]
    return tokens[-1] if tokens else None


def entry(base_dir, name, node_id, gateway):
    bonding = json.loads((base_dir / f"{name}.json").read_text())
    node = {
        "node_id": node_id,
        "mix_host": f"{HOST}:{10000 + node_id}",
        "entry": None,
        "identity_key": bonding["identity_key"],
        "sphinx_key": sphinx_key(name),
        "supported_roles": {"mixnode": not gateway, "mixnet_entry": gateway, "mixnet_exit": gateway},
        "version": node_version(),
    }
    if gateway:
        node["entry"] = {
            "ip_addresses": [HOST],
            "clients_ws_port": 9000 + (node_id - FIRST_GATEWAY),
            "hostname": None,
            "clients_wss_port": None,
        }
    return node


def main(base_dir):
    base_dir = Path(base_dir)
    layers = {f"layer{l}": [] for l in range(1, LAYERS + 1)}
    nodes = {}

    for i in range(1, LAYERS * MIXES_PER_LAYER + 1):
        nodes[i] = entry(base_dir, f"mix{i}", i, gateway=False)
        layers[f"layer{(i - 1) // MIXES_PER_LAYER + 1}"].append(i)

    gateways = []
    for g in range(NUM_GATEWAYS):
        node_id = FIRST_GATEWAY + g
        name = "gateway" if g == 0 else f"gateway{g + 1}"
        nodes[node_id] = entry(base_dir, name, node_id, gateway=True)
        gateways.append(node_id)

    topology = {
        "metadata": {
            "key_rotation_id": 0,
            "absolute_epoch_id": 0,
            "refreshed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "rewarded_set": {
            "epoch_id": 0,
            "entry_gateways": gateways,
            "exit_gateways": gateways,
            **layers,
            "standby": [],
        },
        "node_details": nodes,
    }
    (base_dir / "network.json").write_text(json.dumps(topology, indent=2))
    print(f"Generated topology: {layers}, gateways={gateways}")


if __name__ == "__main__":
    main(sys.argv[1])
