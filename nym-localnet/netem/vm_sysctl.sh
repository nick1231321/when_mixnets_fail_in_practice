#!/bin/sh
# Raises the Docker VM's neighbour (ARP/NDP) table limits for the netem localnet.
#
# The kernel keeps ONE neighbour table across all network namespaces, capped by
# net.ipv4.neigh.default.gc_thresh3 (default 1024). With every node in its own namespace,
# N nodes create ~N*N entries (62 nodes -> ~3,800), the table overflows
# ("neighbour: arp_cache: neighbor table overflow!" in dmesg), ARP resolution fails and the
# kernel silently drops packets between nodes: nodes forward everything they receive and count
# no drops, yet ~50% of packets vanish per hop. The ordinary 3x3 localnet (~120 entries) never
# hits this.
#
# These sysctls are global (not namespaced), so they are set from a privileged container in
# the VM's network namespace. They last until Docker Desktop restarts: run this once after
# starting Docker and before `apply_netem.py`.
#
# Usage: netem/vm_sysctl.sh [thresh3]   (default 16384; thresh1/2 = 1/4 and 1/2 of it)
set -e
T3=${1:-16384}; T2=$((T3 / 2)); T1=$((T3 / 4))
docker run --rm --privileged --net=host --entrypoint sh nym-localnet:latest -c "
for fam in ipv4 ipv6; do
    d=/proc/sys/net/\$fam/neigh/default
    [ -d \$d ] || continue
    echo $T1 > \$d/gc_thresh1; echo $T2 > \$d/gc_thresh2; echo $T3 > \$d/gc_thresh3
    echo \"\$fam neigh gc_thresh1/2/3 = \$(cat \$d/gc_thresh1) \$(cat \$d/gc_thresh2) \$(cat \$d/gc_thresh3)\"
done
echo \"neighbour table overflow messages so far: \$(dmesg 2>/dev/null | grep -c 'neighbor table overflow' || echo 0)\"
"
