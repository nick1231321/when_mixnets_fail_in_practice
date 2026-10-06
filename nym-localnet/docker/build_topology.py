"""Build network.json for the Docker localnet.

Node layout (index -> ports), all announced on 127.0.0.1:
  mixnodes  1..9   mixnet 10000+i, layer = (i-1)//3 + 1   (3 per layer)
  gateways  10, 11 mixnet 10000+i, clients ws 9000+(i-10)

Nodes rotate their sphinx keys on the schedule of the public Nym network (every 24 epochs,
i.e. 24 hours), so the topology records each node's current key and rotation id. With
--watch it keeps rebuilding network.json, replacing it atomically whenever the keys change.

Usage: build_topology.py <localnet_dir> [--watch]
"""

import json
import os
import subprocess
import sys
import time
from collections import Counter
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
WATCH_INTERVAL_SECS = 60


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def primary_sphinx_key(name):
    """Returns (rotation_id, key bytes) of the node's current primary sphinx key."""
    config = NODES_ROOT / f"{name}-localnet" / "config" / "config.toml"
    out = subprocess.run(
        ["nym-node", "node-details", "--config-file", str(config), "--output=json"],
        check=True, capture_output=True, text=True,
    ).stdout
    key = json.loads(out)["x25519_primary_sphinx_key"]
    return key["rotation_id"], list(base58.b58decode(key["public_key"]))


def node_version():
    out = subprocess.run(["nym-node", "--version"], capture_output=True, text=True).stdout
    tokens = [t for t in out.split() if t[:1].isdigit()]
    return tokens[-1] if tokens else None


def entry(base_dir, name, node_id, gateway, rotation_ids):
    bonding = json.loads((base_dir / f"{name}.json").read_text())
    rotation_id, sphinx_key = primary_sphinx_key(name)
    rotation_ids[name] = rotation_id
    node = {
        "node_id": node_id,
        "mix_host": f"{HOST}:{10000 + node_id}",
        "entry": None,
        "identity_key": bonding["identity_key"],
        "sphinx_key": sphinx_key,
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


def build_topology(base_dir):
    layers = {f"layer{l}": [] for l in range(1, LAYERS + 1)}
    nodes = {}
    rotation_ids = {}

    for i in range(1, LAYERS * MIXES_PER_LAYER + 1):
        nodes[i] = entry(base_dir, f"mix{i}", i, False, rotation_ids)
        layers[f"layer{(i - 1) // MIXES_PER_LAYER + 1}"].append(i)

    gateways = []
    for g in range(NUM_GATEWAYS):
        node_id = FIRST_GATEWAY + g
        name = "gateway" if g == 0 else f"gateway{g + 1}"
        nodes[node_id] = entry(base_dir, name, node_id, True, rotation_ids)
        gateways.append(node_id)

    # packets are tagged with the topology's rotation id, so it must match the nodes' keys
    key_rotation_id, _ = Counter(rotation_ids.values()).most_common(1)[0]
    lagging = {n: r for n, r in rotation_ids.items() if r != key_rotation_id}
    if lagging:
        log(f"WARNING: nodes on a different key rotation than {key_rotation_id}: {lagging}")

    return {
        "metadata": {
            "key_rotation_id": key_rotation_id,
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


def keys_of(topology):
    """The parts of the topology that change on key rotation."""
    if topology is None:
        return None
    return topology["metadata"]["key_rotation_id"], {
        str(i): n["sphinx_key"] for i, n in topology["node_details"].items()
    }


def write_atomically(path, topology):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(topology, indent=2))
    os.replace(tmp, path)


def update(base_dir, current):
    """Rebuilds the topology and writes it if the keys changed. Returns the active topology."""
    topology = build_topology(base_dir)
    if keys_of(topology) == keys_of(current):
        return current
    write_atomically(base_dir / "network.json", topology)
    rotation = topology["metadata"]["key_rotation_id"]
    rs = topology["rewarded_set"]
    layers = {l: rs[l] for l in ("layer1", "layer2", "layer3")}
    log(f"wrote network.json for key rotation {rotation}: {layers}, gateways={rs['entry_gateways']}")
    return topology


def main(base_dir, watch):
    base_dir = Path(base_dir)
    current = None
    if (base_dir / "network.json").exists():
        current = json.loads((base_dir / "network.json").read_text())

    current = update(base_dir, current)
    while watch:
        time.sleep(WATCH_INTERVAL_SECS)
        try:
            current = update(base_dir, current)
        except Exception as err:  # keep watching through transient node-details failures
            log(f"failed to refresh topology: {err}")


if __name__ == "__main__":
    main(sys.argv[1], "--watch" in sys.argv[2:])
