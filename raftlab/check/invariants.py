"""The safety oracle: Raft's invariants, checked after every single event.

A simulator only finds bugs if something is watching.  This module watches the
five safety properties from the Raft paper (figure 3) plus two operational ones,
and it does so *incrementally* -- each node's log is validated once per appended
entry rather than rescanned on every event, which keeps a full check affordable
at roughly 10^5 events per run.

  1. Election Safety        at most one leader per term
  2. Leader Append-Only     a leader never overwrites or deletes its own entries
  3. Log Matching           same (index, term) => identical entry and prefix
  4. Leader Completeness    a leader of term T holds every entry committed in a
                            term below T (a slow candidate that wakes up and
                            wins a *stale* term is harmless: it can commit
                            nothing, so the property does not apply to it)
  5. State Machine Safety   no two nodes apply different commands at one index
  6. Committed Entry Change two nodes never commit different entries at one index,
                            and no node deletes an entry it has committed
  7. Term Regression        a node's durable term never goes backwards
  8. Vote Safety            a node never casts two different votes in one term
  9. Commit In Log          a node's commit index never runs past its own log
 10. Snapshot Sanity        a snapshot never covers uncommitted state, and a
                            node's snapshot point never moves backwards

The last two are not in figure 3: they are *causes* whose usual consequences
(two leaders, a rolled-back apply) are rare and easily masked.  Checking the
cause instead of the consequence is what turns a bug that shows up once in six
thousand runs into one that shows up in one run out of three -- the oracle's
sensitivity matters as much as the fault injector's reach.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..raft.node import RaftNode
from ..raft.types import Command, Role

MASK = (1 << 64) - 1
PRIME = 1099511628211


@dataclass(frozen=True)
class Violation:
    kind: str
    time: int
    detail: str

    def __str__(self) -> str:
        return f"t={self.time:>7}  {self.kind}: {self.detail}"


class SafetyChecker:
    def __init__(self, n_nodes: int) -> None:
        self.n = n_nodes
        self.violations: list[Violation] = []
        # global knowledge
        self.leader_of_term: dict[int, int] = {}
        self.canonical: dict[tuple[int, int], int] = {}  # (index, term) -> prefix hash
        # index -> (entry term, cmd, prefix hash, term it was committed in)
        self.committed: dict[int, tuple[int, Command, int, int]] = {}
        self.max_committed = 0
        self.applied: dict[int, Command] = {}
        # per-node bookkeeping.  Prefix hashes are sparse (a dict, not a list)
        # because log compaction removes the bottom of every log.
        self.hashes: dict[int, dict[int, int]] = {i: {0: 0} for i in range(n_nodes)}
        self.checked: dict[int, int] = dict.fromkeys(range(n_nodes), 0)
        self.snapshot_at: dict[int, int] = dict.fromkeys(range(n_nodes), 0)
        self.installs_seen: dict[int, int] = dict.fromkeys(range(n_nodes), 0)
        self.commit_seen: dict[int, int] = dict.fromkeys(range(n_nodes), 0)
        self.max_term: dict[int, int] = dict.fromkeys(range(n_nodes), 0)
        self.applied_seen: dict[int, int] = dict.fromkeys(range(n_nodes), 0)
        self.votes_by_term: dict[tuple[int, int], int] = {}
        self.cmd_ids: dict[Command, int] = {}
        self.elections = 0

    # ------------------------------------------------------------------
    def fail(self, kind: str, now: int, detail: str) -> None:
        if len(self.violations) < 32:
            self.violations.append(Violation(kind, now, detail))

    def cmd_id(self, cmd: Command) -> int:
        got = self.cmd_ids.get(cmd)
        if got is None:
            got = len(self.cmd_ids) + 1
            self.cmd_ids[cmd] = got
        return got

    def mix(self, h: int, term: int, cid: int) -> int:
        return ((h * PRIME) ^ (term << 21) ^ cid) & MASK

    # ------------------------------------------------------------------
    def observe(self, now: int, node: RaftNode) -> None:
        i = node.id

        # (7) durable term monotonicity
        if node.current_term < self.max_term[i]:
            self.fail(
                "TermRegression",
                now,
                f"node {i} term {self.max_term[i]} -> {node.current_term}",
            )
        self.max_term[i] = max(self.max_term[i], node.current_term)

        # (8) a vote, once cast, is durable and unique within its term
        if node.voted_for is not None:
            key = (i, node.current_term)
            prev = self.votes_by_term.get(key)
            if prev is None:
                self.votes_by_term[key] = node.voted_for
            elif prev != node.voted_for:
                self.fail(
                    "VoteSafety",
                    now,
                    f"node {i} voted for {prev} and then for {node.voted_for}"
                    f" in term {node.current_term}",
                )
                self.votes_by_term[key] = node.voted_for

        # (9) commit index must stay inside the log
        if node.commit_index > node.last_index:
            self.fail(
                "CommitBeyondLog",
                now,
                f"node {i} has commit index {node.commit_index}"
                f" but only {node.last_index} log entries",
            )

        # (10) snapshots cover applied, therefore committed, state only
        if node.snapshot_index > node.commit_index:
            self.fail(
                "SnapshotBeyondCommit",
                now,
                f"node {i} snapshot covers index {node.snapshot_index}"
                f" but its commit index is {node.commit_index}",
            )
        if node.snapshot_index < self.snapshot_at[i]:
            self.fail(
                "SnapshotRegression",
                now,
                f"node {i} snapshot point moved back from"
                f" {self.snapshot_at[i]} to {node.snapshot_index}",
            )
        self.snapshot_at[i] = max(self.snapshot_at[i], node.snapshot_index)

        # (2) leader append-only
        trunc = node.min_truncated_index
        if node.truncated_committed is not None:
            # Note: a *follower* may legitimately hold -- and later truncate --
            # an uncommitted entry at an index some other node has committed.
            # What must never happen is a node deleting an entry it had itself
            # committed, so the node records that at the moment of truncation.
            self.fail(
                "TruncatedCommitted",
                now,
                f"node {i} deleted index {node.truncated_committed}, at or below"
                f" its own commit index",
            )
            node.truncated_committed = None
        h = self.hashes[i]
        if trunc is not None:
            node.min_truncated_index = None
            if node.role is Role.LEADER:
                self.fail(
                    "LeaderAppendOnly",
                    now,
                    f"leader {i} (term {node.current_term}) deleted from index {trunc}",
                )
            for k in [k for k in h if k >= trunc]:
                del h[k]
            self.checked[i] = min(self.checked[i], trunc - 1)

        # A node that installed a snapshot cannot show us the prefix below it
        # any more, and the install replaced whatever prefix it had -- including
        # a divergent one, which is usually why it needed the snapshot.  The
        # snapshot point names an entry some leader once appended, so its prefix
        # hash is already known globally: re-anchor on that and revalidate
        # whatever tail the node kept.
        snap = node.snapshot_index
        if node.snapshots_installed > self.installs_seen[i]:
            self.installs_seen[i] = node.snapshots_installed
            h.clear()
            self.checked[i] = 0
        if snap > self.checked[i]:
            known = self.canonical.get((snap, node.snapshot_term))
            if known is None:
                self.fail(
                    "SnapshotUnknownPoint",
                    now,
                    f"node {i} installed a snapshot at index {snap} term"
                    f" {node.snapshot_term}, which no log ever contained",
                )
            else:
                h[snap] = known
                self.checked[i] = snap
        for k in [k for k in h if k < snap]:
            del h[k]  # compacted: nobody can ask about it again

        # (3)+(6) validate freshly appended entries against global knowledge
        while self.checked[i] < node.last_index:
            idx = self.checked[i] + 1
            if idx <= snap:
                self.checked[i] = snap
                continue
            prev_hash = h.get(idx - 1)
            if prev_hash is None:
                break  # nothing to anchor this node's prefix to yet
            e = node.entry_at(idx)
            nh = self.mix(prev_hash, e.term, self.cmd_id(e.cmd))
            h[idx] = nh
            self.checked[i] = idx
            key = (idx, e.term)
            prev = self.canonical.get(key)
            if prev is None:
                self.canonical[key] = nh
            elif prev != nh:
                self.fail(
                    "LogMatching",
                    now,
                    f"node {i} has a different entry/prefix at index {idx} term {e.term}",
                )

        # (1)+(4) leadership
        if node.role is Role.LEADER:
            owner = self.leader_of_term.get(node.current_term)
            if owner is None:
                self.leader_of_term[node.current_term] = i
                self.elections += 1
                self.check_leader_completeness(now, node)
            elif owner != i:
                self.fail(
                    "ElectionSafety",
                    now,
                    f"nodes {owner} and {i} are both leader in term {node.current_term}",
                )

        # (6) record what this node considers committed
        if node.commit_index > self.commit_seen[i]:
            top = min(node.commit_index, node.last_index)
            start = max(self.commit_seen[i] + 1, node.snapshot_index + 1)
            for idx in range(start, top + 1):
                e = node.entry_at(idx)
                rec = self.committed.get(idx)
                if rec is None:
                    if idx not in h:
                        continue
                    self.committed[idx] = (e.term, e.cmd, h[idx], node.current_term)
                    self.max_committed = max(self.max_committed, idx)
                elif rec[0] != e.term or rec[1] != e.cmd:
                    self.fail(
                        "CommittedEntryChanged",
                        now,
                        f"index {idx} committed twice with different entries:"
                        f" term {rec[0]} {rec[1].op} vs node {i} term {e.term} {e.cmd.op}",
                    )
            self.commit_seen[i] = max(self.commit_seen[i], top)

        # (5) state machine safety
        trace = node.applied_trace
        if trace:
            for idx, cmd in trace:
                got = self.applied.get(idx)
                if got is None:
                    self.applied[idx] = cmd
                elif got != cmd:
                    self.fail(
                        "StateMachineSafety",
                        now,
                        f"index {idx} applied as {got.op}(c{got.client},s{got.seq})"
                        f" by one node and {cmd.op}(c{cmd.client},s{cmd.seq}) by node {i}",
                    )
            trace.clear()

    # ------------------------------------------------------------------
    def check_leader_completeness(self, now: int, node: RaftNode) -> None:
        """A leader of term T must hold every entry committed in a term < T."""
        term = node.current_term
        ci = 0
        for idx, rec in self.committed.items():
            if rec[3] < term and idx > ci:
                ci = idx
        if ci == 0:
            return
        h = self.hashes[node.id]
        if ci <= node.snapshot_index:
            # It is inside this node's snapshot, and the snapshot point itself
            # was matched against the canonical prefix when it was installed.
            return
        if node.last_index < ci or ci not in h:
            self.fail(
                "LeaderCompleteness",
                now,
                f"node {node.id} became leader in term {node.current_term} with"
                f" log length {node.last_index}, missing committed index {ci}",
            )
            return
        rec = self.committed[ci]
        if h[ci] != rec[2]:
            self.fail(
                "LeaderCompleteness",
                now,
                f"node {node.id} became leader in term {node.current_term} but its"
                f" prefix at committed index {ci} differs from the committed one",
            )

    # ------------------------------------------------------------------
    def sweep(self, now: int, nodes: list[RaftNode]) -> None:
        """Final pass: catch anything left in a node that stopped being touched."""
        for node in nodes:
            self.observe(now, node)
        # cross-node comparison of the surviving logs
        for a in range(self.n):
            for b in range(a + 1, self.n):
                ha, hb = self.hashes[a], self.hashes[b]
                shared = set(ha) & set(hb)
                if not shared:
                    continue
                common = max(shared)
                ta, tb = nodes[a].term_at(common), nodes[b].term_at(common)
                if ta == tb and ha[common] != hb[common]:
                    self.fail(
                        "LogMatching",
                        now,
                        f"nodes {a} and {b} disagree at index {common} (term {ta})",
                    )
