"""Turn a failing seed into a minimal, readable reproduction.

A seed that fails is not yet a bug report: an 8-second run with a dozen
overlapping faults says nothing about *which* fault mattered.  Because a run is
a pure function of (config, seed, fault list), the fault list can be treated as
the input to a delta-debugging loop: drop a fault, re-run, keep the drop if the
same violation still fires.  What comes out is usually two or three faults and a
few hundred milliseconds -- something a human can actually read.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass

from .raft.bugs import NO_BUGS, Bugs
from .raft.types import RaftConfig
from .runner import CheckedRun, run_and_check
from .sim.cluster import substream
from .sim.config import SimConfig
from .sim.faults import Fault
from .sim.faults import generate as generate_faults


@dataclass
class Repro:
    seed: int
    cfg: SimConfig
    bugs: Bugs
    faults: list[Fault]
    kind: str
    detail: str = ""
    attempts: int = 0
    original_faults: int = 0
    original_ms: int = 0

    def run(self, trace: bool = True) -> CheckedRun:
        return run_and_check(self.cfg, self.seed, self.bugs, faults=self.faults, trace=trace)

    def describe(self) -> str:
        lines = [
            f"repro: seed={self.seed} bug={','.join(self.bugs.enabled_names()) or 'none'}"
            f" nodes={self.cfg.n_nodes} clients={self.cfg.n_clients}"
            f" duration={self.cfg.duration_ms}ms",
            f"violation: {self.kind}: {self.detail}",
        ]
        if self.original_faults:  # absent when the repro was loaded from JSON
            lines.append(
                f"reduced from {self.original_faults} faults / {self.original_ms}ms"
                f" to {len(self.faults)} faults / {self.cfg.duration_ms}ms"
                f" in {self.attempts} replays"
            )
        lines.append("faults:")
        lines += [f"  {f.describe()}" for f in self.faults] or ["  (none: the bug needs no faults at all)"]
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "seed": self.seed,
                "kind": self.kind,
                "detail": self.detail,
                "bugs": self.bugs.enabled_names(),
                "cfg": dataclasses.asdict(self.cfg),
                "faults": [dataclasses.asdict(f) for f in self.faults],
            },
            indent=2,
        )

    @staticmethod
    def from_json(text: str) -> Repro:
        d = json.loads(text)
        raft = RaftConfig(**d["cfg"].pop("raft"))
        cfg = SimConfig(raft=raft, **d["cfg"])
        bugs = Bugs(**dict.fromkeys(d["bugs"], True))
        faults = [Fault(**f) for f in d["faults"]]
        return Repro(d["seed"], cfg, bugs, faults, d["kind"], d.get("detail", ""))


def _kind(run: CheckedRun) -> str | None:
    return run.first.kind if run.first else None


def shrink(
    cfg: SimConfig,
    seed: int,
    bugs: Bugs = NO_BUGS,
    verbose: bool = False,
) -> Repro | None:
    faults = generate_faults(cfg, substream(seed, "faults"))
    base = run_and_check(cfg, seed, bugs, faults=faults)
    if base.ok:
        return None
    target = _kind(base)
    attempts = 1
    orig_faults, orig_ms = len(faults), cfg.duration_ms

    def fails(c: SimConfig, fs: list[Fault]) -> CheckedRun | None:
        nonlocal attempts
        attempts += 1
        r = run_and_check(c, seed, bugs, faults=fs)
        return r if _kind(r) == target else None

    def drop_pass(c: SimConfig, fs: list[Fault]) -> list[Fault]:
        """Repeat greedy removal until nothing more can go."""
        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(fs):
                candidate = fs[:i] + fs[i + 1 :]
                if fails(c, candidate):
                    fs = candidate
                    changed = True
                    if verbose:
                        print(f"  dropped a fault -> {len(fs)} left")
                else:
                    i += 1
        return fs

    # 1. drop faults one at a time (greedy delta debugging, to a fixpoint)
    faults = drop_pass(cfg, faults)

    # 2. shrink the run to just past the violation (never for liveness, whose
    #    definition depends on the length of the quiet tail)
    best = run_and_check(cfg, seed, bugs, faults=faults)
    if target != "NoProgress":
        end = best.first.time if best.first else cfg.duration_ms
        for slack in (50, 200, 600):
            shorter = dataclasses.replace(cfg, duration_ms=min(cfg.duration_ms, end + slack))
            if shorter.duration_ms >= cfg.duration_ms:
                break
            r = fails(shorter, faults)
            if r:
                cfg = shorter
                break

    # 3. fewer clients
    for n in range(1, cfg.n_clients):
        smaller = dataclasses.replace(cfg, n_clients=n)
        if fails(smaller, faults):
            cfg = smaller
            break

    # 4. one more pass over the faults now that the world is smaller
    faults = drop_pass(cfg, faults)

    final = run_and_check(cfg, seed, bugs, faults=faults)
    v = final.first
    return Repro(
        seed=seed,
        cfg=cfg,
        bugs=bugs,
        faults=faults,
        kind=v.kind if v else target or "?",
        detail=v.detail if v else "",
        attempts=attempts,
        original_faults=orig_faults,
        original_ms=orig_ms,
    )
