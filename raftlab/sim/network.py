"""An unreliable, asynchronous network with partitions.

Messages are delayed by a random latency, dropped, duplicated and therefore
reordered.  Partitions are evaluated at *delivery* time, so messages already in
flight when a partition opens are lost -- which is what really happens.
"""

from __future__ import annotations

import random

from ..raft.types import is_client
from .config import SimConfig


class Network:
    def __init__(self, cfg: SimConfig, rng: random.Random) -> None:
        self.cfg = cfg
        self.rng = rng
        # partition id -> list of groups; nodes in different groups cannot talk
        self.partitions: dict[int, tuple[tuple[int, ...], ...]] = {}
        self.sent = 0
        self.dropped = 0
        self.duplicated = 0
        # Real clusters do not have one latency: a few links are much slower
        # than the rest, and they are asymmetric.  Slow links are what produce
        # partially replicated entries, which is where consensus bugs live.
        self.link_mult: dict[tuple[int, int], float] = {}
        for a in range(cfg.n_nodes):
            for b in range(cfg.n_nodes):
                if a == b:
                    continue
                roll = rng.random()
                if roll < 0.06:
                    self.link_mult[(a, b)] = rng.uniform(6.0, 14.0)  # a bad link
                elif roll < 0.20:
                    self.link_mult[(a, b)] = rng.uniform(2.0, 5.0)
                else:
                    self.link_mult[(a, b)] = rng.uniform(0.5, 1.5)

    # -- partitions ----------------------------------------------------
    def add_partition(self, pid: int, groups: tuple[tuple[int, ...], ...]) -> None:
        self.partitions[pid] = groups

    def remove_partition(self, pid: int) -> None:
        self.partitions.pop(pid, None)

    def blocked(self, a: int, b: int) -> bool:
        """Clients reach every node; only the inter-node fabric partitions."""
        if is_client(a) or is_client(b):
            return False
        for groups in self.partitions.values():
            ga = gb = -1
            for i, g in enumerate(groups):
                if a in g:
                    ga = i
                if b in g:
                    gb = i
            if ga != gb:
                return True
        return False

    def reachable_sets(self) -> list[frozenset]:
        """Current connectivity classes, used for liveness reporting."""
        nodes = list(range(self.cfg.n_nodes))
        seen: list[frozenset] = []
        for n in nodes:
            group = frozenset(m for m in nodes if not self.blocked(n, m))
            if group not in seen:
                seen.append(group)
        return seen

    # -- delivery ------------------------------------------------------
    def latency(self, src: int, dst: int) -> int:
        if is_client(src) or is_client(dst):
            return self.rng.randint(1, self.cfg.client_latency_max)
        base = self.rng.randint(self.cfg.latency_min, self.cfg.latency_max)
        return max(1, int(base * self.link_mult.get((src, dst), 1.0)))

    def schedule(self, now: int, src: int, dst: int) -> list[int]:
        """Return the delivery times for one send (0, 1 or 2 copies)."""
        self.sent += 1
        if self.rng.random() < self.cfg.drop_prob:
            self.dropped += 1
            return []
        times = [now + self.latency(src, dst)]
        if self.rng.random() < self.cfg.dup_prob:
            self.duplicated += 1
            times.append(now + self.latency(src, dst))
        return times
