"""One run = simulate, then subject the result to every check we have."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from .check.invariants import Violation
from .check.linearizability import LinResult, check_history
from .raft.bugs import NO_BUGS, Bugs
from .sim.cluster import SimResult, Simulator, substream
from .sim.config import SimConfig
from .sim.faults import Fault
from .sim.faults import generate as generate_faults


@dataclass
class CheckedRun:
    sim: SimResult
    lin: LinResult | None
    failures: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def first(self) -> Violation | None:
        return self.failures[0] if self.failures else None

    def report(self) -> str:
        lines = [self.sim.summary()]
        if self.lin is not None:
            lines.append(f"  linearizability: {self.lin}")
            for op in self.lin.witness:
                lines.append(f"    witness: {op.label()}")
        for v in self.failures:
            lines.append(f"  ! {v}")
        return "\n".join(lines)


def recovers_given_more_time(
    cfg: SimConfig, seed: int, bugs: Bugs, faults: list[Fault], heal_at: int
) -> bool:
    """Replay the same world with a longer quiet tail.

    "No client operation completed in the window I chose" is a heuristic, and a
    cluster repairing a badly diverged log in an adverse network can legitimately
    need longer than one election to come back.  Rather than pick a bigger
    number and hope, the failing run is replayed with the tail doubled: if the
    cluster serves anybody at all in that time, this was a slow recovery and not
    a loss of availability, and no violation is reported.
    """
    longer = dataclasses.replace(
        cfg, duration_ms=cfg.duration_ms + 2 * cfg.quiet_needed()
    )
    sim = Simulator(longer, seed, bugs, faults=faults).run()
    return any(o.completed and o.ret is not None and o.ret > heal_at for o in sim.history.ops)


def run_and_check(
    cfg: SimConfig,
    seed: int,
    bugs: Bugs = NO_BUGS,
    faults: list[Fault] | None = None,
    trace: bool = False,
    check_linearizability: bool = True,
    max_steps: int = 400_000,
) -> CheckedRun:
    if faults is None:
        # Generated here rather than inside the simulator so that the liveness
        # confirmation below can replay exactly the same world.
        faults = generate_faults(cfg, substream(seed, "faults"))
    sim = Simulator(cfg, seed, bugs, faults=faults, trace=trace).run()
    failures: list[Violation] = list(sim.violations)

    lin: LinResult | None = None
    if check_linearizability and sim.history.ops:
        lin = check_history(sim.history, max_steps=max_steps)
        if not lin.ok and not lin.unknown:
            failures.append(
                Violation("Linearizability", sim.stats["sim_end"], lin.detail + f" [key {lin.key}]")
            )

    # Liveness: once every fault has healed the cluster must serve clients
    # again.  Checked only when safety held, so a stopped run is not reported
    # twice.
    if not sim.violations:
        quiet_ms = cfg.quiet_window()
        # Only assert liveness when the run actually left the cluster enough
        # quiet time to recover; otherwise report nothing rather than a
        # verdict the world never gave it a chance to earn.
        if quiet_ms >= cfg.quiet_needed() and sim.stats["ops_after_heal"] == 0:
            if not recovers_given_more_time(cfg, seed, bugs, faults, cfg.heal_time()):
                failures.append(
                    Violation(
                        "NoProgress",
                        sim.stats["sim_end"],
                        f"no client operation completed in the {quiet_ms}ms after the"
                        f" last fault healed, nor in {2 * cfg.quiet_needed()}ms more",
                    )
                )

    return CheckedRun(sim=sim, lin=lin, failures=failures)
