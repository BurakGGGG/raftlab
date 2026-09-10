"""Command line front end: run, fuzz, shrink, replay."""

from __future__ import annotations

import argparse
import dataclasses
from typing import Any

from .campaign import (
    Spec,
    format_coverage,
    format_reads,
    format_recovery,
    format_table,
    run_spec,
)
from .raft.bugs import Bugs
from .runner import run_and_check
from .shrink import Repro, shrink
from .sim.config import PROFILES, SimConfig, swarm


# The three builds compared by `ablation` and `recovery`: plain figure-2 Raft,
# plus each of the two liveness extensions from the dissertation.
PRE_VOTE_VARIANTS: list[tuple[dict[str, Any], str]] = [
    ({"pre_vote": False, "leader_stickiness": False}, "plain"),
    ({"pre_vote": True, "leader_stickiness": False}, "pre-vote"),
    ({"pre_vote": True, "leader_stickiness": True}, "pre-vote+sticky"),
]


def build_config(args) -> SimConfig:
    if args.profile == "swarm":
        cfg = swarm(getattr(args, "seed", 1) or 1)
    else:
        cfg = PROFILES[args.profile]
    over: dict[str, Any] = {}
    if getattr(args, "duration", None):
        over["duration_ms"] = args.duration
    if getattr(args, "nodes", None):
        over["n_nodes"] = args.nodes
    if getattr(args, "clients", None):
        over["n_clients"] = args.clients
    raft_over: dict[str, Any] = {}
    if getattr(args, "no_prevote", False):
        raft_over["pre_vote"] = False
    if getattr(args, "no_noop", False):
        raft_over["noop_on_elect"] = False
    if raft_over:
        over["raft"] = dataclasses.replace(cfg.raft, **raft_over)
    return dataclasses.replace(cfg, **over) if over else cfg


def parse_seeds(text: str) -> range:
    if ":" in text:
        a, b = text.split(":")
        return range(int(a), int(b))
    return range(1, int(text) + 1)


# ---------------------------------------------------------------------------


def cmd_run(args) -> int:
    cfg = build_config(args)
    bugs = Bugs.of(args.bug)
    if args.timeline:
        from .timeline import run_with_timeline

        _, picture = run_with_timeline(cfg, args.seed, bugs)
        print(picture)
        print()
    r = run_and_check(cfg, args.seed, bugs, trace=args.trace)
    print(r.report())
    if args.trace:
        print("\n--- fault schedule ---")
        for f in r.sim.faults:
            print("  " + f.describe())
        print("\n--- trace ---")
        for line in r.sim.trace:
            print(line)
    return 0 if r.ok else 1


def cmd_fuzz(args) -> int:
    if args.profile == "swarm":
        raft_over: dict[str, Any] = {}
        if args.no_prevote:
            raft_over["pre_vote"] = False
        if args.no_noop:
            raft_over["noop_on_elect"] = False
        spec = Spec(args.bug, Bugs.of(args.bug), "swarm", cfg=None, raft_over=raft_over)
        if raft_over:
            spec = Spec(args.bug, Bugs.of(args.bug), "swarm", cfg=PROFILES["normal"], raft_over=raft_over)
    else:
        spec = Spec(args.bug, Bugs.of(args.bug), args.profile, build_config(args))
    rep = run_spec(spec, parse_seeds(args.seeds), args.workers)
    print(format_table([rep]))
    for o in rep.failures[:10]:
        print(f"  seed {o.seed}: {o.kind}: {o.detail}")
    return 0 if not rep.failures else 1


def cmd_campaign(args) -> int:
    seeds = parse_seeds(args.seeds)
    specs: list[Spec] = []
    for prof in args.profiles.split(","):
        specs.append(Spec("correct", Bugs(), prof))
        for name in Bugs.names():
            cfg = PROFILES[prof]
            label = name
            if name == "figure8_commit":
                # Figure 8 is only reachable without the no-op entry a leader
                # normally appends on election: that no-op is what commits the
                # previous term's tail legitimately, and it masks the bug.
                cfg = dataclasses.replace(cfg, raft=dataclasses.replace(cfg.raft, noop_on_elect=False))
                label = "figure8_commit*"
            specs.append(Spec(label, Bugs.of(name), prof, cfg))
    reports = [run_spec(s, seeds, args.workers) for s in specs]
    print(format_table(reports))
    print("\n* figure8_commit is run with noop_on_elect=False (see the code comment).")
    return 0


def cmd_ablation(args) -> int:
    seeds = parse_seeds(args.seeds)
    reports = []
    for prof in ("normal", "hostile", "slow"):
        base = PROFILES[prof]
        for flags, label in PRE_VOTE_VARIANTS:
            cfg = dataclasses.replace(base, raft=dataclasses.replace(base.raft, **flags))
            reports.append(run_spec(Spec(f"correct/{label}", Bugs(), prof, cfg), seeds, args.workers))
    print(format_table(reports))
    return 0


def cmd_reads(args) -> int:
    """Section 6.4, measured: three ways to serve a read-only request."""
    seeds = parse_seeds(args.seeds)
    base = PROFILES[args.profile]
    if args.freeze is not None:
        base = dataclasses.replace(base, clock_freeze_prob=args.freeze)
    labels = ["through log", "read-index", "lease"]
    reports = []
    for mode in ("log", "read_index", "lease"):
        cfg = dataclasses.replace(base, raft=dataclasses.replace(base.raft, read_mode=mode))
        reports.append(run_spec(Spec(f"reads/{mode}", Bugs(), args.profile, cfg), seeds, args.workers))
    print(format_reads(reports, labels))
    print()
    print(format_table(reports))
    for label, r in zip(labels, reports):
        for o in r.failures[:3]:
            print(f"  {label}: seed {o.seed}: {o.kind}: {o.detail[:100]}")
    return 0


def cmd_recovery(args) -> int:
    """How long does the cluster take to come back once the faults stop?"""
    seeds = parse_seeds(args.seeds)
    reports, labels = [], []
    for prof in args.profiles.split(","):
        base = PROFILES[prof]
        for flags, label in PRE_VOTE_VARIANTS:
            cfg = dataclasses.replace(base, raft=dataclasses.replace(base.raft, **flags))
            reports.append(
                run_spec(Spec(label, Bugs(), prof, cfg), seeds, args.workers, recovery=True)
            )
            labels.append(f"{prof}/{label}")
    print(format_recovery(reports, labels))
    return 0


def cmd_coverage(args) -> int:
    """What did the fuzzer actually exercise?"""
    seeds = parse_seeds(args.seeds)
    reports = [
        run_spec(Spec("correct", Bugs(), prof), seeds, args.workers)
        for prof in args.profiles.split(",")
    ]
    for prof, rep in zip(args.profiles.split(","), reports):
        print(f"--- {prof}")
        print(format_coverage([rep]))
        print()
    print("--- all profiles together")
    print(format_coverage(reports))
    return 0


def cmd_shrink(args) -> int:
    cfg = build_config(args)
    bugs = Bugs.of(args.bug)
    rep = shrink(cfg, args.seed, bugs, verbose=args.verbose)
    if rep is None:
        print(f"seed {args.seed} does not fail under this configuration")
        return 1
    print(rep.describe())
    if args.save:
        with open(args.save, "w") as fh:
            fh.write(rep.to_json())
        print(f"\nsaved to {args.save}")
    if args.timeline:
        from .timeline import run_with_timeline

        _, picture = run_with_timeline(rep.cfg, rep.seed, rep.bugs, faults=rep.faults)
        print()
        print(picture)
    if args.trace:
        run = rep.run(trace=True)
        print("\n--- trace ---")
        for line in run.sim.trace:
            print(line)
    return 0


def cmd_replay(args) -> int:
    from .timeline import run_with_timeline

    with open(args.repro) as fh:
        rep = Repro.from_json(fh.read())
    print(rep.describe())
    print()
    _, picture = run_with_timeline(rep.cfg, rep.seed, rep.bugs, faults=rep.faults)
    print(picture)
    run = rep.run(trace=True)
    print("\n--- trace ---")
    for line in run.sim.trace:
        print(line)
    print("\n" + run.report())
    return 0 if run.ok else 1


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("raftlab", description="deterministic simulation testing for Raft")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, seeds_default="200"):
        sp.add_argument("--profile", default="normal", choices=sorted(PROFILES) + ["swarm"])
        sp.add_argument("--bug", default="none", choices=["none"] + Bugs.names())
        sp.add_argument("--duration", type=int)
        sp.add_argument("--nodes", type=int)
        sp.add_argument("--clients", type=int)
        sp.add_argument("--no-prevote", action="store_true", help="disable pre-vote")
        sp.add_argument("--no-noop", action="store_true", help="no no-op entry on election")

    sp = sub.add_parser("run", help="one simulation, fully checked")
    common(sp)
    sp.add_argument("--seed", type=int, default=1)
    sp.add_argument("--trace", action="store_true")
    sp.add_argument("--timeline", action="store_true", help="draw the run as a grid")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("fuzz", help="sweep seeds for one configuration")
    common(sp)
    sp.add_argument("--seeds", default="500")
    sp.add_argument("--workers", type=int, default=0)
    sp.set_defaults(func=cmd_fuzz)

    sp = sub.add_parser("campaign", help="the full matrix: correct build plus every injected bug")
    sp.add_argument("--seeds", default="500")
    sp.add_argument("--profiles", default="normal,hostile,slow,churn")
    sp.add_argument("--workers", type=int, default=0)
    sp.set_defaults(func=cmd_campaign)

    sp = sub.add_parser("ablation", help="measure what pre-vote actually buys")
    sp.add_argument("--seeds", default="400")
    sp.add_argument("--workers", type=int, default=0)
    sp.set_defaults(func=cmd_ablation)

    sp = sub.add_parser("recovery", help="time to serve a request again after a heal")
    sp.add_argument("--seeds", default="500")
    sp.add_argument("--profiles", default="hostile,slow")
    sp.add_argument("--workers", type=int, default=0)
    sp.set_defaults(func=cmd_recovery)

    sp = sub.add_parser("coverage", help="which situations the fuzzer actually reaches")
    sp.add_argument("--seeds", default="300")
    sp.add_argument("--profiles", default="normal,hostile,slow,churn")
    sp.add_argument("--workers", type=int, default=0)
    sp.set_defaults(func=cmd_coverage)

    sp = sub.add_parser("reads", help="measure the three read paths of section 6.4")
    sp.add_argument("--seeds", default="500")
    sp.add_argument("--profile", default="hostile", choices=sorted(PROFILES))
    sp.add_argument("--freeze", type=float, help="fraction of pauses that stop the clock")
    sp.add_argument("--workers", type=int, default=0)
    sp.set_defaults(func=cmd_reads)

    sp = sub.add_parser("shrink", help="minimise a failing seed into a repro")
    common(sp)
    sp.add_argument("--seed", type=int, required=True)
    sp.add_argument("--save")
    sp.add_argument("--trace", action="store_true")
    sp.add_argument("--timeline", action="store_true", help="draw the repro as a grid")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=cmd_shrink)

    sp = sub.add_parser("replay", help="re-run a saved repro")
    sp.add_argument("repro")
    sp.set_defaults(func=cmd_replay)

    args = p.parse_args(argv)
    return args.func(args)
