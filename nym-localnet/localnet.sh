#!/bin/bash
# Docker-based Nym localnet helper
set -e
cd "$(dirname "$0")"

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
  *)     echo "Usage: $0 {build|up|down|logs [service]|ps|test}" ;;
esac
