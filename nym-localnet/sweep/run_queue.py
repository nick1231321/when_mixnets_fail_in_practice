#!/usr/bin/env python3
"""Run the rate-sweep plan (design.py) on the netem localnet and keep the RAW logs of every run.
Nothing is exported, cropped or scored here; processing happens later, over all runs together.

Per run (OUT/runs/<run_id>/):
  config.toml, run.json, clients.csv, messages.csv, lag.csv, load_test.log   the load test's own output
  monitor.csv, verdict.json                                                  resource monitor (resmon)
  gateway_wire/wire-<gateway host>.csv.gz   the gateways' wire log, only the bytes written during this run
  capture/<gateway>.pcap                    tcpdump of the client<->gateway links (only with --capture-every)
  DONE                                      written last; runs with it are skipped on restart
and one row per run in OUT/manifest.csv (plan values + delivery, verdict, status, sizes).

Before every run: the netem rules are checked on both gateways and three mixnodes (all 62 nodes
every --full-check runs) and re-applied if missing, the ARP limit is checked, free disk is checked.
The gateways' shared wire logs are rotated (gateways restarted, netem re-applied) when they grow
beyond --rotate-gb. Pause with `touch OUT/PAUSE` (waits after the current run until the file is
removed); stop with `touch OUT/STOP` (finishes the current run; a restart resumes where it stopped).

  run_queue.py --plan plan.csv --out DIR [--capture-every 0] [--max-runs N] [--dataset A,B,C]
"""
import argparse, csv, gzip, json, os, re, shutil, signal, subprocess, sys, time
from datetime import datetime
from pathlib import Path

LOCAL = Path(__file__).resolve().parent.parent
BIN = LOCAL / 'self-test/target/release/nym-load-test'
TOPOLOGY = LOCAL / 'data-netem/network.json'
GATEWAY_WIRE = LOCAL / 'data-netem/wire'
NETEM_CSV = LOCAL / 'netem/data/l3x20g2/netem.csv'
MIXES_PER_LAYER, GATEWAYS = 20, 2
PROJECT = 'nym-netem'
GATEWAY_CONTAINERS = [f'{PROJECT}-gateway', f'{PROJECT}-gateway2']
SAMPLE_MIXES = [f'{PROJECT}-mix1', f'{PROJECT}-mix30', f'{PROJECT}-mix60']
MANIFEST_COLS = ['run_id', 'dataset', 'condition', 'strategy', 'send_ms', 'mu_ms', 'loop_ms', 'msg_per_min',
                 'load_factor', 'message_size', 'clients', 'pkt_per_s', 'duration_s', 'warmup_s', 'drain_s', 'replicate', 'seed',
                 'started', 'finished', 'exit', 'delivered_min_pct', 'latency_p95_ms', 'verdict', 'reasons',
                 'status', 'netem_reapplied', 'gateway_wire_mb', 'client_wire_mb', 'pcap_mb', 'clients_spec', 'session']


def log(msg):
    print(f'[{datetime.now():%m-%d %H:%M:%S}] {msg}', flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ----------------------------------------------------------------------------- environment checks
def all_nodes():
    return [f'{PROJECT}-mix{i}' for i in range(1, 3 * MIXES_PER_LAYER + 1)] + GATEWAY_CONTAINERS


def netem_ok(containers):
    for c in containers:
        out = sh(['docker', 'exec', c, 'tc', 'qdisc', 'show', 'dev', 'eth0']).stdout
        if 'netem' not in out:
            return False, c
    return True, None


def ensure_netem(full):
    ok, bad = netem_ok(all_nodes() if full else GATEWAY_CONTAINERS + SAMPLE_MIXES)
    if ok:
        return False
    log(f'netem rules missing on {bad}: re-applying')
    r = sh([sys.executable, str(LOCAL / 'netem/apply_netem.py'), str(NETEM_CSV),
            '--mixes-per-layer', str(MIXES_PER_LAYER), '--gateways', str(GATEWAYS)])
    if r.returncode != 0:
        raise SystemExit(f'apply_netem.py failed:\n{r.stderr[-2000:]}')
    ok, bad = netem_ok(all_nodes())
    if not ok:
        raise SystemExit(f'netem still missing on {bad} after re-applying; stopping')
    return True


def arp_ok():
    out = sh(['docker', 'run', '--rm', '--privileged', '--net', 'host', 'alpine',
              'sysctl', '-n', 'net.ipv4.neigh.default.gc_thresh3']).stdout.strip()
    return out.isdigit() and int(out) >= 16384


def free_gb(path):
    return shutil.disk_usage(path).free / 1e9


def gateway_sizes():
    return {p.name: p.stat().st_size for p in GATEWAY_WIRE.glob('wire-*.csv')}


def delivery_ok():
    """End-to-end check: one small message through the mixnet and back (nym-self-test)."""
    try:
        r = sh([str(LOCAL / 'self-test/target/release/nym-self-test'), str(TOPOLOGY), '--size', '1000'], timeout=100)
        return 'SUCCESS' in r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return False


def heal(reason):
    """Restart every node so it loads its current sphinx keys, re-apply netem, test delivery.
    Needed after the nodes' daily key rotation (~12:30 UTC): without a Nym API the running nodes
    keep dropping every packet (all mixnodes receive 0) until restarted (seen 2026-10-17)."""
    log(f'healing the localnet ({reason}): restarting all nodes')
    names = [n for n in sh(['docker', 'ps', '--format', '{{.Names}}']).stdout.split()
             if re.fullmatch(rf'{PROJECT}-(mix\d+|gateway\d?)', n)]
    sh(['docker', 'restart', *names])
    time.sleep(45)
    ensure_netem(full=True)
    ok = delivery_ok()
    log(f'   heal {"succeeded: delivery works" if ok else "FAILED: messages still do not arrive"}')
    return ok


def rotate_gateways():
    log('rotating the gateways\' wire logs: restarting the gateway containers')
    sh(['docker', 'restart', *GATEWAY_CONTAINERS])
    time.sleep(20)                       # gateways reconnect to the mixnet
    ensure_netem(full=True)              # a restart wipes the gateways' tc rules


# ----------------------------------------------------------------------------- one run
def groups_toml(row):
    """One group for a homogeneous run; one group per client (its own mu and sending interval) for a
    heterogeneous run (plan column clients_spec, initiators first, paired by split-pairs)."""
    spec = json.loads(row['clients_spec']) if row.get('clients_spec') else None
    session = f'session = "{row["session"]}"\n' if row.get('session') else ''   # path-selection session scope
    if spec is None:
        return 'random-pairs', f'''[[group]]
name = "g"
count = {row["clients"]}
strategy = "{row["strategy"]}"
{session}average_packet_delay_ms = {row["mu_ms"]}
message_sending_average_delay_ms = {row["send_ms"]}
loop_cover_traffic_average_delay_ms = {row["loop_ms"]}
'''
    def one(k, c):
        mu, send = c[0], c[1]
        loop = c[2] if len(c) > 2 else row['loop_ms']
        toggles = '' if len(c) <= 3 else (f'poisson_sending = {"true" if c[3] else "false"}\n'
                                          f'loop_cover_traffic = {"true" if c[4] else "false"}\n')
        return f'''[[group]]
name = "c{k:02d}"
count = 1
strategy = "{row["strategy"]}"
{session}average_packet_delay_ms = {mu}
message_sending_average_delay_ms = {send}
loop_cover_traffic_average_delay_ms = {loop}
{toggles}'''
    return 'split-pairs', ''.join(one(k, c) for k, c in enumerate(spec))


def config_toml(row, run_dir):
    pairing, groups = groups_toml(row)
    return f'''# run {row["run_id"]}: dataset {row["dataset"]}, {row["condition"]}, {row["strategy"]}
topology = "{TOPOLOGY}"
output_dir = "{run_dir}"
duration_s = {row["duration_s"]}
warmup_s = {row["warmup_s"]}
drain_s = {row["drain_s"]}
message_size = {row["message_size"]}
messages_per_client_per_min = {float(row["msg_per_min"])}
pairing = "{pairing}"
seed = {row["seed"]}
[client]
loop_cover_traffic = true
poisson_sending = true
ack_wait_addition_s = 10
{groups}'''


def slice_gateway_logs(before, run_dir):
    """Copy the bytes each gateway appended during the run, whole lines only, gzipped."""
    out = run_dir / 'gateway_wire'; out.mkdir(exist_ok=True)
    total = 0
    for p in GATEWAY_WIRE.glob('wire-*.csv'):
        start, end = before.get(p.name, 0), p.stat().st_size
        if end < start:                  # file was recreated (gateway restarted): take all of it
            start = 0
        with open(p, 'rb') as f:
            header = f.readline()
            f.seek(start); data = f.read(end - start)
        if start > 0 and not data.startswith(b'\n'):
            with open(p, 'rb') as f:     # drop a partial first line
                f.seek(start - 1)
                if f.read(1) != b'\n':
                    data = data[data.find(b'\n') + 1:]
        data = data[:data.rfind(b'\n') + 1]
        if start == 0 and data.startswith(header):
            data = data[len(header):]
        with gzip.open(out / f'{p.name}.gz', 'wb', compresslevel=6) as g:
            g.write(header); g.write(data)
        total += len(data)
    return total / 1e6


def parse_load_test(log_path):
    """Delivery over all files of the run (the load test reports it per group, and a heterogeneous run
    has one group per client) and the largest per-group p95 latency."""
    sent = delivered = 0; p95 = []
    for line in log_path.read_text(errors='replace').splitlines():
        m = re.match(r'\s*(\S+)\s+(\d+)\s+([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$', line)
        if m:
            n = int(m.group(2)); sent += n; delivered += n * float(m.group(3)) / 100; p95.append(float(m.group(5)))
    return (round(100 * delivered / sent, 2) if sent else None), (max(p95) if p95 else None)


def extract_wire(cap_dir):
    """The wire view of each gateway's pcap, as an observer can use it from encrypted traffic:
    <gw>.frames.csv.gz (one row per WebSocket frame: connection, direction, time, length) and
    <gw>.segments.csv.gz (one row per TCP segment with payload). Returns False if a conversion failed."""
    ok = True
    for pcap in sorted(cap_dir.glob('*.pcap')):
        gw = pcap.stem
        for flag, name in (([], 'frames'), (['--tls'], 'segments')):
            csv_path = cap_dir / f'{gw}.{name}.csv'
            r = sh([sys.executable, str(LOCAL / 'linkobs/pcap_streams.py'), str(pcap), *flag, '--gateway-ports',
                    '9000,9001', '--conn-prefix', f'{gw}/', '--out', str(csv_path)])
            if r.returncode != 0 or not csv_path.exists():
                log(f'   wire extraction failed for {pcap.name} ({name}): {r.stderr.strip()[-200:]}'); ok = False
                continue
            with open(csv_path, 'rb') as f, gzip.open(f'{csv_path}.gz', 'wb', compresslevel=6) as g:
                shutil.copyfileobj(f, g)
            csv_path.unlink()
    return ok


def run_one(row, out, capture, timeout_s, extract=False, keep_pcap=True):
    run_dir = out / 'runs' / row['run_id']
    if run_dir.exists():
        shutil.rmtree(run_dir)           # an unfinished earlier attempt
    run_dir.mkdir(parents=True)
    (run_dir / 'config.toml').write_text(config_toml(row, run_dir))
    before = gateway_sizes()
    started = datetime.now().isoformat(timespec='seconds')
    if capture:
        sh([str(LOCAL / 'linkobs/capture.sh'), 'start', str(run_dir / 'capture')])
    env = {k: v for k, v in os.environ.items() if k != 'MIXCORR_WIRE_DIR'}   # the clients log nothing
    cmd = [sys.executable, str(LOCAL / 'resmon/resmon.py'), 'wrap', str(run_dir),
           '--load-test-log', str(run_dir / 'load_test.log'),
           '--thresholds', 'delivered_min_pct=0,foreign_cores=64',   # delivery judged below over the whole run;
                                                                     # node containers run as root, not "other users"
           '--',
           'sh', '-c', f'"{BIN}" "{run_dir / "config.toml"}" > "{run_dir / "load_test.log"}" 2>&1']
    proc = subprocess.Popen(cmd, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM); time.sleep(5)
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        code = 'timeout'
    if capture:
        time.sleep(3)
        stop = sh([str(LOCAL / 'linkobs/capture.sh'), 'stop', str(run_dir / 'capture')])
        (run_dir / 'capture' / 'capture.log').write_text(stop.stdout + stop.stderr)   # captured / dropped counts
    time.sleep(2)                        # let the gateways flush their last lines
    gw_mb = slice_gateway_logs(before, run_dir)
    pcap_mb = sum(p.stat().st_size for p in (run_dir / 'capture').glob('*.pcap')) / 1e6 if capture else 0
    if capture and extract and extract_wire(run_dir / 'capture') and not keep_pcap:
        for p in (run_dir / 'capture').glob('*.pcap'):
            p.unlink()                   # the wire view is extracted; the raw pcap is kept every N-th run only
    delivered, p95 = parse_load_test(run_dir / 'load_test.log')
    try:
        v = json.loads((run_dir / 'verdict.json').read_text())
        verdict, reasons = v['verdict'], '; '.join(v.get('invalid', []) + v.get('suspect', []))
    except (OSError, ValueError, KeyError):
        verdict, reasons = 'MISSING', ''
    if code != 0 or not (run_dir / 'clients.csv').exists() or delivered is None:
        status = 'failed'
    elif delivered >= 99 and verdict != 'INVALID':
        status = 'clean'
    else:
        status = 'degraded'              # below 99 % delivery or machine saturated: excluded later
    return dict(started=started, finished=datetime.now().isoformat(timespec='seconds'), exit=code,
                delivered_min_pct=delivered, latency_p95_ms=p95, verdict=verdict, reasons=reasons, status=status,
                gateway_wire_mb=round(gw_mb, 1), client_wire_mb=0, pcap_mb=round(pcap_mb, 1))


# ----------------------------------------------------------------------------- main loop
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plan', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--dataset', default='', help='comma-separated datasets of the plan to run (default: all)')
    ap.add_argument('--max-runs', type=int, help='stop after this many runs (e.g. a pilot)')
    ap.add_argument('--only', help='comma-separated run ids to run (e.g. a pilot selection)')
    ap.add_argument('--capture-every', type=int, default=0, help='tcpdump every N-th run (0 = never)')
    ap.add_argument('--extract-wire', action='store_true',
                    help='after each captured run, extract the wire view (frames + TCP segments) from the pcaps')
    ap.add_argument('--keep-pcap-every', type=int, default=1,
                    help='with --extract-wire: keep the raw pcap only every N-th run (1 = always)')
    ap.add_argument('--full-check', type=int, default=25, help='check netem on all nodes every N runs')
    ap.add_argument('--rotate-gb', type=float, default=4.0, help='rotate the gateway wire logs above this size')
    ap.add_argument('--min-free-gb', type=float, default=25.0)
    ap.add_argument('--retry-failed', action='store_true', help='also re-run runs whose latest status is failed or degraded')
    a = ap.parse_args()
    out = Path(a.out).expanduser().resolve(); (out / 'runs').mkdir(parents=True, exist_ok=True)
    lock = out / 'LOCK'
    if lock.exists():
        raise SystemExit(f'{lock} exists: another runner is active (delete it if not)')
    lock.write_text(str(os.getpid()))
    try:
        if not BIN.exists():
            raise SystemExit(f'{BIN} missing: cargo build --release in self-test')
        if not arp_ok():
            raise SystemExit('ARP table limit too low: run netem/vm_sysctl.sh first')
        shutil.copy(a.plan, out / 'plan.csv')
        plan = [r for r in csv.DictReader(open(a.plan)) if not a.dataset or r['dataset'] in a.dataset.split(',')]
        if a.only:
            plan = [r for r in plan if r['run_id'] in a.only.split(',')]
        manifest = out / 'manifest.csv'
        failed = set()
        if manifest.exists():
            last = {r['run_id']: r['status'] for r in csv.DictReader(open(manifest))}   # latest attempt
            failed = {k for k, v in last.items() if v in ('failed', 'degraded')}
        todo = [r for r in plan if not (out / 'runs' / r['run_id'] / 'DONE').exists()
                or (a.retry_failed and r['run_id'] in failed)]
        log(f'{len(plan)} runs in the selection, {len(todo)} to do')
        new_manifest = not manifest.exists()
        topology_mtime = TOPOLOGY.stat().st_mtime
        done = 0
        for row in todo:
            if (out / 'PAUSE').exists():
                log('PAUSE file found: paused (rm PAUSE to continue)')
                while (out / 'PAUSE').exists() and not (out / 'STOP').exists():
                    time.sleep(5)
                log('resumed')
            if (out / 'STOP').exists():
                log('STOP file found: stopping'); break
            if a.max_runs and done >= a.max_runs:
                break
            if free_gb(out) < a.min_free_gb:
                log(f'less than {a.min_free_gb} GB free: stopping'); break
            if max(gateway_sizes().values(), default=0) > a.rotate_gb * 1e9:
                rotate_gateways()
            if TOPOLOGY.stat().st_mtime != topology_mtime:          # the nodes rotated their keys
                time.sleep(60)                                        # let every node finish rotating
                if not heal('key rotation: network.json was rewritten'):
                    (out / 'PAUSE').touch(); log('PAUSE set: the localnet does not deliver, needs a look'); continue
                topology_mtime = TOPOLOGY.stat().st_mtime
            reapplied = ensure_netem(full=(done % a.full_check == 0))
            if done % a.full_check == 0 and not arp_ok():
                raise SystemExit('ARP table limit was reset (Docker restarted?): run netem/vm_sysctl.sh')
            capture = a.capture_every > 0 and int(row['run_id'][1:]) % a.capture_every == 0
            timeout = int(row['duration_s']) + int(row['warmup_s']) + int(row['drain_s']) + 600
            log(f'{row["run_id"]} {row["dataset"]} {row["condition"]:22s} {row["strategy"]:10s} '
                f'{row["clients"]} clients, {row["pkt_per_s"]} pkt/s{" +pcap" if capture else ""}')
            keep = int(row['run_id'][1:]) % a.keep_pcap_every == 0
            res = run_one(row, out, capture, timeout, a.extract_wire, keep)
            if not res['delivered_min_pct']:                         # nothing arrived at all: broken network
                log(f'   -> {res["status"]}, nothing delivered: healing and repeating {row["run_id"]} once')
                if not heal('a run delivered nothing'):
                    (out / 'PAUSE').touch(); log('PAUSE set: the localnet does not deliver, needs a look'); continue
                topology_mtime = TOPOLOGY.stat().st_mtime
                res = run_one(row, out, capture, timeout, a.extract_wire, keep)
            res['netem_reapplied'] = reapplied
            with open(manifest, 'a', newline='') as fh:
                cols = MANIFEST_COLS if new_manifest else next(csv.reader(open(manifest)))
                w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore')
                if new_manifest:
                    w.writeheader(); new_manifest = False
                w.writerow({**row, **res})
            (out / 'runs' / row['run_id'] / 'DONE').write_text(res['finished'])
            log(f'   -> {res["status"]}: delivered {res["delivered_min_pct"]}%, p95 {res["latency_p95_ms"]} ms, '
                f'{res["verdict"]}; gw {res["gateway_wire_mb"]} MB, pcap {res["pcap_mb"]} MB')
            done += 1
        log(f'finished {done} runs this session')
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
