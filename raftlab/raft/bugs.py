"""Injectable Raft bugs.

The point of a deterministic simulator is to catch protocol bugs that only
appear under rare interleavings.  A test harness that has never caught a bug is
just an untested claim, so the implementation can be compiled with deliberate
faults: each flag below reproduces a *real* mistake that has shipped in real
Raft implementations, and the fuzz campaign measures how quickly the checker
catches each one.

Every flag defaults to False -- that configuration is the correct algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Bugs:
    # Section 5.4.2: a leader may only mark an entry committed once an entry of
    # *its own term* is on a majority.  Dropping that check is the classic
    # "Figure 8" bug: a committed entry can later be overwritten.
    figure8_commit: bool = False

    # votedFor must survive a crash.  If it does not, a node can vote twice in
    # one term -> two leaders in the same term.
    no_persist_vote: bool = False

    # Section 5.4.1: a voter must refuse candidates whose log is less
    # up-to-date than its own, otherwise leader completeness is lost.
    no_log_up_to_date_check: bool = False

    # Followers must only truncate the log where entries actually conflict.
    # Truncating blindly deletes already-committed entries that this
    # AppendEntries simply did not carry.
    blind_truncate: bool = False

    # The AppendEntries consistency check (prev_log_index/prev_log_term) is
    # what makes the Log Matching Property hold.
    no_prev_log_check: bool = False

    # commitIndex must be min(leaderCommit, index of last new entry).  Taking
    # the leader's number verbatim commits entries this follower has not
    # matched yet, which may later be truncated.
    commit_index_unclamped: bool = False

    # Applying before commit exposes uncommitted (rollback-able) state.
    apply_before_commit: bool = False

    # Every RPC handler must reject terms older than currentTerm.
    accept_stale_terms: bool = False

    # Section 7: a snapshot that is older than what this node has already
    # committed must be ignored, or installing it rolls the state machine back.
    snapshot_accepts_stale: bool = False

    # Section 6.3 again, and the classic snapshotting pitfall: a snapshot has
    # to carry the client sessions, not just the data.  Restore without them and
    # a retried request is applied a second time after the restore -- the log
    # stays perfectly replicated, so again only the linearizability checker can
    # see it.
    snapshot_forgets_sessions: bool = False

    # Section 4.1: a configuration must take effect when it is *appended*.
    # Waiting for the commit lets the old and the new configuration each hold a
    # majority at the same time, which is two leaders in one term.
    config_on_commit: bool = False

    # Section 4.1 again: only one configuration change may be in flight.
    config_concurrent: bool = False

    # Section 6.3: without client sessions a retried request is applied twice.
    # Every Raft invariant still holds -- the log is perfectly replicated, it
    # simply contains the command twice -- so only the end-to-end
    # linearizability checker can see this one.
    no_session_dedup: bool = False

    def any_enabled(self) -> bool:
        return any(getattr(self, f.name) for f in fields(self))

    def enabled_names(self) -> list[str]:
        return [f.name for f in fields(self) if getattr(self, f.name)]

    @staticmethod
    def names() -> list[str]:
        return [f.name for f in fields(Bugs)]

    @staticmethod
    def of(name: str) -> Bugs:
        if name in ("none", "", None):
            return Bugs()
        if name not in Bugs.names():
            raise ValueError(f"unknown bug {name!r}; known: {Bugs.names()}")
        return Bugs(**{name: True})


NO_BUGS = Bugs()
