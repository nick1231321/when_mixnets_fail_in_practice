#!/usr/bin/env python3
"""Keep a copy of a collection on a remote machine and move the pcaps off this one.

Every --every seconds:
  1. rsync everything except the pcaps (logs, manifest, plan; cheap, repeated);
  2. for every finished run (DONE present) that still has pcaps here, move them: rsync with
     --remove-source-files, so a pcap is deleted locally only after it arrived complete.
The run that is in progress (no DONE yet) is never touched. Runs at low priority (nice 15).

  sync_runs.py --local ~/flow-matching-data/2026-10-17-rate-sweep \\
               --remote amazone:flow-matching-data/2026-10-17-rate-sweep [--every 120] [--once]

Stop with Ctrl-C or by killing it; a restart continues (rsync only sends what is missing).
"""
import argparse, os, subprocess, time
from datetime import datetime
from pathlib import Path

SSH = 'ssh -o BatchMode=yes -o ServerAliveInterval=30'


def log(msg):
    print(f'[{datetime.now():%m-%d %H:%M:%S}] {msg}', flush=True)


def rsync(*args):
    r = subprocess.run(['rsync', '-a', '--partial', '-e', SSH, *args], capture_output=True, text=True)
    if r.returncode != 0:
        log(f'rsync failed ({r.returncode}): {r.stderr.strip()[-300:]}')
    return r.returncode == 0


def sync_once(local, remote):
    ok = rsync('--exclude', 'runs/*/capture/*.pcap', f'{local}/', f'{remote}/')
    moved = 0
    for done in sorted(local.glob('*/runs/*/DONE')):
        cap = done.parent / 'capture'
        pcaps = list(cap.glob('*.pcap'))
        if not pcaps:
            continue
        rel = cap.relative_to(local)
        if rsync('--remove-source-files', *map(str, pcaps), f'{remote}/{rel}/'):
            moved += len(pcaps)
    return ok, moved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--local', required=True, type=Path)
    ap.add_argument('--remote', required=True, help='host:path, e.g. amazone:flow-matching-data/2026-10-17-rate-sweep')
    ap.add_argument('--every', type=int, default=120, help='seconds between syncs')
    ap.add_argument('--once', action='store_true')
    a = ap.parse_args()
    os.nice(15)
    local = a.local.expanduser().resolve()
    host, path = a.remote.split(':', 1)
    subprocess.run(SSH.split() + [host, f'mkdir -p {path}'], check=True)
    while True:
        ok, moved = sync_once(local, a.remote)
        log(f'synced{"" if ok else " (with errors)"}; moved {moved} pcaps')
        if a.once:
            break
        time.sleep(a.every)


if __name__ == '__main__':
    main()
