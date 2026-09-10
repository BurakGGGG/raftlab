"""Simulation parameters.  One frozen dataclass = one reproducible world."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..raft.types import RaftConfig


@dataclass(frozen=True)
class SimConfig:
    n_nodes: int = 5
    n_clients: int = 4
    duration_ms: int = 20_000
    tick_ms: int = 5
    raft: RaftConfig = field(default_factory=RaftConfig)

    # --- network ---
    latency_min: int = 2
    latency_max: int = 35
    drop_prob: float = 0.02
    dup_prob: float = 0.01
    client_latency_max: int = 25

    # --- clocks: nodes do not agree on time and do not tick at the same rate ---
    clock_drift: float = 0.02  # +-2%
    clock_offset_ms: int = 60

    # --- fault injection rates (events per simulated second) ---
    partitions_per_sec: float = 0.35
    crashes_per_sec: float = 0.60
    pause_fraction: float = 0.4
    # of those pauses, how many freeze the node's clock as well
    clock_freeze_prob: float = 0.5
    # probability that a run also gets a correlated burst of faults
    leader_churn_prob: float = 0.35
    vote_churn_prob: float = 0.35
    max_partition_ms: int = 3_000
    max_crash_ms: int = 4_000
    # Faults stop being *created* after this fraction of the run and every
    # fault is healed by heal_end, so the tail of the run is a quiet period in
    # which the cluster must demonstrate liveness.
    fault_window: float = 0.70
    heal_end: float = 0.80

    # --- workload ---
    # Section 4.1.  0 means "start with every node"; a smaller number leaves
    # the rest outside the cluster for the operator to add later.
    initial_members: int = 0
    membership_changes_per_sec: float = 0.0
    n_keys: int = 3
    client_timeout_ms: int = 500
    think_time_ms: int = 5
    # A client that is told "I am not the leader" must back off before asking
    # somebody else.  Without this, a leaderless window turns into a retry
    # storm that drowns the run in messages and tells you nothing.
    retry_backoff_ms: int = 25

    def quiet_needed(self) -> int:
        """How long a healed cluster needs before "no progress" means anything.

        Recovery costs at least one election, and a split vote costs another,
        and the client that has to notice all this has its own timeout.  Sizing
        the window in milliseconds would just encode the normal profile's
        timers; sizing it in the world's own timeouts is what makes the same
        liveness assertion meaningful in a wide-area cluster too.
        """
        return max(1_500, 6 * self.raft.election_timeout_max + 2 * self.client_timeout_ms)

    def heal_time(self) -> int:
        """Faults stop by here, leaving the tail of the run quiet."""
        return max(int(self.duration_ms * 0.5), self.duration_ms - self.quiet_needed())

    def quiet_window(self) -> int:
        return self.duration_ms - self.heal_time()


# ---------------------------------------------------------------------------
# World profiles.  The environment is a test dimension in its own right: a bug
# that never shows up under friendly timing shows up immediately when election
# timeouts stop being well separated, or when a tenth of the packets vanish.
# ---------------------------------------------------------------------------
def swarm(seed: int) -> SimConfig:
    """Swarm testing (Groce et al. 2012): let every seed pick its own world.

    A fixed profile explores one corner of the configuration space very well and
    the rest not at all.  Drawing the parameters per run -- timeouts, batch
    size, loss rates, how many clients hammer how few keys -- covers far more of
    it for the same budget.  The two timing constraints below are kept because
    violating them makes elections impossible rather than merely hard, and a
    world where Raft cannot work by construction teaches nothing.
    """
    import random as _random

    r = _random.Random(f"raftlab:swarm:{seed}")
    latency_max = r.choice([10, 25, 40, 80])
    et_min = r.choice([4, 5, 6, 8, 10]) * latency_max      # election timeout >> broadcast
    heartbeat = max(5, et_min // r.choice([3, 4, 6]))       # and >> heartbeat
    return SimConfig(
        duration_ms=r.choice([6_000, 8_000, 12_000]),
        n_clients=r.randint(2, 6),
        n_keys=r.randint(1, 4),
        latency_min=r.randint(1, 5),
        latency_max=latency_max,
        drop_prob=r.choice([0.0, 0.01, 0.03, 0.08]),
        dup_prob=r.choice([0.0, 0.01, 0.05]),
        client_timeout_ms=et_min * r.choice([2, 3, 4]),
        retry_backoff_ms=max(5, et_min // r.choice([2, 4, 8])),
        partitions_per_sec=r.choice([0.0, 0.2, 0.5, 0.9]),
        crashes_per_sec=r.choice([0.1, 0.5, 1.0, 1.5]),
        pause_fraction=r.choice([0.0, 0.3, 0.6]),
        max_crash_ms=r.choice([300, 1_200, 4_000]),
        max_partition_ms=r.choice([500, 2_000, 4_000]),
        leader_churn_prob=r.choice([0.0, 0.4, 0.8]),
        vote_churn_prob=r.choice([0.0, 0.4, 0.8]),
        raft=RaftConfig(
            election_timeout_min=et_min,
            election_timeout_max=et_min + r.choice([10, 40, 100, 200]),
            heartbeat_ms=heartbeat,
            max_entries_per_append=r.choice([1, 4, 8, 64]),
        ),
    )


PROFILES = {
    # a cluster whose membership keeps moving under it
    "churn": SimConfig(
        duration_ms=10_000,
        n_nodes=5,
        initial_members=3,
        membership_changes_per_sec=0.6,
        partitions_per_sec=0.3,
        crashes_per_sec=0.5,
    ),
    # everyday cluster: healthy network, occasional faults
    "normal": SimConfig(duration_ms=8_000),
    # timers barely separated -> split votes and duelling candidates;
    # lossy links -> retransmission-free RPCs are simply lost
    "hostile": SimConfig(
        duration_ms=8_000,
        drop_prob=0.06,
        dup_prob=0.03,
        partitions_per_sec=0.5,
        crashes_per_sec=1.0,
        max_crash_ms=1_200,
        pause_fraction=0.5,
        raft=RaftConfig(election_timeout_min=150, election_timeout_max=190, heartbeat_ms=60),
    ),
    # wide-area: latency dominates the election timeout, replication is always
    # partial when a leader dies
    "slow": SimConfig(
        duration_ms=8_000,
        latency_min=5,
        latency_max=80,
        client_timeout_ms=800,
        partitions_per_sec=0.45,
        crashes_per_sec=0.8,
        max_crash_ms=1_500,
        raft=RaftConfig(election_timeout_min=200, election_timeout_max=400, heartbeat_ms=80),
    ),
}
