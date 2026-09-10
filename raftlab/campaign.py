"""Fuzz campaign: sweep seeds across configurations, in parallel.

Each (world profile, implementation variant) pair is run over a range of seeds.
Because a run is a pure function of its seed, the output is a table of
*measurements*, not anecdotes: how many seeds does it take to catch a given
bug, and how much simulated time did that buy us.
"""

from __future__ import annotations

import dataclasses
import multiprocessing as mp
import time
from dataclasses import dataclass
from typing import Any

from .raft.bugs import Bugs
from .runner import run_and_check
from .sim.config import PROFILES, SimConfig, swarm


@dataclass
class Spec:
    name: str
    bugs: Bugs
    profile: str = "normal"
    cfg: SimConfig | None = None
    note: str = ""
    raft_over: dict[str, Any] = dataclasses.field(default_factory=dict)

    def config(self, seed: int = 0) -> SimConfig:
        if self.profile == "swarm":
            cfg = swarm(seed)
            if self.cfg is not None:  # keep any protocol overrides (e.g. no no-op)
                cfg = dataclasses.replace(cfg, raft=dataclasses.replace(cfg.raft, **self.raft_over))
            return cfg
        return self.cfg if self.cfg is not None else PROFILES[self.profile]


@dataclass
class Outcome:
    seed: int
    ok: bool
    kind: str = ""
    detail: str = ""
    sim_ms: int = 0
    events: int = 0
    ops: int = 0
    elections: int = 0
    lin_unknown: bool = False
    read_p50: int = 0
    read_p99: int = 0
    committed: int = 0
    msgs: int = 0
    coverage: tuple[str, ...] = ()
    recovery_ms: int = -1   # time from the last heal to the next served request


@dataclass
class ConfigReport:
    spec: Spec
    outcomes: list[Outcome]
    wall: float

    @property
    def failures(self) -> list[Outcome]:
        return [o for o in self.outcomes if not o.ok]

    @property
    def first_failure(self) -> Outcome | None:
        return self.failures[0] if self.failures else None

    def kinds(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.failures:
            out[o.kind] = out.get(o.kind, 0) + 1
        return out

    def sim_seconds(self) -> float:
        return sum(o.sim_ms for o in self.outcomes) / 1000.0

    def events(self) -> int:
        return sum(o.events for o in self.outcomes)

    def mean_ops(self) -> float:
        return sum(o.ops for o in self.outcomes) / max(len(self.outcomes), 1)

    def mean_elections(self) -> float:
        return sum(o.elections for o in self.outcomes) / max(len(self.outcomes), 1)

    def mean(self, field: str) -> float:
        return sum(getattr(o, field) for o in self.outcomes) / max(len(self.outcomes), 1)


_SPEC: Spec | None = None
_RECOVERY = False


def _init(spec: Spec, recovery: bool = False) -> None:
    global _SPEC, _RECOVERY
    _SPEC = spec
    _RECOVERY = recovery


def measure_recovery(cfg, seed: int, bugs) -> int:
    """How long after the last fault heals does the cluster serve somebody?

    The faults are generated for the profile's own duration and then replayed in
    a much longer run, so the answer is not censored by the end of the run.
    -1 means it had still not recovered when the long run ended.
    """
    import dataclasses

    from .sim.cluster import Simulator, substream
    from .sim.faults import generate

    faults = generate(cfg, substream(seed, "faults"))
    heal = cfg.heal_time()
    long_cfg = dataclasses.replace(cfg, duration_ms=cfg.duration_ms * 3)
    sim = Simulator(long_cfg, seed, bugs, faults=faults, stop_on_violation=False).run()
    after = [
        o.ret for o in sim.history.ops if o.completed and o.ret is not None and o.ret > heal
    ]
    return min(after) - heal if after else -1


def _run_one(seed: int) -> Outcome:
    assert _SPEC is not None
    if _RECOVERY:
        cfg = _SPEC.config(seed)
        return Outcome(
            seed=seed, ok=True, recovery_ms=measure_recovery(cfg, seed, _SPEC.bugs)
        )
    r = run_and_check(_SPEC.config(seed), seed, _SPEC.bugs)
    v = r.first
    return Outcome(
        seed=seed,
        ok=r.ok,
        kind=v.kind if v else "",
        detail=v.detail if v else "",
        sim_ms=r.sim.stats["sim_end"],
        events=r.sim.stats["events"],
        ops=r.sim.stats["ops_completed"],
        elections=r.sim.stats["elections"],
        lin_unknown=bool(r.lin is not None and r.lin.unknown),
        read_p50=r.sim.stats["read_p50"],
        read_p99=r.sim.stats["read_p99"],
        committed=r.sim.stats["committed"],
        msgs=r.sim.stats["msgs_sent"],
        coverage=r.sim.stats["coverage"],
    )


def run_spec(spec: Spec, seeds: range, workers: int = 0, recovery: bool = False) -> ConfigReport:
    t0 = time.time()
    workers = workers or mp.cpu_count()
    if workers == 1:
        _init(spec, recovery)
        outcomes = [_run_one(s) for s in seeds]
    else:
        with mp.Pool(workers, initializer=_init, initargs=(spec, recovery)) as pool:
            outcomes = pool.map(_run_one, list(seeds), chunksize=8)
    return ConfigReport(spec, outcomes, time.time() - t0)


def percentile(values: list[int], q: float) -> int:
    return values[min(len(values) - 1, int(q * len(values)))]


def format_recovery(reports: list[ConfigReport], labels: list[str]) -> str:
    """How long the cluster takes to serve its next request after a heal."""
    width = max([len(x) for x in labels] + [16]) + 2
    head = (
        f"{'build'.ljust(width)}{'runs':>6}{'median':>9}{'p90':>8}{'p99':>8}{'worst':>9}"
        f"{'over 1s':>9}{'never':>7}"
    )
    lines = [head, "-" * len(head)]
    for label, r in zip(labels, reports):
        vals = sorted(o.recovery_ms for o in r.outcomes if o.recovery_ms >= 0)
        never = sum(1 for o in r.outcomes if o.recovery_ms < 0)
        if not vals:
            lines.append(f"{label.ljust(width)}{len(r.outcomes):>6}{'-':>9}")
            continue
        slow = sum(1 for v in vals if v > 1000)
        lines.append(
            f"{label.ljust(width)}{len(r.outcomes):>6}{percentile(vals, 0.5):>8}m"
            f"{percentile(vals, 0.9):>7}m{percentile(vals, 0.99):>7}m"
            f"{vals[-1]:>8}m{slow / len(vals) * 100:>8.1f}%{never:>7}"
        )
    return "\n".join(lines)


def format_coverage(reports: list[ConfigReport]) -> str:
    """How often each interesting situation was actually reached."""
    buckets: dict[str, int] = {}
    total = 0
    for r in reports:
        for o in r.outcomes:
            total += 1
            for name in o.coverage:
                buckets[name] = buckets.get(name, 0) + 1
    width = max([len(k) for k in buckets] + [30]) + 2
    head = f"{'situation reached'.ljust(width)}{'runs':>8}{'share':>9}"
    lines = [head, "-" * len(head)]
    for name, count in sorted(buckets.items(), key=lambda kv: -kv[1]):
        bar = "#" * int(count / max(total, 1) * 28)
        lines.append(f"{name.replace('_', ' ').ljust(width)}{count:>8}{count / total * 100:>8.1f}%  {bar}")
    lines.append("-" * len(head))
    lines.append(f"{total} runs")
    return "\n".join(lines)


def format_reads(reports: list[ConfigReport], labels: list[str]) -> str:
    head = (
        f"{'read path'.ljust(14)}{'seeds':>6}{'stale reads':>13}{'read p50':>10}"
        f"{'read p99':>10}{'log/run':>9}{'msgs/op':>9}"
    )
    lines = [head, "-" * len(head)]
    for label, r in zip(labels, reports):
        n = len(r.outcomes)
        bad = len(r.failures)
        ops = max(r.mean_ops(), 1e-9)
        lines.append(
            f"{label.ljust(14)}{n:>6}{f'{bad} ({bad / n * 100:.1f}%)':>13}"
            f"{r.mean('read_p50'):>9.0f}m{r.mean('read_p99'):>9.0f}m"
            f"{r.mean('committed'):>9.0f}{r.mean('msgs') / ops:>9.1f}"
        )
    return "\n".join(lines)


def format_table(reports: list[ConfigReport]) -> str:
    w = max([len(r.spec.name) for r in reports] + [13]) + 2
    head = (
        f"{'configuration'.ljust(w)}{'profile':<9}{'seeds':>6}{'caught':>7}{'rate':>7}"
        f"{'first':>7}{'ops/run':>9}{'elect':>7}  detected as"
    )
    lines = [head, "-" * len(head)]
    for r in reports:
        n = len(r.outcomes)
        f = len(r.failures)
        kinds = ", ".join(f"{k} x{v}" for k, v in sorted(r.kinds().items(), key=lambda kv: -kv[1]))
        first = r.first_failure.seed if r.first_failure else "-"
        lines.append(
            f"{r.spec.name.ljust(w)}{r.spec.profile:<9}{n:>6}{f:>7}{f / n * 100:>6.1f}%"
            f"{str(first):>7}{r.mean_ops():>9.0f}{r.mean_elections():>7.1f}  {kinds or '(clean)'}"
        )
    unknown = sum(1 for r in reports for o in r.outcomes if o.lin_unknown)
    total_sim = sum(r.sim_seconds() for r in reports)
    total_wall = sum(r.wall for r in reports)
    total_events = sum(r.events() for r in reports)
    lines.append("-" * len(head))
    if unknown:
        lines.append(
            f"note: the linearizability search hit its step budget on {unknown} run(s)"
            f" and reported UNKNOWN rather than a pass"
        )
    lines.append(
        f"{len(reports)} configurations, {sum(len(r.outcomes) for r in reports)} runs,"
        f" {total_events:,} events, {total_sim / 60:.1f} simulated minutes"
        f" in {total_wall:.1f}s wall ({total_sim / max(total_wall, 1e-9):.0f}x real time)"
    )
    return "\n".join(lines)
