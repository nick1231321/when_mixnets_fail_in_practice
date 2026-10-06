#!/usr/bin/env python3
"""Runs the localnet self-test binaries with different path selection configurations and
verifies, from their `[path-selection]` debug logs, that every route follows its configuration.

For every run it checks:
  * the client reports the expected strategy and routing of each traffic class;
  * every route uses one mixnode of each layer of data/network.json;
  * each traffic class follows its own flag: real packets and reply SURBs --real-routing, acks
    of real packets --real-ack-routing, loop cover packets --cover-routing and their acks
    --cover-ack-routing. `strategy` routes are counted in the session, `baseline` routes leave
    it untouched (with every class on baseline no session is created at all);
  * each session is created once, before its first route, and counts its packets 1, 2, 3, ...;
  * the strategy itself:
      K-HF  the configured layers are fixed for the session and every route uses those nodes,
      K/W   every layer preselects min(K, layer size) nodes and every route stays within them,
      α-SS  every roll matches its decision, new routes are not yet in the leading set, reused
            routes are, and the printed leading set size matches;
  * the Baseline strategy logs no path selection at all (no selector is created).

Whether the message came back is reported separately: a stopped mixnode loses the packets routed
through it without making the routing wrong. Use --require-delivery to fail runs on it too.

Usage (from nym-localnet, with the localnet up and the self-test built in release mode):
  python3 verify_path_selection.py              # every strategy parameter, 6 routing configs each
  python3 verify_path_selection.py --quick      # one parameter per strategy
  python3 verify_path_selection.py --combos all # all 16 routing configs per parameter
  python3 verify_path_selection.py --only khf   # runs whose name contains "khf"
  python3 verify_path_selection.py --build -v   # build first, print every check
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTINGS = ("strategy", "baseline")
KINDS = ("real", "reply-surb", "real-ack", "cover", "cover-ack")
# traffic classes: (Run attribute, command line flag)
CLASSES = (
    ("real", "--real-routing"),
    ("real_ack", "--real-ack-routing"),
    ("cover", "--cover-routing"),
    ("cover_ack", "--cover-ack-routing"),
)
CLASS_OF_KIND = {
    "real": "real",
    "reply-surb": "real",
    "real-ack": "real_ack",
    "cover": "cover",
    "cover-ack": "cover_ack",
}
LOG_FILTER = "warn,nym_topology::path_selection=debug"

ANSI = re.compile(r"\x1b\[[0-9;]*m")
TAG = "[path-selection] "

# path selection log messages, see common/topology/src/path_selection.rs
PATH = r"\[(?P<path>\d+, \d+, \d+)\]"
ROUTING_CONFIG = (
    r"real: (?P<real>\w+), real-ack: (?P<real_ack>\w+), "
    r"cover: (?P<cover>\w+), cover-ack: (?P<cover_ack>\w+)"
)
STARTUP = re.compile(rf"^routes will use (?P<strategy>.+); routing {ROUTING_CONFIG}$")
NEW_KHF = re.compile(r"^new session (?P<s>\S+): K-HF fixed nodes per layer \[(?P<fixed>.*)\]$")
NEW_KW = re.compile(
    r"^new session (?P<s>\S+): K/W \(K=(?P<k>\d+)\) preselected nodes per layer \[(?P<subsets>.*)\]$"
)
NEW_ALPHA = re.compile(r"^new session (?P<s>\S+): α-SS with α=(?P<alpha>[\d.]+)$")
ROUTE = re.compile(r"^session (?P<s>\S+) packet #(?P<n>\d+) \((?P<kind>[\w-]+)\): (?P<rest>.*)$")
KHF_ROUTE = re.compile(rf"^K-HF route {PATH} \(fixed \[(?P<fixed>.*)\]\)$")
KW_ROUTE = re.compile(rf"^K/W route {PATH}$")
ALPHA_ROUTE = re.compile(
    rf"^\[(?P<dice>[^\]]+)\] α-SS (?P<action>new|reused) route {PATH}"
    r"(?P<note> \(no unused routes left, reusing instead\))? \(leading set: (?P<size>\d+) routes\)$"
)
DICE_FIRST = "first packet, no roll → new"
DICE_ROLL = re.compile(
    r"^roll (?P<roll>[\d.]+) (?P<op><|≥) α=(?P<alpha>[\d.]+) → (?P<decision>reuse|new)$"
)
BASELINE_ROUTE = re.compile(
    rf"^session (?P<s>\S+) \((?P<kind>[\w-]+)\): baseline route {PATH}"
    r"(?P<configured> \(routing configured as baseline, session untouched\))?$"
)
KHF_REPLACED = re.compile(
    r"^session (?P<s>\S+): fixed node (?P<node>\d+) on layer (?P<layer>\d) left the topology, "
    r"replacing it with (?P<replacement>\d+)$"
)
KW_EMPTIED = re.compile(
    r"^session (?P<s>\S+): all preselected nodes on layer (?P<layer>\d) left the topology, "
    r"picking a new one$"
)
KW_CLAMPED = re.compile(
    r"^K=(?P<k>\d+) exceeds the (?P<n>\d+) nodes on layer (?P<layer>\d), using all of them$"
)
STDOUT_STRATEGY = re.compile(r"^Path selection strategy: (?P<strategy>.+)$", re.M)
STDOUT_ROUTING = re.compile(rf"^Routing: {ROUTING_CONFIG}$", re.M)


def path_of(match):
    return tuple(int(n) for n in match.group("path").split(", "))


@dataclass
class Strategy:
    """A path selection strategy, as configured on the command line."""

    name: str  # baseline | khf | kw | alpha
    value: object = None  # fixed layers (list), K (int) or α (float)

    @property
    def rust_debug(self):
        """The strategy as the client prints it (`{:?}` of PathSelectionStrategy)."""
        if self.name == "baseline":
            return "Baseline"
        if self.name == "khf":
            return f"KHopsFixed {{ fixed_layers: [{', '.join(map(str, self.value))}] }}"
        if self.name == "kw":
            return f"KOverW {{ k: {self.value} }}"
        return f"AlphaSticky {{ alpha: {self.value!r} }}"

    @property
    def label(self):
        if self.name == "baseline":
            return "baseline"
        if self.name == "khf":
            return f"khf-{'.'.join(map(str, self.value))}"
        return f"{self.name}-{self.value}"

    def binary_args(self):
        """Binary and strategy arguments of the dedicated self-test binary."""
        if self.name == "baseline":
            return "nym-self-test-baseline", []
        if self.name == "khf":
            return "nym-self-test-khf", ["--layers", ",".join(map(str, self.value))]
        if self.name == "kw":
            return "nym-self-test-kw", ["--k", str(self.value)]
        return "nym-self-test-alpha", ["--alpha", str(self.value)]

    def generic_arg(self):
        """Value of `--strategy` of the generic nym-self-test binary."""
        if self.name == "baseline":
            return "baseline"
        if self.name == "khf":
            return "khf:" + ",".join(map(str, self.value))
        return f"{self.name}:{self.value}"


@dataclass
class Run:
    strategy: Strategy
    real: str = "strategy"
    real_ack: str = "strategy"
    cover: str = "strategy"
    cover_ack: str = "strategy"
    generic: bool = False

    @property
    def routing_config(self):
        return {attr: getattr(self, attr) for attr, _ in CLASSES}

    @property
    def name(self):
        prefix = "generic-" if self.generic else ""
        routing = "_".join(f"{attr.replace('_', '')}-{getattr(self, attr)[0]}" for attr, _ in CLASSES)
        return f"{prefix}{self.strategy.label}_{routing}"

    def command(self, bin_dir, topology, size):
        if self.generic:
            binary, args = "nym-self-test", ["--strategy", self.strategy.generic_arg()]
        else:
            binary, args = self.strategy.binary_args()
        cmd = [str(bin_dir / binary), str(topology), "--size", str(size), *args]
        if self.strategy.name != "baseline":
            for attr, flag in CLASSES:
                cmd += [flag, getattr(self, attr)]
        return cmd

    def routing(self, kind):
        """Expected routing of `kind` packets."""
        return getattr(self, CLASS_OF_KIND[kind])


def routing_combos(combos):
    """Routing configs to run each strategy parameter with."""
    if combos == "all":
        return [
            dict(zip([attr for attr, _ in CLASSES], values))
            for values in __import__("itertools").product(ROUTINGS, repeat=len(CLASSES))
        ]
    # all on the strategy, one class at a time on baseline, all on baseline
    single = [{attr: "strategy" for attr, _ in CLASSES}]
    for attr, _ in CLASSES:
        single.append({**single[0], attr: "baseline"})
    single.append({attr: "baseline" for attr, _ in CLASSES})
    return single


def build_matrix(quick, combos):
    params = {
        "khf": [[1, 2]] if quick else [[1], [2], [1, 2], [1, 2, 3]],
        "kw": [2] if quick else [1, 2, 5],
        "alpha": [0.5] if quick else [0.0, 0.5, 1.0],
    }
    runs = [Run(Strategy("baseline"))]
    for name, values in params.items():
        for value in values:
            for routing in routing_combos(combos):
                runs.append(Run(Strategy(name, value), **routing))
    if not quick:
        # the generic binary parses the same strategies through `--strategy`
        for strategy in (
            Strategy("baseline"),
            Strategy("khf", [1, 2]),
            Strategy("kw", 2),
            Strategy("alpha", 0.8),
        ):
            runs.append(Run(strategy, generic=True))
    return runs


@dataclass
class Session:
    packets: int = 0
    fixed: list = None  # K-HF
    subsets: list = None  # K/W
    leading: list = field(default_factory=list)  # α-SS
    rolls: int = 0
    reuse_decisions: int = 0


@dataclass
class Result:
    run: Run
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)  # (kind, routing) -> routes
    delivered: bool = False
    exit_code: int = None
    duration: float = 0.0
    stats: dict = field(default_factory=dict)

    def ok(self, require_delivery):
        return not self.errors and (self.delivered or not require_delivery)


class Verifier:
    def __init__(self, run, layers, output, exit_code):
        self.run = run
        self.strategy = run.strategy
        self.layers = layers  # [set(layer1), set(layer2), set(layer3)]
        self.output = ANSI.sub("", output)
        self.result = Result(run, exit_code=exit_code)
        self.sessions = {}
        self.baseline_paths = defaultdict(list)  # kind -> paths with baseline routing
        self.strategy_paths = defaultdict(list)  # kind -> paths following the strategy
        self.clamped_layers = set()

    def error(self, msg):
        self.result.errors.append(msg)

    def check(self, cond, msg):
        if cond:
            self.result.checks.append(msg)
        else:
            self.error(msg)
        return cond

    def verify(self):
        self.check_stdout()
        messages = [line.split(TAG, 1)[1].strip() for line in self.output.splitlines() if TAG in line]
        if self.strategy.name == "baseline":
            self.check(
                not messages,
                f"baseline strategy logs no path selection (found {len(messages)} lines)",
            )
            return self.result
        for i, msg in enumerate(messages):
            self.handle(i, msg)
        self.check_summary(messages)
        return self.result

    def check_stdout(self):
        r = self.result
        r.delivered = "SUCCESS: received our own message back" in self.output
        m = STDOUT_STRATEGY.search(self.output)
        self.check(
            m is not None and m.group("strategy") == self.strategy.rust_debug,
            f"client reports strategy {self.strategy.rust_debug}"
            + ("" if m is None else f" (got {m.group('strategy')})"),
        )
        m = STDOUT_ROUTING.search(self.output)
        expected = self.run.routing_config
        if self.strategy.name == "baseline":
            expected = {attr: "strategy" for attr, _ in CLASSES}  # the flags are not passed
        self.check(
            m is not None and m.groupdict() == expected,
            f"client reports routing {expected}",
        )

    # --- per message ---------------------------------------------------------------------

    def handle(self, i, msg):
        where = f"line {i + 1}: {msg}"
        if m := STARTUP.match(msg):
            return self.on_startup(i, m, where)
        if m := ROUTE.match(msg):
            return self.on_route(m, where)
        if m := BASELINE_ROUTE.match(msg):
            return self.on_baseline_route(m, where)
        for regex, kind in ((NEW_KHF, "khf"), (NEW_KW, "kw"), (NEW_ALPHA, "alpha")):
            if m := regex.match(msg):
                return self.on_new_session(m, kind, where)
        if m := KHF_REPLACED.match(msg):
            return self.on_khf_replaced(m, where)
        if m := KW_EMPTIED.match(msg):
            return self.on_kw_emptied(m, where)
        if m := KW_CLAMPED.match(msg):
            self.clamped_layers.add(int(m.group("layer")))
            return None
        self.error(f"unrecognized path selection message: {where}")

    def on_startup(self, i, m, where):
        self.check(i == 0, "startup message comes first")
        self.check(
            m.group("strategy") == self.strategy.rust_debug,
            f"selector uses {self.strategy.rust_debug} (got {m.group('strategy')})",
        )
        routing = {attr: m.group(attr) for attr, _ in CLASSES}
        self.check(
            routing == self.run.routing_config,
            f"selector uses routing {self.run.routing_config} (got {routing})",
        )

    def check_path(self, path, where):
        valid = len(path) == 3 and all(node in self.layers[l] for l, node in enumerate(path))
        if not valid:
            self.error(f"route {list(path)} does not take one node of each layer: {where}")
        return valid

    def check_kind(self, kind, routing, where):
        if kind not in KINDS:
            self.error(f"unknown packet kind '{kind}': {where}")
            return
        self.result.counts[(kind, routing)] += 1
        expected = self.run.routing(kind)
        if routing != expected:
            self.error(f"{kind} route uses {routing} routing, expected {expected}: {where}")

    def on_new_session(self, m, kind, where):
        s = m.group("s")
        if kind != self.strategy.name:
            self.error(f"{kind} session in a {self.strategy.name} run: {where}")
            return
        if s in self.sessions:
            self.error(f"session {s} created twice: {where}")
            return
        session = self.sessions[s] = Session()

        if kind == "khf":
            fixed = [None if v == "None" else int(re.fullmatch(r"Some\((\d+)\)", v).group(1))
                     for v in m.group("fixed").split(", ")]
            expected_layers = [l + 1 for l, node in enumerate(fixed) if node is not None]
            self.check(expected_layers == self.strategy.value,
                       f"session {s} fixes layers {self.strategy.value} (got {expected_layers})")
            for l, node in enumerate(fixed):
                if node is not None and node not in self.layers[l]:
                    self.error(f"fixed node {node} is not on layer {l + 1}: {where}")
            session.fixed = fixed

        elif kind == "kw":
            k = int(m.group("k"))
            self.check(k == self.strategy.value, f"session {s} uses K={self.strategy.value}")
            subsets = [[int(n) for n in group.split(", ") if n]
                       for group in re.findall(r"\[([\d, ]*)\]", m.group("subsets"))]
            if not self.check(len(subsets) == 3, f"session {s} preselects nodes on 3 layers"):
                return
            for l, subset in enumerate(subsets):
                want = min(k, len(self.layers[l]))
                self.check(len(set(subset)) == len(subset) == want,
                           f"session {s} preselects {want} distinct nodes on layer {l + 1} (got {subset})")
                self.check(set(subset) <= self.layers[l],
                           f"preselected nodes {subset} are on layer {l + 1}")
                if k > len(self.layers[l]):
                    self.check(l + 1 in self.clamped_layers,
                               f"K={k} > {len(self.layers[l])} nodes on layer {l + 1} is warned about")
            session.subsets = [set(subset) for subset in subsets]

        else:
            alpha = float(m.group("alpha"))
            self.check(alpha == self.strategy.value, f"session {s} uses α={self.strategy.value}")

    def on_route(self, m, where):
        s, n, kind, rest = m.group("s"), int(m.group("n")), m.group("kind"), m.group("rest")
        self.check_kind(kind, "strategy", where)
        session = self.sessions.get(s)
        if session is None:
            self.error(f"route of session {s} before the session was created: {where}")
            return
        if n != session.packets + 1:
            self.error(f"session {s} counted packet #{n} after #{session.packets}: {where}")
        session.packets = n

        regex = {"khf": KHF_ROUTE, "kw": KW_ROUTE, "alpha": ALPHA_ROUTE}[self.strategy.name]
        route = regex.match(rest)
        if route is None:
            self.error(f"route is not a {self.strategy.name} route: {where}")
            return
        path = path_of(route)
        if not self.check_path(path, where):
            return
        self.strategy_paths[kind].append(path)
        getattr(self, f"verify_{self.strategy.name}")(session, route, path, where)

    def verify_khf(self, session, route, path, where):
        printed = route.group("fixed")
        expected = ", ".join("None" if node is None else f"Some({node})" for node in session.fixed)
        if printed != expected:
            self.error(f"route prints fixed [{printed}], session has [{expected}]: {where}")
        for l, node in enumerate(session.fixed):
            if node is not None and path[l] != node:
                self.error(f"layer {l + 1} uses {path[l]} instead of fixed node {node}: {where}")

    def verify_kw(self, session, route, path, where):
        for l, node in enumerate(path):
            if node not in session.subsets[l]:
                self.error(
                    f"layer {l + 1} uses {node}, not preselected {sorted(session.subsets[l])}: {where}"
                )

    def verify_alpha(self, session, route, path, where):
        alpha = self.strategy.value
        dice, action = route.group("dice"), route.group("action")
        size, note = int(route.group("size")), route.group("note") is not None
        leading = session.leading

        if not leading:
            if dice != DICE_FIRST or action != "new":
                self.error(f"first route of the session must be new without a roll: {where}")
            decision = "new"
        else:
            roll = DICE_ROLL.match(dice)
            if roll is None:
                self.error(f"later routes must roll: {where}")
                return
            value, op, decision = float(roll.group("roll")), roll.group("op"), roll.group("decision")
            if float(roll.group("alpha")) != alpha:
                self.error(f"roll compares against α={roll.group('alpha')}, expected {alpha}: {where}")
            if (op == "<") != (decision == "reuse"):
                self.error(f"roll operator {op} does not match decision {decision}: {where}")
            # the roll is printed with 4 decimals, so it is ambiguous right at α
            if abs(value - alpha) >= 1e-4 and (value < alpha) != (op == "<"):
                self.error(f"roll {value} {op} α={alpha} is wrong: {where}")
            session.rolls += 1
            session.reuse_decisions += decision == "reuse"

        if action == "new":
            if decision != "new":
                self.error(f"new route after a reuse decision: {where}")
            if path in leading:
                self.error(f"new route {list(path)} is already in the leading set: {where}")
            leading.append(path)
        else:
            if path not in leading:
                self.error(f"reused route {list(path)} is not in the leading set: {where}")
            all_routes = len(self.layers[0]) * len(self.layers[1]) * len(self.layers[2])
            if decision == "new" and not (note and len(leading) >= all_routes):
                self.error(f"reused after a 'new' decision with unused routes left: {where}")
            if decision == "reuse" and note:
                self.error(f"'no unused routes left' note on a reuse decision: {where}")
        if size != len(leading):
            self.error(f"leading set printed as {size}, expected {len(leading)}: {where}")

    def on_baseline_route(self, m, where):
        kind = m.group("kind")
        if m.group("configured") is None:
            # plain baseline routes come from a selector running the Baseline strategy, which
            # the client never creates
            self.error(f"baseline-strategy route from a {self.strategy.name} selector: {where}")
            return
        self.check_kind(kind, "baseline", where)
        path = path_of(m)
        if self.check_path(path, where):
            self.baseline_paths[kind].append(path)

    def on_khf_replaced(self, m, where):
        session = self.sessions.get(m.group("s"))
        layer, node = int(m.group("layer")) - 1, int(m.group("node"))
        replacement = int(m.group("replacement"))
        if session is None or session.fixed is None or session.fixed[layer] != node:
            self.error(f"replacement of a node that was not fixed: {where}")
            return
        self.check(replacement in self.layers[layer], f"replacement {replacement} is on layer {layer + 1}")
        session.fixed[layer] = replacement
        self.result.warnings.append(f"fixed node replaced: {where}")

    def on_kw_emptied(self, m, where):
        # the log does not say which node was picked, so accept any node of that layer from now on
        session = self.sessions.get(m.group("s"))
        if session is not None and session.subsets is not None:
            layer = int(m.group("layer")) - 1
            session.subsets[layer] = set(self.layers[layer])
        self.result.warnings.append(f"preselected nodes replaced: {where}")

    # --- whole run -----------------------------------------------------------------------

    def check_summary(self, messages):
        self.check(any(STARTUP.match(msg) for msg in messages), "selector startup message is logged")
        counts = self.result.counts
        for kind in ("real", "reply-surb", "real-ack"):
            routing = self.run.routing(kind)
            self.check(counts[(kind, routing)] > 0, f"{kind} routes are logged with {routing} routing")
        for kind in ("cover", "cover-ack"):
            if counts[(kind, self.run.routing(kind))] == 0:
                self.result.warnings.append(f"no {kind} routes were logged during the run")
        if any(routing == "strategy" for (_, routing) in counts):
            self.check(len(self.sessions) == 1, f"one session (sending to ourselves), got {len(self.sessions)}")
        else:
            self.check(not self.sessions, "no session is created when every class is on baseline")

        stats = self.result.stats
        for kind, paths in sorted(self.strategy_paths.items()):
            stats[f"{kind} distinct routes"] = len(set(paths))
        for kind, paths in sorted(self.baseline_paths.items()):
            stats[f"{kind} (baseline) distinct routes"] = len(set(paths))
        for s, session in self.sessions.items():
            stats[f"session {s} packets"] = session.packets
            if self.strategy.name == "alpha":
                stats["leading set"] = len(session.leading)
                if session.rolls:
                    stats["reuse rate"] = f"{session.reuse_decisions / session.rolls:.2f} (α={self.strategy.value})"
            if self.strategy.name == "khf":
                self.check_khf_baseline_routes(session)

    def check_khf_baseline_routes(self, session):
        """Baseline routes should ignore the fixed nodes; with many of them that becomes visible."""
        fixed = [(l, node) for l, node in enumerate(session.fixed) if node is not None]
        for kind, paths in self.baseline_paths.items():
            off = sum(any(path[l] != node for l, node in fixed) for path in paths)
            self.result.stats[f"{kind} (baseline) routes off the fixed nodes"] = f"{off}/{len(paths)}"
            if len(paths) >= 30 and off == 0:
                self.error(f"{len(paths)} baseline {kind} routes all use the fixed nodes")


def load_layers(topology):
    rewarded = json.loads(topology.read_text())["rewarded_set"]
    return [set(rewarded[f"layer{l}"]) for l in (1, 2, 3)]


def execute(run, args, layers, log_dir):
    cmd = run.command(args.bin_dir, args.topology, args.size)
    env = dict(os.environ, RUST_LOG=LOG_FILTER, NO_COLOR="1")
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, timeout=args.timeout)
        output = proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace")
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as err:
        output = (err.stdout or b"").decode(errors="replace") + (err.stderr or b"").decode(errors="replace")
        exit_code = None
    (log_dir / f"{run.name}.log").write_text(" ".join(cmd) + "\n\n" + output)

    result = Verifier(run, layers, output, exit_code).verify()
    result.duration = time.monotonic() - started
    if exit_code is None:
        result.warnings.append(f"timed out after {args.timeout}s")
    return result


def print_result(result, args):
    ok = result.ok(args.require_delivery)
    status = "PASS" if ok else "FAIL"
    delivery = "delivered" if result.delivered else f"NOT delivered (exit {result.exit_code})"
    print(f"\n[{status}] {result.run.name}  ({result.duration:.1f}s, {delivery})")
    if result.counts:
        routes = ", ".join(f"{kind}/{routing}: {n}" for (kind, routing), n in sorted(result.counts.items()))
        print(f"    routes   {routes}")
    for key, value in result.stats.items():
        print(f"    stats    {key}: {value}")
    if args.verbose:
        for check in dict.fromkeys(result.checks):
            print(f"    ok       {check}")
    for warning in result.warnings[:5]:
        print(f"    warning  {warning}")
    for error in result.errors[: args.max_errors]:
        print(f"    ERROR    {error}")
    if len(result.errors) > args.max_errors:
        print(f"    ... and {len(result.errors) - args.max_errors} more errors")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="one parameter per strategy")
    parser.add_argument("--combos", choices=("single", "all"), default="single",
                        help="routing configs per parameter: 'single' (default: all strategy, each "
                             "class alone on baseline, all baseline) or 'all' 16")
    parser.add_argument("--only", help="run only configurations whose name contains this")
    parser.add_argument("--list", action="store_true", help="list the configurations and exit")
    parser.add_argument("--build", action="store_true", help="cargo build --release the self-test first")
    parser.add_argument("--size", type=int, default=20000, help="message size in bytes (default 20000)")
    parser.add_argument("--timeout", type=int, default=120, help="seconds per run (default 120)")
    parser.add_argument("--topology", type=Path, default=HERE / "data" / "network.json")
    parser.add_argument("--bin-dir", type=Path, default=HERE / "self-test" / "target" / "release")
    parser.add_argument("--log-dir", type=Path, default=HERE / "verify-logs")
    parser.add_argument("--require-delivery", action="store_true", help="fail runs whose message did not arrive")
    parser.add_argument("--max-errors", type=int, default=10, help="errors printed per run")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every passed check")
    args = parser.parse_args()

    runs = build_matrix(args.quick, args.combos)
    if args.only:
        runs = [run for run in runs if args.only in run.name]
    if args.list:
        for run in runs:
            print(f"{run.name:55} {' '.join(run.command(args.bin_dir, args.topology, args.size)[1:])}")
        return 0
    if not runs:
        print(f"no configuration matches '{args.only}'")
        return 2

    if args.build:
        subprocess.run(
            ["cargo", "build", "--release", "--manifest-path", str(HERE / "self-test" / "Cargo.toml")],
            check=True,
        )
    if not args.topology.exists():
        print(f"topology {args.topology} not found, is the localnet up? (./localnet.sh up)")
        return 2
    layers = load_layers(args.topology)
    args.log_dir.mkdir(parents=True, exist_ok=True)

    print(f"{len(runs)} runs, layers {[sorted(layer) for layer in layers]}, logs in {args.log_dir}")
    results = []
    for i, run in enumerate(runs, 1):
        print(f"[{i}/{len(runs)}] {run.name} ...", end="", flush=True)
        result = execute(run, args, layers, args.log_dir)
        print(" done", flush=True)
        print_result(result, args)
        results.append(result)

    failed = [r for r in results if not r.ok(args.require_delivery)]
    undelivered = [r for r in results if not r.delivered]
    print("\n" + "=" * 72)
    print(f"{len(results) - len(failed)}/{len(results)} runs passed the routing checks"
          + (" and delivery" if args.require_delivery else ""))
    if undelivered:
        print(f"{len(undelivered)} runs did not get their message back (see the logs; is a node down?)")
    for result in failed:
        reason = result.errors[0] if result.errors else "message not delivered"
        print(f"  FAIL {result.run.name}: {reason}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
