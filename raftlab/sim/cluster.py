"""The deterministic simulator.

Every source of nondeterminism a real cluster has -- message latency, message
loss, reordering, duplicate delivery, process crashes, network partitions, clock
drift, thread interleaving -- is replaced by a seeded pseudo-random choice made
by a single-threaded event loop over a virtual clock.  Consequences:

  * A run is a pure function of (config, seed, fault schedule).  Reproducing a
    failure means re-running it, not "trying to catch it again".
  * Simulated time is free, so a 20-second cluster life costs milliseconds and a
    rare interleaving can be searched for over thousands of seeds.
  * The whole system, including the failure injector, lives in one process, so
    the checker can observe every node after every single event.
"""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field
from typing import Any

from ..check.history import History
from ..check.invariants import SafetyChecker, Violation
from ..raft.bugs import NO_BUGS, Bugs
from ..raft.node import RaftNode
from ..raft.types import (
    CLIENT_BASE,
    ClientReply,
    ClientRequest,
    Envelope,
    Role,
    config_command,
    is_client,
)
from .client import ClientSim
from .config import SimConfig
from .faults import Fault
from .faults import generate as generate_faults
from .network import Network


def substream(seed: int, name: str) -> random.Random:
    """Independent, reproducible RNG per subsystem, derived from one seed."""
    return random.Random(f"raftlab:{seed}:{name}")


@dataclass
class SimResult:
    seed: int
    cfg: SimConfig
    bugs: Bugs
    faults: list[Fault]
    history: History
    violations: list[Violation]
    stats: dict[str, Any]
    trace: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        s = self.stats
        return (
            f"seed={self.seed} events={s['events']} msgs={s['msgs_sent']}"
            f" (drop {s['msgs_dropped']}, dup {s['msgs_dup']})"
            f" elections={s['elections']} committed={s['committed']}"
            f" snapshots={s['snapshots']}/{s['snapshots_installed']}"
            f" ops={s['ops_completed']}/{s['ops_started']}"
            f" violations={len(self.violations)}"
        )


class Simulator:
    def __init__(
        self,
        cfg: SimConfig,
        seed: int,
        bugs: Bugs = NO_BUGS,
        faults: list[Fault] | None = None,
        trace: bool = False,
        stop_on_violation: bool = True,
    ) -> None:
        self.cfg = cfg
        self.seed = seed
        self.bugs = bugs
        self.tracing = trace
        self.stop_on_violation = stop_on_violation
        self.trace: list[str] = []

        self.net = Network(cfg, substream(seed, "net"))
        self.faults = faults if faults is not None else generate_faults(cfg, substream(seed, "faults"))

        ids = tuple(range(cfg.n_nodes))
        start = cfg.initial_members or cfg.n_nodes
        self.initial_members = tuple(ids[:start])
        self.nodes = [
            RaftNode(
                i, ids, cfg.raft, seed=f"{seed}:node{i}", bugs=bugs, now=0,  # type: ignore[arg-type]
                members=self.initial_members,
            )
            for i in ids
        ]
        self.alive = [True] * cfg.n_nodes
        self.paused_until: dict[int, int] = {}
        self.frozen_ms = [0] * cfg.n_nodes   # simulated time a node's clock missed
        self.freeze_start: dict[int, int] = {}
        self.durable = [n.durable() for n in self.nodes]

        # clock skew: nobody shares an epoch or a tick rate
        crng = substream(seed, "clock")
        self.drift = [1.0 + crng.uniform(-cfg.clock_drift, cfg.clock_drift) for _ in ids]
        self.offset = [crng.randint(0, cfg.clock_offset_ms) for _ in ids]

        hist = History()
        self.history = hist
        crng2 = substream(seed, "clients")
        self.clients = [ClientSim(c, cfg, substream(seed, f"client{c}"), hist) for c in range(cfg.n_clients)]

        self.jitter = substream(seed, "jitter")
        # Which interesting situations this run actually reached.  A fuzz
        # campaign that never produces a snapshot install has not tested
        # InstallSnapshot, however many seeds it burned.
        self.cov: set = set()
        self.snap_installed = [0] * cfg.n_nodes
        self.heal_at = cfg.heal_time()
        self.checker = SafetyChecker(cfg.n_nodes)
        self.now = 0
        self.queue: list[tuple[int, int, str, Any]] = []
        self.counter = 0
        self.events = 0
        self.last_role = [Role.FOLLOWER] * cfg.n_nodes
        self.ops_after_heal = 0
        self.crashes = 0
        self.pauses = 0
        self.operator_seq = 0
        self.member_requests = 0
        self.frozen = [0] * cfg.n_nodes
        self.crashed_by: dict[int, int] = {}
        self.paused_by: dict[int, int] = {}

        self._bootstrap(crng2)

    # ------------------------------------------------------------------
    def push(self, when: int, kind: str, payload: Any = None) -> None:
        self.counter += 1
        heapq.heappush(self.queue, (when, self.counter, kind, payload))

    def node_now(self, i: int, when: int) -> int:
        """What node i believes the time is: its own epoch, its own rate, and
        minus whatever it slept through with its clock stopped."""
        return int((when - self.frozen_ms[i]) * self.drift[i]) + self.offset[i]

    def log(self, msg: str) -> None:
        if self.tracing:
            self.trace.append(f"t={self.now:>7}  {msg}")

    def _bootstrap(self, rng: random.Random) -> None:
        for i in range(self.cfg.n_nodes):
            self.push(rng.randrange(self.cfg.tick_ms), "tick", i)
        for f in self.faults:
            if f.kind == "partition":
                self.push(f.start, "part_start", f)
                self.push(f.end, "part_end", f.fid)
            elif f.kind == "pause":
                self.push(f.start, "pause", f)
                self.push(f.end, "resume", f.fid)
            else:
                self.push(f.start, "crash", f)
                self.push(f.end, "restart", f.fid)
        for c in range(self.cfg.n_clients):
            self.push(rng.randint(0, 200), "cstart", c)
        window = self.cfg.heal_time()
        changes = int(self.cfg.duration_ms / 1000.0 * self.cfg.membership_changes_per_sec)
        for _ in range(changes):
            self.push(rng.randint(300, max(400, window)), "member_change", None)

    # ------------------------------------------------------------------
    def send(self, env: Envelope) -> None:
        for t in self.net.schedule(self.now, env.src, env.dst):
            self.push(t, "deliver", env)

    def emit(self, envs: list[Envelope]) -> None:
        for e in envs:
            self.send(e)

    def hit(self, name: str) -> None:
        self.cov.add(name)

    def post(self, i: int) -> None:
        """Persist, then observe.  Called after every node event."""
        node = self.nodes[i]
        if node.dirty:
            self.durable[i] = node.durable()
        if node.min_truncated_index is not None:
            self.hit("log_truncated")
        if node.snapshots_installed > self.snap_installed[i]:
            self.snap_installed[i] = node.snapshots_installed
            self.hit("snapshot_installed")
        if node.snapshots_taken:
            self.hit("snapshot_taken")
        if node.role is Role.LEADER:
            leaders = [n for n in self.nodes if n.role is Role.LEADER and self.alive[n.id]]
            if len(leaders) > 1:
                self.hit("two_nodes_believe_they_lead")
                if self.net.partitions and any(
                    n.current_term < node.current_term for n in leaders
                ):
                    self.hit("stale_leader_in_partition")
            if node.config_changes:
                self.hit("config_changed")
            if node.reads_served_locally:
                self.hit("read_served_without_the_log")
        if self.tracing and node.role is not self.last_role[i]:
            self.log(f"node {i}: {self.last_role[i].value} -> {node.role.value} (term {node.current_term})")
            self.last_role[i] = node.role
        self.checker.observe(self.now, node)

    # ------------------------------------------------------------------
    def run(self) -> SimResult:
        cfg = self.cfg
        while self.queue:
            when, _, kind, payload = heapq.heappop(self.queue)
            if when > cfg.duration_ms:
                break
            self.now = when
            self.events += 1
            getattr(self, f"ev_{kind}")(payload)
            if self.stop_on_violation and self.checker.violations:
                self.log(f"STOP: {self.checker.violations[0]}")
                break

        if not self.checker.violations:
            self.checker.sweep(self.now, self.nodes)
        if self.checker.elections > 1:
            self.hit("leadership_changed_hands")
        if any(o.attempts > 2 for o in self.history.ops):
            self.hit("client_retried_more_than_once")
        if any(
            o.completed and o.ret is not None and o.ret - o.invoke > 1_000
            for o in self.history.ops
        ):
            self.hit("client_op_took_over_a_second")
        return self._result()

    # -- event handlers -------------------------------------------------
    def ev_tick(self, i: int) -> None:
        self.push(self.now + self.cfg.tick_ms, "tick", i)
        if not self.alive[i] or i in self.paused_until:
            return
        self.emit(self.nodes[i].on_tick(self.node_now(i, self.now)))
        self.post(i)

    def ev_deliver(self, env: Envelope) -> None:
        dst = env.dst
        if is_client(dst):
            self.deliver_client(env)
            return
        if not self.alive[dst] or self.net.blocked(env.src, dst):
            self.net.dropped += 1
            return
        resume = self.paused_until.get(dst)
        if resume is not None:
            # A paused process does not lose the message, it just cannot look
            # at it yet.  The whole inbox lands at once when it wakes up.
            self.push(max(resume, self.now + 1), "deliver", env)
            return
        self.emit(self.nodes[dst].on_message(self.node_now(dst, self.now), env))
        self.post(dst)

    def deliver_client(self, env: Envelope) -> None:
        reply = env.msg
        if not isinstance(reply, ClientReply):
            return
        if reply.client < 0 or reply.client >= len(self.clients):
            return  # the operator: it does not retry, the next tick proposes again
        client = self.clients[reply.client]
        was = client.current
        out = client.on_reply(self.now, reply)
        if client.current is None and was is not None:
            self.log(f"client {reply.client} completed {was.op}({was.key}) -> {was.result!r}")
            if self.now >= self.heal_at:
                self.ops_after_heal += 1
            self.push(self.now + self.cfg.think_time_ms, "cstart", reply.client)
        elif out is not None:
            # Redirected: wait a little before bothering another node.
            delay = self.cfg.retry_backoff_ms + self.jitter.randrange(self.cfg.retry_backoff_ms + 1)
            self.push(self.now + delay, "csend", out)
            self.push(
                self.now + delay + self.cfg.client_timeout_ms,
                "ctimeout",
                (reply.client, client.attempt),
            )

    def ev_csend(self, env: Envelope) -> None:
        self.send(env)

    def ev_member_change(self, _payload) -> None:
        """The operator asks the current leader to add or remove one server.

        Modelled as an ordinary client request, because that is what it is: it
        goes through the log, it can be lost, and it can be proposed to a node
        that is no longer leader.
        """
        leader = self.current_leader()
        if leader is None:
            return
        members = set(self.nodes[leader].members)
        outside = [i for i in range(self.cfg.n_nodes) if i not in members]
        if outside and (len(members) <= 3 or self.jitter.random() < 0.5):
            action, target = "add", self.jitter.choice(sorted(outside))
        elif len(members) > 3:
            action, target = "remove", self.jitter.choice(sorted(members))
        else:
            return
        self.operator_seq += 1
        self.member_requests += 1
        self.log(f"OPERATOR {action} node {target} (config was {sorted(members)})")
        self.send(
            Envelope(
                CLIENT_BASE + 900,
                leader,
                ClientRequest(config_command(action, target, self.operator_seq)),
            )
        )

    def ev_cstart(self, cid: int) -> None:
        client = self.clients[cid]
        if client.current is not None:
            return
        self.send(client.begin(self.now))
        self.push(self.now + self.cfg.client_timeout_ms, "ctimeout", (cid, client.attempt))

    def ev_ctimeout(self, payload: tuple[int, int]) -> None:
        cid, attempt = payload
        client = self.clients[cid]
        if client.current is None or client.attempt != attempt:
            return  # already answered, or a newer attempt is outstanding
        self.send(client.retry(self.now))
        self.push(self.now + self.cfg.client_timeout_ms, "ctimeout", (cid, client.attempt))

    def current_config(self) -> tuple[int, ...]:
        """The configuration of the most authoritative node we can see."""
        best, best_term = self.initial_members, -1
        for n in self.nodes:
            if n.current_term > best_term:
                best, best_term = tuple(sorted(n.members)), n.current_term
        return best

    def current_leader(self) -> int | None:
        best, best_term = None, -1
        for i, n in enumerate(self.nodes):
            if self.alive[i] and n.role is Role.LEADER and n.current_term > best_term:
                best, best_term = i, n.current_term
        return best

    def ev_pause(self, fault: Fault) -> None:
        i = self.current_leader() if fault.target == "leader" else fault.node
        if i is None or not self.alive[i] or i in self.paused_until:
            return
        self.paused_by[fault.fid] = i
        self.paused_until[i] = fault.end
        self.pauses += 1
        if fault.freeze:
            self.freeze_start[i] = self.now
            self.hit("clock_frozen")
        if self.nodes[i].role is Role.LEADER:
            self.hit("leader_paused")
        self.log(f"PAUSE node {i} ({self.nodes[i].role.value}, term {self.nodes[i].current_term})"
                 f" for {fault.end - self.now}ms{' with its clock stopped' if fault.freeze else ''}")

    def ev_resume(self, fid: int) -> None:
        i = self.paused_by.pop(fid, None)
        if i is None:
            return
        self.paused_until.pop(i, None)
        self.thaw(i)
        self.log(f"RESUME node {i}")

    def thaw(self, i: int) -> None:
        started = self.freeze_start.pop(i, None)
        if started is not None:
            self.frozen_ms[i] += self.now - started
            self.frozen[i] = self.frozen[i] + 1

    def ev_crash(self, fault: Fault) -> None:
        i = self.current_leader() if fault.target == "leader" else fault.node
        if i is None or not self.alive[i]:
            return
        self.paused_until.pop(i, None)
        self.thaw(i)
        self.crashed_by[fault.fid] = i
        self.alive[i] = False
        self.crashes += 1
        if any(n.role in (Role.CANDIDATE, Role.PRE_CANDIDATE) for n in self.nodes):
            self.hit("crash_during_election")
        if self.nodes[i].role is Role.LEADER:
            self.hit("leader_crashed")
        self.log(f"CRASH node {i} (was {self.nodes[i].role.value}, term {self.nodes[i].current_term})")

    def ev_restart(self, fid: int) -> None:
        i = self.crashed_by.pop(fid, None)
        if i is None or self.alive[i]:
            return
        self.alive[i] = True
        if any(n.role in (Role.CANDIDATE, Role.PRE_CANDIDATE) for n in self.nodes):
            self.hit("restart_during_election")
        self.nodes[i].restart(self.node_now(i, self.now), self.durable[i])
        self.last_role[i] = Role.FOLLOWER
        self.log(f"RESTART node {i} (durable term {self.nodes[i].current_term},"
                 f" log {self.nodes[i].last_index})")
        self.post(i)

    def resolve_groups(self, fault: Fault) -> tuple[tuple[int, ...], ...] | None:
        n = self.cfg.n_nodes
        if fault.target == "fixed":
            return fault.groups
        leader = self.current_leader()
        if leader is None:
            return None
        others = [i for i in range(n) if i != leader]
        if fault.target == "isolate_leader":
            return ((leader,), tuple(others))
        # leader_minority: keep the leader with too few peers to commit anything
        keep = (n // 2) - 1
        return (tuple(sorted([leader] + others[:keep])), tuple(sorted(others[keep:])))

    def ev_part_start(self, fault: Fault) -> None:
        groups = self.resolve_groups(fault)
        if groups is None or len(groups) < 2:
            return
        pid = fault.fid
        self.net.add_partition(pid, groups)
        leader = self.current_leader()
        if leader is not None:
            group = next((g for g in groups if leader in g), ())
            if len(group) <= self.cfg.n_nodes // 2:
                self.hit("leader_cut_into_a_minority")
        self.log("PARTITION " + " | ".join(",".join(map(str, g)) for g in groups))

    def ev_part_end(self, pid: int) -> None:
        if pid in self.net.partitions:
            groups = self.net.partitions[pid]
            self.net.remove_partition(pid)
            self.log("HEAL      " + " | ".join(",".join(map(str, g)) for g in groups))

    # ------------------------------------------------------------------
    def _result(self) -> SimResult:
        hs = self.history.stats()
        stats = {
            "events": self.events,
            "msgs_sent": self.net.sent,
            "msgs_dropped": self.net.dropped,
            "msgs_dup": self.net.duplicated,
            "elections": self.checker.elections,
            "committed": self.checker.max_committed,
            "applied": len(self.checker.applied),
            "ops_started": hs["ops"],
            "ops_completed": hs["completed"],
            "ops_pending": hs["pending"],
            "retries": hs["retries"],
            "ops_after_heal": self.ops_after_heal,
            "read_p50": hs["read_p50"],
            "read_p99": hs["read_p99"],
            "write_p50": hs["write_p50"],
            "write_p99": hs["write_p99"],
            "reads": hs["reads"],
            "crashes": self.crashes,
            "pauses": self.pauses,
            "clock_freezes": sum(self.frozen),
            "member_requests": self.member_requests,
            "config_size": len(self.current_config()),
            "config_changes": max(n.config_changes for n in self.nodes),
            "reads_local": sum(n.reads_served_locally for n in self.nodes),
            "snapshots": sum(n.snapshots_taken for n in self.nodes),
            "snapshots_installed": sum(n.snapshots_installed for n in self.nodes),
            "partitions": sum(1 for f in self.faults if f.kind == "partition"),
            "sim_end": self.now,
            "coverage": tuple(sorted(self.cov)),
        }
        return SimResult(
            seed=self.seed,
            cfg=self.cfg,
            bugs=self.bugs,
            faults=self.faults,
            history=self.history,
            violations=list(self.checker.violations),
            stats=stats,
            trace=self.trace,
        )


def run_sim(cfg: SimConfig, seed: int, bugs: Bugs = NO_BUGS, **kw) -> SimResult:
    return Simulator(cfg, seed, bugs, **kw).run()
