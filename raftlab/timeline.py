"""Render a run as a picture you can actually read.

A 20,000-line event trace is not insight.  This collapses one run into a
character grid -- one column per time slice, one row per node -- so that the
shape of a run (who led, who was cut off, where the log stalled) is visible at a
glance, next to the fault schedule that caused it.
"""

from __future__ import annotations

from .raft.types import Role
from .sim.cluster import SimResult, Simulator

# what each node was doing during a slice, in priority order
SYMBOLS = {
    "leader": "L",
    "candidate": "c",
    "follower": ".",
    "crashed": "x",
    "paused": "~",
    "outside": " ",
}


class Timeline:
    """Samples the cluster on a fixed grid while the simulation runs."""

    def __init__(self, sim: Simulator, columns: int = 96) -> None:
        self.sim = sim
        self.columns = columns
        self.slice_ms = max(1, sim.cfg.duration_ms // columns)
        self.rows: dict[int, list[str]] = {i: [] for i in range(sim.cfg.n_nodes)}
        self.commits: list[int] = []
        self.events: list[str] = []
        self.next_sample = 0

    def sample(self) -> None:
        sim = self.sim
        while sim.now >= self.next_sample and len(self.commits) < self.columns:
            for i, node in enumerate(sim.nodes):
                if not sim.alive[i]:
                    mark = SYMBOLS["crashed"]
                elif i in sim.paused_until:
                    mark = SYMBOLS["paused"]
                elif not node.is_member():
                    mark = SYMBOLS["outside"]
                elif node.role is Role.LEADER:
                    mark = SYMBOLS["leader"]
                elif node.role in (Role.CANDIDATE, Role.PRE_CANDIDATE):
                    mark = SYMBOLS["candidate"]
                else:
                    mark = SYMBOLS["follower"]
                self.rows[i].append(mark)
            self.commits.append(sim.checker.max_committed)
            self.next_sample += self.slice_ms

    def render(self, result: SimResult) -> str:
        width = len(self.commits)
        out: list[str] = []
        out.append(f"timeline  seed={result.seed}  {self.slice_ms}ms per column"
                   f"  ({result.cfg.duration_ms}ms total)")
        out.append("")
        for i in range(self.sim.cfg.n_nodes):
            row = "".join(self.rows[i][:width])
            out.append(f"  node {i} |{row}|")

        # committed index, drawn as a coarse sparkline
        top = max(self.commits + [1])
        levels = " .:-=+*#%@"
        spark = "".join(levels[min(len(levels) - 1, int(c / top * (len(levels) - 1)))]
                        for c in self.commits[:width])
        out.append(f"  commit |{spark}|  0..{top}")

        # a ruler in seconds
        ruler = [" "] * width
        for col in range(width):
            t = col * self.slice_ms
            if t % 1000 < self.slice_ms:
                label = str(t // 1000)
                for k, ch in enumerate(label):
                    if col + k < width:
                        ruler[col + k] = ch
        out.append(f"         |{''.join(ruler)}|  seconds")
        out.append("")
        out.append("  L leader   c candidate   . follower   x crashed   ~ paused"
                   "   (blank) outside the configuration")
        out.append("")
        out.append("faults:")
        for f in result.faults:
            out.append(f"  {f.describe()}")
        if result.violations:
            out.append("")
            out.append("violations:")
            for v in result.violations:
                out.append(f"  {v}")
        return "\n".join(out)


def run_with_timeline(cfg, seed, bugs, faults=None, columns: int = 96):
    """Run one simulation, sampling the cluster on a grid as it goes."""
    sim = Simulator(cfg, seed, bugs, faults=faults, trace=True)
    tl = Timeline(sim, columns)
    original = sim.post

    def post(i: int) -> None:
        original(i)
        tl.sample()

    sim.post = post  # type: ignore[method-assign]
    result = sim.run()
    tl.sample()
    return result, tl.render(result)
