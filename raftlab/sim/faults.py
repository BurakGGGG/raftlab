"""Fault schedules.

A schedule is plain data generated from the seed *before* the run starts.  That
is what makes shrinking possible: the shrinker deletes faults from the list and
replays, instead of trying to steer a random stream.

Some faults are *targeted*: "crash whoever is leader at t=3200" or "isolate the
leader into a minority".  Those are resolved when the fault fires, because the
interesting failures in a consensus protocol cluster around leadership changes,
and uniformly random victims rarely hit them.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from .config import SimConfig


@dataclass(frozen=True)
class Fault:
    fid: int
    kind: str  # "partition" | "crash" | "pause"
    start: int
    end: int
    # crash: "fixed" (use node) or "leader" (whoever leads when it fires)
    # partition: "fixed" (use groups), "isolate_leader", "leader_minority"
    target: str = "fixed"
    groups: tuple[tuple[int, ...], ...] | None = None
    node: int | None = None
    # A pause that also freezes the node's clock: a suspended VM, not a GC
    # pause.  On resume the node believes almost no time has passed, which is
    # precisely the assumption a leader lease rests on.
    freeze: bool = False

    def describe(self) -> str:
        span = f"[{self.start:>6}..{self.end:>6}]"
        if self.kind == "partition":
            if self.target != "fixed":
                return f"{span} partition ({self.target})"
            gs = " | ".join(",".join(str(n) for n in g) for g in (self.groups or ()))
            return f"{span} partition {gs}"
        who = "the leader" if self.target == "leader" else f"node {self.node}"
        frozen = " (clock frozen)" if self.freeze else ""
        return f"{span} {self.kind} {who}{frozen}"


def generate(cfg: SimConfig, rng: random.Random) -> list[Fault]:
    faults: list[Fault] = []
    heal = cfg.heal_time()
    window = max(1, min(int(cfg.duration_ms * cfg.fault_window), heal - 1))
    seconds = cfg.duration_ms / 1000.0
    fid = 0

    for _ in range(int(seconds * cfg.partitions_per_sec)):
        start = rng.randint(0, window)
        end = min(heal, start + rng.randint(200, cfg.max_partition_ms))
        if end <= start:
            continue
        roll = rng.random()
        groups: tuple[tuple[int, ...], ...] | None
        if roll < 0.35:
            target, groups = "isolate_leader", None
        elif roll < 0.60:
            target, groups = "leader_minority", None
        else:
            nodes = list(range(cfg.n_nodes))
            rng.shuffle(nodes)
            if cfg.n_nodes >= 5 and rng.random() < 0.3:
                a = rng.randint(1, cfg.n_nodes - 2)
                b = rng.randint(a + 1, cfg.n_nodes - 1)
                groups = (
                    tuple(sorted(nodes[:a])),
                    tuple(sorted(nodes[a:b])),
                    tuple(sorted(nodes[b:])),
                )
            else:
                a = rng.randint(1, cfg.n_nodes - 1)
                groups = (tuple(sorted(nodes[:a])), tuple(sorted(nodes[a:])))
            target = "fixed"
        faults.append(Fault(fid, "partition", start, end, target, groups))
        fid += 1

    for _ in range(int(seconds * cfg.crashes_per_sec)):
        start = rng.randint(0, window)
        end = min(heal, start + rng.randint(50, cfg.max_crash_ms))
        if end <= start:
            continue
        # A pause keeps the process alive but stops it dead: its timers freeze,
        # its inbox queues up and floods in on resume.  That is a GC pause or a
        # descheduled VM, and it is far better than a crash at producing a
        # leader that does not yet know it has been replaced.
        kind = "pause" if rng.random() < cfg.pause_fraction else "crash"
        freeze = kind == "pause" and rng.random() < cfg.clock_freeze_prob
        if rng.random() < 0.5:
            faults.append(Fault(fid, kind, start, end, "leader", freeze=freeze))
        else:
            faults.append(
                Fault(fid, kind, start, end, "fixed", node=rng.randrange(cfg.n_nodes), freeze=freeze)
            )
        fid += 1

    # ---- correlated bursts -------------------------------------------------
    # Independent uniformly-scattered faults are the weakest schedule there is.
    # Real outages come in bursts, and consensus bugs live in the window around
    # a leadership change, so a fraction of runs get a burst of short faults
    # spaced about one election apart.  This is a generic nemesis, not a
    # scenario: it only says "keep leadership moving", never which bug to hit.
    if rng.random() < cfg.leader_churn_prob:
        start = rng.randint(0, window)
        gap = rng.randint(120, 420)
        dur = rng.randint(80, 320)
        kind = "crash" if rng.random() < 0.6 else "pause"
        for k in range(rng.randint(3, 8)):
            at = start + k * gap
            if at >= heal:
                break
            faults.append(Fault(fid, kind, at, min(heal, at + dur), "leader"))
            fid += 1

    if rng.random() < cfg.vote_churn_prob:
        # Very short outages of arbitrary nodes, spaced tighter than an election
        # timeout: this is what puts a crash *inside* somebody's election.
        start = rng.randint(0, window)
        gap = rng.randint(60, 220)
        for k in range(rng.randint(4, 10)):
            at = start + k * gap
            if at >= heal:
                break
            dur = rng.randint(40, 160)
            faults.append(
                Fault(fid, "crash", at, min(heal, at + dur), "fixed", node=rng.randrange(cfg.n_nodes))
            )
            fid += 1

    faults.sort(key=lambda f: (f.start, f.kind, f.fid))
    return renumber(faults)


def renumber(faults: list[Fault]) -> list[Fault]:
    return [replace(f, fid=i) for i, f in enumerate(faults)]
