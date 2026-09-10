"""Wire types and configuration for the Raft implementation.

Everything here is immutable and hashable so that simulator traces can be
hashed/compared for determinism checks, and so that a durable snapshot of a
node's log is a cheap tuple copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Addressing.  Nodes are 0..n-1, clients live above CLIENT_BASE so that a
# single integer address space covers every endpoint the network sees.
# ---------------------------------------------------------------------------
CLIENT_BASE = 1000


def is_client(addr: int) -> bool:
    return addr >= CLIENT_BASE


class Role(Enum):
    FOLLOWER = "follower"
    PRE_CANDIDATE = "pre-candidate"
    CANDIDATE = "candidate"
    LEADER = "leader"


# ---------------------------------------------------------------------------
# Replicated state machine commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """A client command replicated through the log.

    ``client`` + ``seq`` form the session identity used for exactly-once
    semantics (Raft dissertation section 6.3): a retried request carries the
    same (client, seq) and must not be applied twice.
    """

    client: int
    seq: int
    op: str  # "get" | "put" | "cas" | "append" | "noop" | "config"
    key: str = ""
    args: tuple[Any, ...] = ()

    def is_noop(self) -> bool:
        return self.op == "noop"


NOOP = Command(client=-1, seq=-1, op="noop")


def config_command(action: str, node: int, seq: int) -> Command:
    """A membership change (section 4.1).  It travels through the log like any
    other entry, but it is *not* state machine state: every node applies it the
    moment it appends it, not when it commits."""
    return Command(client=-2, seq=seq, op="config", args=(action, node))


@dataclass(frozen=True)
class LogEntry:
    term: int
    index: int
    cmd: Command


# ---------------------------------------------------------------------------
# RPCs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestVote:
    term: int
    candidate: int
    last_log_index: int
    last_log_term: int


@dataclass(frozen=True)
class PreVote:
    """A vote poll that does not increment anybody's term (Raft section 9.6)."""

    term: int  # the term the candidate *would* campaign in
    candidate: int
    last_log_index: int
    last_log_term: int


@dataclass(frozen=True)
class PreVoteResp:
    term: int
    granted: bool


@dataclass(frozen=True)
class RequestVoteResp:
    term: int
    granted: bool


@dataclass(frozen=True)
class AppendEntries:
    term: int
    leader: int
    prev_log_index: int
    prev_log_term: int
    entries: tuple[LogEntry, ...]
    leader_commit: int
    # Identifies the heartbeat round this message belongs to.  A majority of
    # replies to one round is what proves the sender is still leader *now*,
    # which is what ReadIndex reads and leader leases are built on.
    round_id: int = 0


@dataclass(frozen=True)
class AppendEntriesResp:
    term: int
    success: bool
    match_index: int = 0
    # prev_log_index of the request this answers.  A leader must ignore a
    # rejection that does not refer to the probe it currently has outstanding,
    # or a duplicated packet rewinds replication and the retransmissions
    # multiply.
    replies_to: int = 0
    # Fast log backtracking (section 5.3 optimisation).
    conflict_index: int = 0
    conflict_term: int = 0
    round_id: int = 0


@dataclass(frozen=True)
class InstallSnapshot:
    """Section 7.  Sent when the entries a follower needs have been compacted
    away.  Modelled as one chunk: chunking is an engineering detail of the
    transport, not of the protocol."""

    term: int
    leader: int
    last_included_index: int
    last_included_term: int
    data: tuple[Any, ...]
    members: tuple[int, ...] = ()


@dataclass(frozen=True)
class InstallSnapshotResp:
    term: int
    match_index: int


@dataclass(frozen=True)
class ClientRequest:
    cmd: Command


@dataclass(frozen=True)
class ClientReply:
    client: int
    seq: int
    ok: bool
    value: Any = None
    leader_hint: int | None = None


@dataclass(frozen=True)
class Envelope:
    src: int
    dst: int
    msg: Any

    def kind(self) -> str:
        return type(self.msg).__name__


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RaftConfig:
    election_timeout_min: int = 150
    election_timeout_max: int = 300
    heartbeat_ms: int = 50
    # Cap on entries shipped per AppendEntries, so replication of a long log
    # takes several rounds (exercises the next_index machinery).
    max_entries_per_append: int = 8
    # Append a no-op entry on election, which is how real implementations make
    # the "commit only entries of the current term" rule non-blocking.
    noop_on_elect: bool = True
    # Section 9.6: poll for votes before incrementing the term, so a node that
    # was partitioned away cannot force a healthy leader to step down just by
    # arriving with an inflated term.
    pre_vote: bool = True
    # Section 4.2.3: ignore vote requests received while a leader is still
    # believed to be alive.  Without it a candidate that cannot win can still
    # unseat a leader that is working fine.
    leader_stickiness: bool = True
    # Section 4.1: allow the cluster to change size while it runs.
    membership_changes: bool = True
    # Section 7: compact the log once this many entries have been applied past
    # the last snapshot.  0 disables compaction, and the log grows forever.
    snapshot_threshold: int = 40
    # How read-only requests are served (section 6.4):
    #   "log"        every read goes through the log like a write.  Simple,
    #                correct, and the most expensive.
    #   "read_index" the leader confirms it is still leader with one heartbeat
    #                round, then answers from its own state machine.
    #   "lease"      the leader answers immediately while it believes its lease
    #                holds.  Cheapest, and only as sound as the clocks.
    read_mode: str = "log"
