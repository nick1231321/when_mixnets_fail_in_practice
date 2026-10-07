#!/bin/bash
# Docker-based Nym localnet helper
set -e
cd "$(dirname "$0")"

NODES="mix1 mix2 mix3 mix4 mix5 mix6 mix7 mix8 mix9 gateway gateway2"

# Network namespace of a running container, empty if it is not running.
netns_of() {
    docker exec "nym-$1" readlink /proc/1/ns/net 2>/dev/null || true
}

# Starts the namespace holder, then every node that is stopped or stuck in a stale namespace
# (left behind when the holder itself restarted). See the comment in docker-compose.yml.
heal() {
    docker compose up -d netns >/dev/null 2>&1
    local target healed=0
    target=$(netns_of netns)
    for node in $NODES; do
        local current
        current=$(netns_of "$node")
        if [ -z "$current" ]; then
            echo "starting $node (not running)"
            docker compose up -d --no-deps "$node" >/dev/null 2>&1
            healed=$((healed + 1))
        elif [ "$current" != "$target" ]; then
            echo "restarting $node (in a stale network namespace)"
            docker compose restart "$node" >/dev/null 2>&1
            healed=$((healed + 1))
        fi
    done
    echo "healed $healed node(s); all nodes share namespace $target"
}

case "${1:-help}" in
  build) docker compose build ;;
  up)
    mkdir -p data
    docker compose up -d
    echo "Waiting for topology (data/network.json)..."
    until [ -f data/network.json ]; do sleep 1; done
    echo "Waiting for gateway on 127.0.0.1:9000..."
    until nc -z 127.0.0.1 9000 2>/dev/null; do sleep 1; done
    echo "Localnet is up. Topology: $(pwd)/data/network.json"
    ;;
  down)  docker compose down -v; rm -rf data ;;
  logs)  shift; docker compose logs -f "$@" ;;
  ps)    docker compose ps ;;
  test)  cargo run --release --manifest-path self-test/Cargo.toml -- data/network.json ;;
  verify) shift; python3 verify_path_selection.py "$@" ;;
  heal)  heal ;;
  *)     echo "Usage: $0 {build|up|down|logs [service]|ps|test|heal|verify [--quick] [--only NAME] [-v]}" ;;
esac
