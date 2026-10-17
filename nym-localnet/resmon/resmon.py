#!/usr/bin/env python3
"""Resource monitor for localnet experiments: samples the machine once per second while a run is
going, and judges afterwards whether the run's TIMING can be trusted.

A run can deliver 100 % of its messages and still be useless for flow matching: if the host is
saturated, packets wait in run queues and sockets for tens or hundreds of ms, which is the same
magnitude as the mixing delay the classifiers measure.  This tool records the signals that reveal
that, and turns them into a verdict.

  resmon.py record OUT.csv [--interval 1] [--procs nym-node,nym-load-test] [--cores 0-31]
      sample until SIGINT/SIGTERM; one row per second (Linux: /proc; macOS: top/ps/docker stats)
  resmon.py verdict OUT.csv [--load-test-log LOG] [--json OUT.json]
      summarise the samples (+ the load test's own scheduler-lag/delivery lines) and print
      OK / SUSPECT / INVALID with the reasons
  resmon.py wrap OUT_DIR -- COMMAND ...
      record while COMMAND runs, then print the verdict (monitor.csv, verdict.json in OUT_DIR)

Thresholds (override with --thresholds k=v,...):
  idle_min_pct=10       machine idle below this ...
  idle_low_frac=0.05    ... in more than this fraction of samples -> INVALID (sustained saturation);
                        in fewer (isolated dips) -> SUSPECT
  idle_p05_pct=25       5th percentile of idle below this       -> SUSPECT
  runq_ratio=1.0        runnable tasks > ratio x assigned cores for >5 % of samples -> SUSPECT
  proc_core_pct=95      one monitored process >= this % of a core for >10 % of samples -> SUSPECT
  steal_pct=5           CPU steal above this (VMs)              -> SUSPECT
  sched_lag_p99_ms=5    load test scheduler lag p99 above this  -> INVALID
  delivered_min_pct=95  delivery below this                     -> INVALID
  drops=0               growth of kernel drop counters above this -> SUSPECT
  foreign_cores=4       other users' processes using more than this many cores -> SUSPECT
"""
import argparse, csv, json, os, platform, re, signal, subprocess, sys, time
from pathlib import Path

LINUX = platform.system() == 'Linux'
TH = dict(idle_min_pct=10, idle_low_frac=0.05, idle_p05_pct=25, runq_ratio=1.0, proc_core_pct=95, steal_pct=5,
          sched_lag_p99_ms=5, delivered_min_pct=95, drops=0, foreign_cores=4)
DROP_KEYS = [('Udp', 'RcvbufErrors'), ('Udp', 'SndbufErrors'), ('Udp', 'InErrors'),
             ('TcpExt', 'ListenOverflows'), ('TcpExt', 'ListenDrops'), ('TcpExt', 'TCPBacklogDrop'),
             ('Tcp', 'RetransSegs')]


# ------------------------------------------------------------------ Linux sampling (/proc)
def parse_cores(spec, ncpu):
    if not spec:
        return list(range(ncpu))
    out = []
    for part in spec.split(','):
        a, _, b = part.partition('-')
        out += list(range(int(a), int(b or a) + 1))
    return out


def cpu_times():
    """per-cpu (busy, idle, steal) jiffies from /proc/stat"""
    res = {}
    for line in open('/proc/stat'):
        if not line.startswith('cpu') or line.startswith('cpu '):
            continue
        f = line.split(); n = int(f[0][3:]); v = list(map(int, f[1:]))
        idle = v[3] + v[4]; steal = v[7] if len(v) > 7 else 0
        res[n] = (sum(v[:8]) - idle - steal, idle, steal)
    return res


def snmp_counters():
    out = {}
    for path in ('/proc/net/snmp', '/proc/net/netstat'):
        try:
            lines = open(path).read().splitlines()
        except OSError:
            continue
        for h, v in zip(lines[0::2], lines[1::2]):
            names, vals = h.split()[1:], v.split()[1:]
            sect = h.split(':')[0]
            for k, x in zip(names, vals):
                out[(sect, k)] = int(x)
    return out


def proc_table(names, me):
    """pid -> (comm, user_is_me, utime+stime jiffies) for all processes; names select the monitored ones"""
    tab = {}
    for d in os.listdir('/proc'):
        if not d.isdigit():
            continue
        try:
            st = open(f'/proc/{d}/stat').read()
            uid = os.stat(f'/proc/{d}').st_uid
        except OSError:
            continue
        r = st.rfind(')'); comm = st[st.find('(') + 1:r]; f = st[r + 2:].split()
        tab[int(d)] = (comm, uid == me, int(f[11]) + int(f[12]))
    return tab


def record_linux(out, interval, names, cores):
    hz = os.sysconf('SC_CLK_TCK'); me = os.getuid(); ncpu = os.cpu_count()
    cores = parse_cores(cores, ncpu)
    prev_cpu, prev_proc, prev_snmp, t_prev = cpu_times(), proc_table(names, me), snmp_counters(), time.time()
    snmp0 = dict(prev_snmp)
    w = csv.writer(out); w.writerow(['t', 'idle_pct', 'idle_assigned_pct', 'steal_pct', 'runnable', 'load1',
                                     'mem_avail_gb', 'max_proc_core_pct', 'max_proc', 'mon_procs', 'foreign_cores']
                                    + [f'{a}.{b}' for a, b in DROP_KEYS])
    stop = {'v': False}
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: stop.__setitem__('v', True))
    while not stop['v']:
        time.sleep(interval)
        now = time.time(); dt = now - t_prev; t_prev = now
        cur = cpu_times()
        def pct(cs, idx):
            num = sum(cur[c][idx] - prev_cpu[c][idx] for c in cs if c in cur)
            den = sum(sum(cur[c]) - sum(prev_cpu[c]) for c in cs if c in cur) or 1
            return 100.0 * num / den
        idle_all, idle_asg, steal = pct(cur.keys(), 1), pct(cores, 1), pct(cur.keys(), 2)
        prev_cpu = cur
        la = open('/proc/loadavg').read().split(); runnable = int(la[3].split('/')[0]) - 1
        mem = next(int(l.split()[1]) for l in open('/proc/meminfo') if l.startswith('MemAvailable')) / 1048576
        tab = proc_table(names, me)
        maxp, maxname, nmon, foreign = 0.0, '', 0, 0.0
        for pid, (comm, mine, j) in tab.items():
            if pid not in prev_proc:
                continue
            p = 100.0 * (j - prev_proc[pid][2]) / hz / dt
            if not mine:
                foreign += p / 100.0
            if any(comm.startswith(n[:15]) for n in names):
                nmon += 1
                if p > maxp:
                    maxp, maxname = p, f'{comm}:{pid}'
        prev_proc = tab
        sn = snmp_counters()
        w.writerow([f'{now:.1f}', f'{idle_all:.1f}', f'{idle_asg:.1f}', f'{steal:.2f}', runnable, la[0],
                    f'{mem:.1f}', f'{maxp:.1f}', maxname, nmon, f'{foreign:.2f}']
                   + [sn.get(k, 0) - snmp0.get(k, 0) for k in DROP_KEYS])
        out.flush()


# ------------------------------------------------------------------ macOS sampling (Docker localnet)
def record_macos(out, interval, names, cores):
    w = csv.writer(out); w.writerow(['t', 'idle_pct', 'idle_assigned_pct', 'steal_pct', 'runnable', 'load1',
                                     'mem_avail_gb', 'max_proc_core_pct', 'max_proc', 'mon_procs', 'foreign_cores',
                                     'docker_max_cpu_pct', 'docker_max_name'])
    stop = {'v': False}
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda *_: stop.__setitem__('v', True))
    have_docker = subprocess.run(['which', 'docker'], capture_output=True).returncode == 0
    while not stop['v']:
        t0 = time.time()
        top = subprocess.run(['top', '-l', '2', '-n', '0', '-s', str(max(1, int(interval)))], capture_output=True, text=True).stdout
        m = re.findall(r'CPU usage: [\d.]+% user, [\d.]+% sys, ([\d.]+)% idle', top)
        idle = float(m[-1]) if m else float('nan')
        load1 = os.getloadavg()[0]
        ps = subprocess.run(['ps', '-A', '-o', 'pcpu=,comm='], capture_output=True, text=True).stdout.splitlines()
        maxp, maxname, nmon = 0.0, '', 0
        for line in ps:
            p, _, comm = line.strip().partition(' ')
            base = os.path.basename(comm.strip())
            if any(base.startswith(n) for n in names):
                nmon += 1
                if float(p) > maxp:
                    maxp, maxname = float(p), base[:40]
        dmax, dname = '', ''
        if have_docker:
            ds = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{.Name}} {{.CPUPerc}}'], capture_output=True, text=True).stdout.split('\n')
            vals = [(float(x.split()[1].rstrip('%')), x.split()[0]) for x in ds if x.strip() and x.split()[1].rstrip('%').replace('.', '', 1).isdigit()]
            if vals:
                dmax, dname = max(vals)
        w.writerow([f'{t0:.1f}', f'{idle:.1f}', f'{idle:.1f}', '0', '', f'{load1:.2f}', '', f'{maxp:.1f}', maxname, nmon, '', dmax, dname])
        out.flush()


# ------------------------------------------------------------------ verdict
def load_test_lines(path):
    """scheduler lag p99 and delivery rows from nym-load-test's console output"""
    res = {'sched_lag_p99_ms': None, 'delivered_pct': [], 'abandoned': 0}
    if not path or not Path(path).exists():
        return res
    for line in Path(path).read_text(errors='replace').splitlines():
        m = re.search(r'scheduler lag: p50 [\d.]+ ms, p99 ([\d.]+) ms', line)
        if m:
            res['sched_lag_p99_ms'] = float(m.group(1))
        m = re.match(r'\s*\S+\s+(\d+)\s+([\d.]+)%', line)
        if m and int(m.group(1)) > 0:
            res['delivered_pct'].append(float(m.group(2)))
        m = re.search(r'WARNING: (\d+) clients could not accept', line)
        if m:
            res['abandoned'] = int(m.group(1))
    return res


def pctl(xs, q):
    xs = sorted(x for x in xs if x == x)
    return xs[min(len(xs) - 1, int(q / 100 * len(xs)))] if xs else float('nan')


def verdict(csv_path, lt_log=None, th=TH, cores_assigned=None):
    rows = list(csv.DictReader(open(csv_path)))
    lt = load_test_lines(lt_log)
    f = lambda k: [float(r[k]) for r in rows if r.get(k) not in (None, '', 'nan')]
    reasons_invalid, reasons_suspect, stats = [], [], {}
    idle = f('idle_assigned_pct')
    if idle:
        stats.update(idle_min=min(idle), idle_p05=pctl(idle, 5), idle_median=pctl(idle, 50))
        low_frac = sum(i < th['idle_min_pct'] for i in idle) / len(idle)
        stats['idle_low_frac'] = low_frac
        if low_frac > th['idle_low_frac']:
            # sustained saturation: the run's timing cannot be trusted
            reasons_invalid.append(f'machine idle below {th["idle_min_pct"]}% in {100*low_frac:.0f}% of samples (> {100*th["idle_low_frac"]:.0f}%)')
        elif low_frac > 0:
            # isolated dips: brief host load spikes, worth noting but not invalidating
            reasons_suspect.append(f'machine idle dipped below {th["idle_min_pct"]}% in {sum(i < th["idle_min_pct"] for i in idle)} sample(s) (min {min(idle):.0f}%)')
        elif pctl(idle, 5) < th['idle_p05_pct']:
            reasons_suspect.append(f'idle p05 {pctl(idle, 5):.0f}% (< {th["idle_p05_pct"]}%)')
    runq = f('runnable')
    if runq and cores_assigned:
        frac = sum(r > th['runq_ratio'] * cores_assigned for r in runq) / len(runq)
        stats['runq_over_frac'] = frac
        if frac > 0.05:
            reasons_suspect.append(f'run queue above {th["runq_ratio"]}x{cores_assigned} cores in {100*frac:.0f}% of samples')
    pc = f('max_proc_core_pct')
    if pc:
        frac = sum(p >= th['proc_core_pct'] for p in pc) / len(pc)
        stats.update(max_proc_core_p95=pctl(pc, 95), max_proc_pinned_frac=frac)
        if frac > 0.10:
            reasons_suspect.append(f'a monitored process pinned >= {th["proc_core_pct"]}% of a core in {100*frac:.0f}% of samples')
    st = f('steal_pct')
    if st and max(st) > th['steal_pct']:
        reasons_suspect.append(f'CPU steal up to {max(st):.1f}%')
    fc = f('foreign_cores')
    if fc:
        stats['foreign_cores_p95'] = pctl(fc, 95)
        if pctl(fc, 95) > th['foreign_cores']:
            reasons_suspect.append(f'other users used {pctl(fc, 95):.1f} cores (p95)')
    for a, b in DROP_KEYS:
        k = f'{a}.{b}'
        if rows and k in rows[-1] and rows[-1][k] not in ('', None):
            grow = float(rows[-1][k])
            stats[k] = grow
            if b != 'RetransSegs' and grow > th['drops']:
                reasons_suspect.append(f'kernel counter {k} grew by {grow:.0f}')
    if lt['sched_lag_p99_ms'] is not None:
        stats['sched_lag_p99_ms'] = lt['sched_lag_p99_ms']
        if lt['sched_lag_p99_ms'] > th['sched_lag_p99_ms']:
            reasons_invalid.append(f'load-test scheduler lag p99 {lt["sched_lag_p99_ms"]:.1f} ms (> {th["sched_lag_p99_ms"]} ms)')
    if lt['delivered_pct']:
        stats['delivered_min_pct'] = min(lt['delivered_pct'])
        if min(lt['delivered_pct']) < th['delivered_min_pct']:
            reasons_invalid.append(f'delivery {min(lt["delivered_pct"]):.1f}% (< {th["delivered_min_pct"]}%)')
    if lt['abandoned']:
        reasons_invalid.append(f'{lt["abandoned"]} clients could not keep up (overloaded)')
    v = 'INVALID' if reasons_invalid else 'SUSPECT' if reasons_suspect else 'OK'
    return {'verdict': v, 'invalid': reasons_invalid, 'suspect': reasons_suspect, 'samples': len(rows), 'stats': stats}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('record'); r.add_argument('out'); r.add_argument('--interval', type=float, default=1.0)
    r.add_argument('--procs', default='nym-node,nym-load-test'); r.add_argument('--cores', default='')
    v = sub.add_parser('verdict'); v.add_argument('csv'); v.add_argument('--load-test-log'); v.add_argument('--json')
    v.add_argument('--cores-assigned', type=int); v.add_argument('--thresholds', default='')
    wr = sub.add_parser('wrap'); wr.add_argument('outdir'); wr.add_argument('--procs', default='nym-node,nym-load-test')
    wr.add_argument('--cores', default=''); wr.add_argument('--load-test-log'); wr.add_argument('--thresholds', default='')
    argv = sys.argv[1:]
    command = []
    if argv and argv[0] == 'wrap':
        if '--' not in argv:
            ap.error('wrap: put the command after --')
        i = argv.index('--'); argv, command = argv[:i], argv[i + 1:]
    a = ap.parse_args(argv)
    a.command = command
    if getattr(a, 'thresholds', ''):
        for kv in a.thresholds.split(','):
            k, _, x = kv.partition('='); TH[k] = float(x)
    if a.cmd == 'record':
        names = [n for n in a.procs.split(',') if n]
        with open(a.out, 'w', newline='') as out:
            (record_linux if LINUX else record_macos)(out, a.interval, names, a.cores)
    elif a.cmd == 'verdict':
        res = verdict(a.csv, a.load_test_log, TH, a.cores_assigned)
        if a.json:
            Path(a.json).write_text(json.dumps(res, indent=1))
        print(f"{res['verdict']}: " + ('; '.join(res['invalid'] + res['suspect']) or 'no overload signs')
              + f"  ({res['samples']} samples; " + ', '.join(f'{k}={v:.3g}' for k, v in res['stats'].items() if isinstance(v, (int, float))) + ')')
        sys.exit(0 if res['verdict'] == 'OK' else 1 if res['verdict'] == 'SUSPECT' else 2)
    elif a.cmd == 'wrap':
        cmd = a.command
        Path(a.outdir).mkdir(parents=True, exist_ok=True)
        mon = subprocess.Popen([sys.executable, __file__, 'record', str(Path(a.outdir) / 'monitor.csv'),
                                '--procs', a.procs, '--cores', a.cores])
        try:
            rc = subprocess.call(cmd)
        finally:
            mon.send_signal(signal.SIGINT); mon.wait(timeout=10)
        ncores = len(parse_cores(a.cores, os.cpu_count())) if a.cores else os.cpu_count()
        res = verdict(Path(a.outdir) / 'monitor.csv', a.load_test_log, TH, ncores)
        (Path(a.outdir) / 'verdict.json').write_text(json.dumps(res, indent=1))
        print(f"run exit {rc}; {res['verdict']}: " + ('; '.join(res['invalid'] + res['suspect']) or 'no overload signs'))
        sys.exit(rc)


if __name__ == '__main__':
    main()
