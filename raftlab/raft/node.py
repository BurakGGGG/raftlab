"""A complete Raft consensus node, written as a pure state machine.

The node performs no I/O and reads no wall clock: every entry point takes the
current time and returns the messages to send.  That is what makes the whole
system deterministically simulatable -- the simulator owns time, the network
and the disk, and the node owns only the protocol.

Implements: leader election, log replication with the fast-backup optimisation
and a one-batch-per-peer in-flight window, the commit rules of section 5.4.2,
client sessions for exactly-once semantics, crash recovery from durable state,
and log compaction with InstallSnapshot (section 7).

The log is stored relative to the snapshot: ``log[0]`` is a dummy entry standing
for (last included index, last included term), and the entry with log index *i*
lives at position ``i - snapshot_index``.  Every access goes through the helpers
below, because off-by-one errors in that translation are exactly the kind of bug
this project exists to catch.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from typing import Any

from ..kv.statemachine import KVStateMachine, apply_pure
from .bugs import NO_BUGS, Bugs
from .types import (
    NOOP,
    AppendEntries,
    AppendEntriesResp,
    ClientReply,
    ClientRequest,
    Command,
    Envelope,
    InstallSnapshot,
    InstallSnapshotResp,
    LogEntry,
    PreVote,
    PreVoteResp,
    RaftConfig,
    RequestVote,
    RequestVoteResp,
    Role,
)


class RaftNode:
    def __init__(
        self,
        node_id: int,
        peers: tuple[int, ...],
        cfg: RaftConfig,
        seed: int,
        bugs: Bugs = NO_BUGS,
        now: int = 0,
        members: Iterable[int] | None = None,
    ) -> None:
        self.id = node_id
        self.all_nodes = tuple(sorted(peers))
        self.set_members(members if members is not None else peers)
        self.cfg = cfg
        self.bugs = bugs
        self.rng = random.Random(seed)

        # --- durable state (survives a crash) ---
        self.current_term = 0
        self.voted_for: int | None = None
        # log[0] is a dummy standing for the snapshot point; real entries follow
        self.log: list[LogEntry] = [LogEntry(0, 0, NOOP)]
        self.snapshot_index = 0
        self.snapshot_term = 0
        self.snapshot_data: tuple[Any, ...] = ((), ())
        self.snapshot_members: tuple[int, ...] = tuple(sorted(self.members))
        self.config_index = 0
        self.config_changes = 0

        # --- volatile state ---
        self.role = Role.FOLLOWER
        self.leader_id: int | None = None
        self.commit_index = 0
        self.last_applied = 0
        self.sm = KVStateMachine(dedup=not bugs.no_session_dedup)
        self.votes: set[int] = set()
        self.pre_votes: set[int] = set()
        self.pre_term = 0
        self.last_leader_contact = -(1 << 30)
        self.next_index: dict[int, int] = {}
        self.match_index: dict[int, int] = {}
        # Highest log index already put on the wire to each peer.  Without it a
        # leader that answers every AppendEntriesResp with another
        # AppendEntries turns one duplicated packet into two permanent
        # request/response streams, and the traffic grows geometrically.  Real
        # implementations keep the same window (etcd calls it Progress.Inflights).
        self.sent_upto: dict[int, int] = {}
        # log index -> (client address, seq) awaiting a reply from this leader
        self.pending: dict[int, tuple[int, int]] = {}
        self.pending_by_req: dict[tuple[int, int], int] = {}

        self.election_deadline = 0
        self.heartbeat_deadline = 0
        self.dirty = True  # durable state changed since the simulator last saved it
        self.applied_trace: list[tuple[int, Command]] = []  # for the safety checker
        self.snapshots_taken = 0
        self.snapshots_installed = 0
        # read-only request machinery (section 6.4)
        self.round_id = 0
        self.round_open = 0        # the round most recently opened
        self.round_done = 0        # highest round a majority has answered
        self.term_start_index = 0  # this leader's first entry of its own term
        # Acks are tracked *per round*.  Keeping a single "current round" and
        # resetting it on every new heartbeat throws away the replies still in
        # flight, and under a steady read load no round ever reaches a majority:
        # the reads then wait forever while the cluster looks perfectly healthy.
        self.round_acks: dict[int, set] = {}
        self.round_sent: dict[int, int] = {}
        self.round_sent_at = 0
        self.lease_until = -(1 << 30)
        self.read_waiters: list[tuple[int, int, int, Command]] = []
        self.reads_served_locally = 0
        self.reads_via_log = 0
        # Instrumentation for the invariant checker: the lowest log index this
        # node has deleted since the checker last looked.  Lets the checker
        # validate logs incrementally instead of rescanning them every event.
        self.min_truncated_index: int | None = None
        self.truncated_committed: int | None = None
        self.reset_election_timer(now)

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------
    @property
    def last_index(self) -> int:
        return self.snapshot_index + len(self.log) - 1

    @property
    def last_term(self) -> int:
        return self.log[-1].term

    def pos(self, index: int) -> int:
        """Log index -> position in self.log.  Negative means compacted away."""
        return index - self.snapshot_index

    def entry_at(self, index: int) -> LogEntry:
        return self.log[self.pos(index)]

    def has_index(self, index: int) -> bool:
        return self.snapshot_index <= index <= self.last_index

    def term_at(self, index: int) -> int:
        """-1 for an index this node cannot speak about: past the end of the
        log, or already compacted into the snapshot."""
        return self.log[self.pos(index)].term if self.has_index(index) else -1

    def majority(self) -> int:
        return len(self.members) // 2 + 1

    def set_members(self, members: Iterable[int]) -> None:
        self.members = frozenset(members)
        self.peers = tuple(sorted(m for m in self.members if m != self.id))
        self.cluster_size = len(self.members)
        if getattr(self, "role", None) is Role.LEADER:
            self.init_progress()

    def init_progress(self) -> None:
        """Section 4.1: a leader has to start tracking a server the moment the
        configuration adds it.  Without this the leader has no nextIndex for the
        new member, never learns where its log begins, and the cluster wedges
        with a majority it can no longer reach."""
        for p in self.peers:
            self.next_index.setdefault(p, self.last_index + 1)
            self.match_index.setdefault(p, 0)
            self.sent_upto.setdefault(p, 0)
        for gone in [p for p in self.next_index if p not in self.members]:
            self.next_index.pop(gone, None)
            self.match_index.pop(gone, None)
            self.sent_upto.pop(gone, None)

    def is_member(self) -> bool:
        return self.id in self.members

    def on_appended(self, entry: LogEntry) -> None:
        if entry.cmd.op == "config" and not self.bugs.config_on_commit:
            self.apply_config_entry(entry)

    def apply_config_entry(self, entry: LogEntry) -> None:
        """Section 4.1: a configuration takes effect as soon as it is appended,
        committed or not.  Waiting for the commit is the classic way to end up
        with two disjoint majorities and two leaders."""
        if entry.cmd.op != "config":
            return
        action, node = entry.cmd.args
        members = set(self.members)
        if action == "add":
            members.add(node)
        else:
            members.discard(node)
        self.set_members(members)
        self.config_index = entry.index
        self.config_changes += 1

    def rebuild_config(self) -> None:
        """After a restart: the snapshot carries a configuration, the log tail
        may carry newer ones."""
        self.set_members(self.snapshot_members)
        self.config_index = 0
        for e in self.log[1:]:
            if e.cmd.op == "config" and not self.bugs.config_on_commit:
                self.apply_config_entry(e)

    def note_truncation(self, index: int) -> None:
        if index <= self.last_index:
            cur = self.min_truncated_index
            self.min_truncated_index = index if cur is None else min(cur, index)
            if index <= self.commit_index:
                # Deleting an entry this node had already committed.  Recorded
                # at the moment it happens, because the commit index may move
                # again before the checker looks.
                self.truncated_committed = index

    def reset_election_timer(self, now: int) -> None:
        lo, hi = self.cfg.election_timeout_min, self.cfg.election_timeout_max
        self.election_deadline = now + self.rng.randint(lo, hi)

    # ------------------------------------------------------------------
    # durability: the simulator snapshots this after every event and restores
    # it (and nothing else) on restart.
    # ------------------------------------------------------------------
    def durable(self) -> dict[str, Any]:
        self.dirty = False
        return {
            "current_term": self.current_term,
            "voted_for": self.voted_for,
            "log": tuple(self.log),
            "snapshot_index": self.snapshot_index,
            "snapshot_term": self.snapshot_term,
            "snapshot_data": self.snapshot_data,
            "snapshot_members": self.snapshot_members,
        }

    def restart(self, now: int, durable: dict[str, Any]) -> None:
        self.current_term = durable["current_term"]
        self.voted_for = None if self.bugs.no_persist_vote else durable["voted_for"]
        self.log = list(durable["log"])
        self.snapshot_index = durable["snapshot_index"]
        self.snapshot_term = durable["snapshot_term"]
        self.snapshot_data = durable["snapshot_data"]
        self.snapshot_members = durable["snapshot_members"]
        self.rebuild_config()
        self.role = Role.FOLLOWER
        self.leader_id = None
        # Everything inside the snapshot is committed by definition, so recovery
        # starts there rather than replaying from zero.
        self.commit_index = self.snapshot_index
        self.last_applied = self.snapshot_index
        self.sm = KVStateMachine(dedup=not self.bugs.no_session_dedup)
        self.sm.restore(self.snapshot_data)
        self.votes = set()
        self.pre_votes = set()
        self.last_leader_contact = -(1 << 30)
        self.next_index = {}
        self.match_index = {}
        self.sent_upto = {}
        self.pending = {}
        self.pending_by_req = {}
        self.clear_read_state()
        self.applied_trace = []
        self.min_truncated_index = None
        self.truncated_committed = None
        self.dirty = True
        self.reset_election_timer(now)

    # ------------------------------------------------------------------
    # role transitions
    # ------------------------------------------------------------------
    def step_down(self, term: int, now: int) -> None:
        if term > self.current_term:
            self.current_term = term
            self.voted_for = None
            self.dirty = True
        self.role = Role.FOLLOWER
        self.leader_id = None
        self.votes = set()
        self.pre_votes = set()
        self.pending.clear()
        self.pending_by_req.clear()
        self.clear_read_state()
        self.reset_election_timer(now)

    def start_election(self, now: int) -> list[Envelope]:
        """With pre-vote on, poll first and only campaign if the poll wins."""
        if not self.cfg.pre_vote:
            return self.become_candidate(now)
        self.role = Role.PRE_CANDIDATE
        self.leader_id = None
        self.pre_term = self.current_term + 1
        self.pre_votes = {self.id}
        self.reset_election_timer(now)
        if len(self.pre_votes) >= self.majority():
            return self.become_candidate(now)
        return [
            Envelope(
                self.id,
                p,
                PreVote(self.pre_term, self.id, self.last_index, self.last_term),
            )
            for p in self.peers
        ]

    def in_leader_lease(self, now: int) -> bool:
        return (
            self.cfg.leader_stickiness
            and now - self.last_leader_contact < self.cfg.election_timeout_min
        )

    def log_is_current(self, last_log_term: int, last_log_index: int) -> bool:
        if self.bugs.no_log_up_to_date_check:
            return True
        return last_log_term > self.last_term or (
            last_log_term == self.last_term and last_log_index >= self.last_index
        )

    def become_candidate(self, now: int) -> list[Envelope]:
        self.current_term += 1
        self.role = Role.CANDIDATE
        self.voted_for = self.id
        self.leader_id = None
        self.votes = {self.id}
        self.dirty = True
        self.reset_election_timer(now)
        out = [
            Envelope(
                self.id,
                p,
                RequestVote(self.current_term, self.id, self.last_index, self.last_term),
            )
            for p in self.peers
        ]
        if len(self.votes) >= self.majority():  # single-node cluster
            out += self.become_leader(now)
        return out

    def become_leader(self, now: int) -> list[Envelope]:
        self.role = Role.LEADER
        self.leader_id = self.id
        self.next_index = dict.fromkeys(self.peers, self.last_index + 1)
        self.match_index = dict.fromkeys(self.peers, 0)
        self.sent_upto = dict.fromkeys(self.peers, 0)
        self.term_start_index = self.last_index + 1
        self.pending.clear()
        self.pending_by_req.clear()
        self.clear_read_state()
        if self.cfg.noop_on_elect:
            self.log.append(LogEntry(self.current_term, self.last_index + 1, NOOP))
            self.dirty = True
        self.heartbeat_deadline = now + self.cfg.heartbeat_ms
        return self.broadcast_append(now)

    # ------------------------------------------------------------------
    # replication
    # ------------------------------------------------------------------
    def make_append(self, peer: int, round_id: int = 0) -> Envelope:
        next_i = max(1, self.next_index.get(peer, self.last_index + 1))
        if next_i <= self.snapshot_index:
            # What this peer needs no longer exists as log entries.
            self.sent_upto[peer] = max(self.sent_upto.get(peer, 0), self.snapshot_index)
            return Envelope(
                self.id,
                peer,
                InstallSnapshot(
                    term=self.current_term,
                    leader=self.id,
                    last_included_index=self.snapshot_index,
                    last_included_term=self.snapshot_term,
                    data=self.snapshot_data,
                    members=tuple(sorted(self.members)),
                ),
            )
        prev = next_i - 1
        start = self.pos(next_i)
        entries = tuple(self.log[start : start + self.cfg.max_entries_per_append])
        self.sent_upto[peer] = max(self.sent_upto.get(peer, 0), prev + len(entries))
        return Envelope(
            self.id,
            peer,
            AppendEntries(
                term=self.current_term,
                leader=self.id,
                prev_log_index=prev,
                prev_log_term=self.term_at(prev),
                entries=entries,
                leader_commit=self.commit_index,
                round_id=round_id,
            ),
        )

    def clear_read_state(self) -> None:
        self.round_open = 0
        self.round_done = 0
        self.round_acks = {}
        self.round_sent = {}
        self.read_waiters = []
        self.lease_until = -(1 << 30)

    def broadcast_append(self, now: int) -> list[Envelope]:
        """Heartbeat: everybody hears from the leader, caught up or not.

        Each broadcast opens a numbered round.  A majority of replies to that
        round is proof the sender was still leader at the moment it was sent --
        the fact ReadIndex needs, and the fact a lease extrapolates from.
        """
        self.heartbeat_deadline = now + self.cfg.heartbeat_ms
        self.round_id += 1
        self.round_open = self.round_id
        self.round_acks[self.round_id] = {self.id}
        self.round_sent[self.round_id] = now
        self.round_sent_at = now
        if len(self.round_acks) > 64:  # keep the bookkeeping bounded
            for old_round in sorted(self.round_acks)[:32]:
                self.round_acks.pop(old_round, None)
                self.round_sent.pop(old_round, None)
        out = [self.make_append(p, self.round_id) for p in self.peers]
        if len(self.round_acks[self.round_id]) >= self.majority():
            out += self.complete_round(self.round_id, now)
        return out

    def complete_round(self, round_id: int, now: int) -> list[Envelope]:
        """A majority answered this round: leadership is confirmed as of the
        moment that round was sent."""
        if round_id > self.round_done:
            self.round_done = round_id
            # The lease runs from when the round was *sent*, not from now: that
            # is the last instant we know no election could have started.
            self.lease_until = max(
                self.lease_until,
                self.round_sent.get(round_id, now) + self.cfg.election_timeout_min,
            )
        for finished in [r for r in self.round_acks if r <= self.round_done]:
            self.round_acks.pop(finished, None)
            self.round_sent.pop(finished, None)
        return self.flush_reads()

    def flush_reads(self) -> list[Envelope]:
        out: list[Envelope] = []
        keep = []
        for waiter in self.read_waiters:
            min_round, read_index, src, cmd = waiter
            if min_round <= self.round_done and self.last_applied >= read_index:
                out.append(self.reply_read(src, cmd))
            else:
                keep.append(waiter)
        self.read_waiters = keep
        return out

    def reply_read(self, src: int, cmd: Command) -> Envelope:
        _, value = apply_pure(self.sm.data.get(cmd.key), cmd.op, cmd.args)
        self.reads_served_locally += 1
        return Envelope(
            self.id, src, ClientReply(cmd.client, cmd.seq, True, value, self.id)
        )

    def read_barrier(self) -> int:
        """The lowest index a read is allowed to be answered from.

        Confirming leadership is not enough on its own: a leader that has just
        been elected inherits whatever commit index it happened to have as a
        follower, which can lag the entries a previous leader really did commit.
        Section 6.4 closes that by making the leader commit an entry of its own
        term first -- which is what the no-op appended on election is for.
        """
        if self.cfg.noop_on_elect:
            return max(self.commit_index, self.term_start_index)
        return self.commit_index

    def serve_read(self, now: int, src: int, cmd: Command) -> list[Envelope]:
        """Section 6.4.  Either the lease says we are still leader, or we ask."""
        barrier = self.read_barrier()
        if (
            self.cfg.read_mode == "lease"
            and now < self.lease_until
            and self.last_applied >= barrier
        ):
            return [self.reply_read(src, cmd)]
        out: list[Envelope] = []
        if self.round_open and self.round_sent_at == now:
            min_round = self.round_open  # a round opened this instant postdates us
        else:
            out += self.broadcast_append(now)
            min_round = self.round_open
        self.read_waiters.append((min_round, barrier, src, cmd))
        return out + self.flush_reads()

    def has_unsent(self, peer: int) -> bool:
        return self.last_index > self.sent_upto.get(peer, 0)

    def replicate(self, now: int) -> list[Envelope]:
        """Ship only what is not already on the wire; the heartbeat retransmits."""
        return [self.make_append(p) for p in self.peers if self.has_unsent(p)]

    def advance_commit(self) -> None:
        """Section 5.4.2: commit index N once a majority has it *and* the entry
        belongs to the current term."""
        if self.role is not Role.LEADER:
            return
        # A leader that has been removed from the configuration keeps serving
        # until the removal commits, but it no longer counts towards a majority.
        own = [self.last_index] if self.is_member() else []
        matches = sorted(
            own + [self.match_index.get(p, 0) for p in self.peers], reverse=True
        )
        if not matches:
            return
        n = matches[min(len(matches) - 1, len(self.members) // 2)]
        if n > self.commit_index:
            if self.term_at(n) == self.current_term or self.bugs.figure8_commit:
                self.commit_index = n

    # ------------------------------------------------------------------
    # applying committed entries
    # ------------------------------------------------------------------
    def apply_committed(self, now: int) -> list[Envelope]:
        out: list[Envelope] = []
        target = self.last_index if self.bugs.apply_before_commit else min(
            self.commit_index, self.last_index
        )
        while self.last_applied < target:
            self.last_applied += 1
            entry = self.entry_at(self.last_applied)
            if entry.cmd.op == "config":
                if self.bugs.config_on_commit:
                    self.apply_config_entry(entry)
                if (
                    entry.cmd.args[0] == "remove"
                    and entry.cmd.args[1] == self.id
                    and self.role is Role.LEADER
                ):
                    # Section 4.2.2: a leader that has removed itself steps down
                    # once the removal is committed.
                    self.step_down(self.current_term, now)
            result = self.sm.apply(entry.cmd)
            self.applied_trace.append((self.last_applied, entry.cmd))
            req = self.pending.pop(self.last_applied, None)
            if req is not None:
                self.pending_by_req.pop((req[0], req[1]), None)
                if self.role is Role.LEADER:
                    out.append(
                        Envelope(
                            self.id,
                            req[0],
                            ClientReply(entry.cmd.client, req[1], True, result, self.id),
                        )
                    )
        self.maybe_compact()
        if self.read_waiters:
            out += self.flush_reads()
        return out

    # ------------------------------------------------------------------
    # log compaction (section 7)
    # ------------------------------------------------------------------
    def maybe_compact(self) -> None:
        threshold = self.cfg.snapshot_threshold
        if not threshold or self.last_applied - self.snapshot_index < threshold:
            return
        self.take_snapshot(self.last_applied)

    def take_snapshot(self, index: int) -> None:
        """Freeze the state machine at `index` and drop the entries below it."""
        if index <= self.snapshot_index or index > self.last_applied:
            return
        term = self.term_at(index)
        keep = self.log[self.pos(index) + 1 :]
        self.snapshot_index = index
        self.snapshot_term = term
        self.snapshot_data = self.sm.snapshot()
        self.snapshot_members = tuple(sorted(self.members))
        self.log = [LogEntry(term, index, NOOP)] + keep
        self.snapshots_taken += 1
        self.dirty = True

    # ------------------------------------------------------------------
    # event entry points
    # ------------------------------------------------------------------
    def on_tick(self, now: int) -> list[Envelope]:
        out: list[Envelope] = []
        if self.role is Role.LEADER:
            if now >= self.heartbeat_deadline:
                out += self.broadcast_append(now)
        elif now >= self.election_deadline and self.is_member():
            # A node that is not in the configuration must stay quiet: it cannot
            # win, and campaigning would only disrupt the cluster it just left.
            out += self.start_election(now)
        out += self.apply_committed(now)
        return out

    def on_message(self, now: int, env: Envelope) -> list[Envelope]:
        msg = env.msg
        handlers: dict[type, Callable[[int, int, Any], list[Envelope]]] = {
            PreVote: self.handle_pre_vote,
            PreVoteResp: self.handle_pre_vote_resp,
            RequestVote: self.handle_request_vote,
            RequestVoteResp: self.handle_request_vote_resp,
            AppendEntries: self.handle_append_entries,
            AppendEntriesResp: self.handle_append_entries_resp,
            InstallSnapshot: self.handle_install_snapshot,
            InstallSnapshotResp: self.handle_install_snapshot_resp,
            ClientRequest: self.handle_client_request,
        }
        handler = handlers.get(type(msg))
        if handler is None:
            return []
        out = handler(now, env.src, msg)
        out += self.apply_committed(now)
        return out

    # -- vote ----------------------------------------------------------
    def handle_pre_vote(self, now: int, src: int, msg: PreVote) -> list[Envelope]:
        """Answer a poll without touching currentTerm or votedFor."""
        granted = (
            msg.term > self.current_term
            and self.log_is_current(msg.last_log_term, msg.last_log_index)
            and not self.in_leader_lease(now)
        )
        term = msg.term if granted else self.current_term
        return [Envelope(self.id, src, PreVoteResp(term, granted))]

    def handle_pre_vote_resp(self, now: int, src: int, msg: PreVoteResp) -> list[Envelope]:
        if not msg.granted and msg.term > self.current_term:
            self.step_down(msg.term, now)
            return []
        if self.role is not Role.PRE_CANDIDATE or msg.term != self.pre_term:
            return []
        if msg.granted:
            self.pre_votes.add(src)
            if len(self.pre_votes) >= self.majority():
                return self.become_candidate(now)
        return []

    def handle_request_vote(self, now: int, src: int, msg: RequestVote) -> list[Envelope]:
        if msg.term < self.current_term and not self.bugs.accept_stale_terms:
            return [Envelope(self.id, src, RequestVoteResp(self.current_term, False))]
        if self.in_leader_lease(now) and msg.term > self.current_term:
            # A leader is still working as far as this node knows: refuse, and
            # deliberately do not adopt the candidate's term.
            return [Envelope(self.id, src, RequestVoteResp(self.current_term, False))]
        if msg.term > self.current_term:
            self.step_down(msg.term, now)

        up_to_date = self.log_is_current(msg.last_log_term, msg.last_log_index)

        granted = (
            msg.term >= self.current_term
            and self.voted_for in (None, msg.candidate)
            and up_to_date
        )
        if granted:
            self.voted_for = msg.candidate
            self.dirty = True
            self.reset_election_timer(now)
        return [Envelope(self.id, src, RequestVoteResp(self.current_term, granted))]

    def handle_request_vote_resp(
        self, now: int, src: int, msg: RequestVoteResp
    ) -> list[Envelope]:
        if msg.term > self.current_term:
            self.step_down(msg.term, now)
            return []
        if self.role is not Role.CANDIDATE or msg.term != self.current_term:
            return []
        if msg.granted:
            self.votes.add(src)
            if len(self.votes) >= self.majority():
                return self.become_leader(now)
        return []

    # -- replication ---------------------------------------------------
    def handle_append_entries(
        self, now: int, src: int, msg: AppendEntries
    ) -> list[Envelope]:
        def reply(success: bool, match: int = 0, ci: int = 0, ct: int = 0):
            return [
                Envelope(
                    self.id,
                    src,
                    AppendEntriesResp(
                        self.current_term, success, match, msg.prev_log_index, ci, ct,
                        msg.round_id,
                    ),
                )
            ]

        if msg.term < self.current_term and not self.bugs.accept_stale_terms:
            return reply(False)
        if msg.term > self.current_term:
            self.step_down(msg.term, now)
        if self.role in (Role.CANDIDATE, Role.PRE_CANDIDATE) and msg.term == self.current_term:
            self.role = Role.FOLLOWER
        self.leader_id = msg.leader
        self.last_leader_contact = now
        self.reset_election_timer(now)

        # --- consistency check (Log Matching Property) ---
        if msg.prev_log_index < self.snapshot_index:
            # Already compacted, therefore already committed: the leader is
            # behind our snapshot, so tell it where we actually are.
            return reply(True, match=self.snapshot_index)
        if msg.prev_log_index > self.last_index:
            return reply(False, ci=self.last_index + 1, ct=0)
        if (
            self.term_at(msg.prev_log_index) != msg.prev_log_term
            and not self.bugs.no_prev_log_check
        ):
            conflict_term = self.term_at(msg.prev_log_index)
            ci = msg.prev_log_index
            while ci > self.snapshot_index + 1 and self.term_at(ci - 1) == conflict_term:
                ci -= 1
            return reply(False, ci=ci, ct=conflict_term)

        # --- append ---
        if msg.entries:
            if self.bugs.blind_truncate:
                self.note_truncation(msg.prev_log_index + 1)
                del self.log[self.pos(msg.prev_log_index) + 1 :]
                self.log.extend(msg.entries)
                self.dirty = True
                for e in msg.entries:
                    self.on_appended(e)
            else:
                for e in msg.entries:
                    if e.index <= self.snapshot_index:
                        continue  # already in our snapshot
                    if e.index <= self.last_index:
                        if self.term_at(e.index) != e.term:
                            self.note_truncation(e.index)
                            del self.log[self.pos(e.index) :]  # conflict: drop the tail
                            self.log.append(e)
                            self.dirty = True
                            self.on_appended(e)
                    else:
                        self.log.append(e)
                        self.dirty = True
                        self.on_appended(e)

        # --- commit ---
        if msg.leader_commit > self.commit_index:
            last_new = msg.prev_log_index + len(msg.entries)
            if self.bugs.commit_index_unclamped:
                self.commit_index = msg.leader_commit
            else:
                self.commit_index = min(msg.leader_commit, last_new)

        return reply(True, match=msg.prev_log_index + len(msg.entries))

    def handle_append_entries_resp(
        self, now: int, src: int, msg: AppendEntriesResp
    ) -> list[Envelope]:
        if msg.term > self.current_term:
            self.step_down(msg.term, now)
            return []
        if self.role is not Role.LEADER or msg.term != self.current_term:
            return []

        out: list[Envelope] = []
        acks = self.round_acks.get(msg.round_id)
        if acks is not None:
            # A reply at all -- even a rejection -- means this peer still
            # recognises us as leader for this term.
            acks.add(src)
            if len(acks) >= self.majority():
                out += self.complete_round(msg.round_id, now)

        if msg.success:
            self.match_index[src] = max(self.match_index.get(src, 0), msg.match_index)
            self.next_index[src] = self.match_index[src] + 1
            self.advance_commit()
            # One batch in flight per peer: ship more only once the peer has
            # acknowledged everything already on the wire.  Reacting to *every*
            # response instead means a duplicated packet forks a second
            # request/response chain, and the chains multiply until the link
            # saturates.  Losses are recovered by the heartbeat.
            if self.has_unsent(src) and self.match_index[src] >= self.sent_upto.get(src, 0):
                out.append(self.make_append(src))
            return out

        if msg.replies_to != self.next_index.get(src, self.last_index + 1) - 1:
            return out  # a stale or duplicated rejection: it answers an old probe

        # Fast backup: jump over the whole conflicting term instead of
        # decrementing next_index one index per round trip.
        if msg.conflict_term > 0:
            last_of_term = 0
            for i in range(self.last_index, self.snapshot_index, -1):
                if self.term_at(i) == msg.conflict_term:
                    last_of_term = i
                    break
            self.next_index[src] = (
                last_of_term + 1 if last_of_term else max(1, msg.conflict_index)
            )
        else:
            self.next_index[src] = max(1, msg.conflict_index)
        self.sent_upto[src] = self.next_index[src] - 1  # rewind the window
        out.append(self.make_append(src))
        return out

    # -- snapshots (section 7) -----------------------------------------
    def handle_install_snapshot(
        self, now: int, src: int, msg: InstallSnapshot
    ) -> list[Envelope]:
        def reply(match: int):
            return [Envelope(self.id, src, InstallSnapshotResp(self.current_term, match))]

        if msg.term < self.current_term and not self.bugs.accept_stale_terms:
            return reply(0)
        if msg.term > self.current_term:
            self.step_down(msg.term, now)
        if self.role in (Role.CANDIDATE, Role.PRE_CANDIDATE) and msg.term == self.current_term:
            self.role = Role.FOLLOWER
        self.leader_id = msg.leader
        self.last_leader_contact = now
        self.reset_election_timer(now)

        lii, lit = msg.last_included_index, msg.last_included_term
        if not self.bugs.snapshot_accepts_stale:
            if lii <= self.commit_index:
                # Everything in it is already committed here: nothing to do.
                return reply(min(self.last_index, self.commit_index))
            if self.term_at(lii) == lit:
                # We hold that entry already; the snapshot only tells us it is
                # committed, so keep the log and move the commit index.
                self.commit_index = max(self.commit_index, lii)
                return reply(lii)

        keep: list[LogEntry] = []
        if self.term_at(lii) == lit:
            keep = self.log[self.pos(lii) + 1 :]
        elif lii < self.last_index:
            self.note_truncation(lii + 1)

        self.snapshot_index = lii
        self.snapshot_term = lit
        self.snapshot_data = msg.data
        if msg.members:
            self.snapshot_members = msg.members
            self.set_members(msg.members)
            for e in keep:
                self.apply_config_entry(e)
        self.log = [LogEntry(lit, lii, NOOP)] + keep
        self.sm = KVStateMachine(dedup=not self.bugs.no_session_dedup)
        self.sm.restore(msg.data)
        if self.bugs.snapshot_forgets_sessions:
            self.sm.sessions.clear()
        self.commit_index = max(self.commit_index, lii)
        self.last_applied = lii
        self.snapshots_installed += 1
        self.dirty = True
        return reply(lii)

    def handle_install_snapshot_resp(
        self, now: int, src: int, msg: InstallSnapshotResp
    ) -> list[Envelope]:
        if msg.term > self.current_term:
            self.step_down(msg.term, now)
            return []
        if self.role is not Role.LEADER or msg.term != self.current_term:
            return []
        if msg.match_index:
            self.match_index[src] = max(self.match_index.get(src, 0), msg.match_index)
            self.next_index[src] = self.match_index[src] + 1
            self.advance_commit()
            if self.has_unsent(src) and self.match_index[src] >= self.sent_upto.get(src, 0):
                return [self.make_append(src)]
        return []

    # -- clients -------------------------------------------------------
    def handle_client_request(
        self, now: int, src: int, msg: ClientRequest
    ) -> list[Envelope]:
        cmd = msg.cmd
        if self.role is not Role.LEADER:
            return [
                Envelope(
                    self.id,
                    src,
                    ClientReply(cmd.client, cmd.seq, False, None, self.leader_id),
                )
            ]

        session = self.sm.sessions.get(cmd.client)
        if self.sm.dedup and session is not None and cmd.seq <= session[0]:
            # Already applied (this is a retry): answer from the session cache.
            value = session[1] if cmd.seq == session[0] else None
            return [
                Envelope(self.id, src, ClientReply(cmd.client, cmd.seq, True, value, self.id))
            ]

        if cmd.op == "config":
            if not self.cfg.membership_changes:
                return [Envelope(self.id, src, ClientReply(cmd.client, cmd.seq, False, None, self.id))]
            if self.config_index > self.commit_index and not self.bugs.config_concurrent:
                # Section 4.1: one change at a time, or two configurations can
                # each believe they hold a majority.
                return [Envelope(self.id, src, ClientReply(cmd.client, cmd.seq, False, None, self.id))]

        if cmd.op == "get" and self.cfg.read_mode != "log":
            return self.serve_read(now, src, cmd)
        self.reads_via_log += 1 if cmd.op == "get" else 0

        existing = self.pending_by_req.get((cmd.client, cmd.seq))
        if existing is not None:
            self.pending[existing] = (src, cmd.seq)  # retry while in flight
            return []

        index = self.last_index + 1
        entry = LogEntry(self.current_term, index, cmd)
        self.log.append(entry)
        self.dirty = True
        self.on_appended(entry)
        self.pending[index] = (src, cmd.seq)
        self.pending_by_req[(cmd.client, cmd.seq)] = index
        return self.replicate(now)
