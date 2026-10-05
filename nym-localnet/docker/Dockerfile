# syntax=docker/dockerfile:1.4
# nym-node image for a Docker-based localnet (build context: the nym repo root)
FROM rust:latest AS builder
WORKDIR /usr/src/nym
COPY . .
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/usr/src/nym/target \
    cargo build --release --locked -p nym-node && \
    cp target/release/nym-node /usr/local/bin/nym-node

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-base58 netcat-openbsd iproute2 iptables jq \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/bin/nym-node /usr/local/bin/nym-node
COPY docker/localnet/build_topology.py /usr/local/bin/build_topology.py
WORKDIR /nym
CMD ["nym-node", "--help"]
