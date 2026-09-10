"""Protocol-level tests: the algorithm itself, with the simulator as the harness."""

import dataclasses
import unittest

from raftlab.raft.node import RaftNode
from raftlab.raft.types import RaftConfig, Role
from raftlab.runner import run_and_check
from raftlab.sim.cluster import Simulator
from raftlab.sim.config import PROFILES, SimConfig
from raftlab.sim.faults import Fault

QUIET = SimConfig(
    duration_ms=4_000,
    partitions_per_sec=0.0,
    crashes_per_sec=0.0,
    leader_churn_prob=0.0,
    vote_churn_prob=0.0,
    drop_prob=0.0,
    dup_prob=0.0,
)


def logs_agree(sim: Simulator) -> bool:
    """Every pair of nodes must agree on every index they both still hold.

    "Both still hold" matters once log compaction is on: a node that installed a
    snapshot no longer has the entries below it to compare.
    """
    nodes = sim.nodes
    for a in range(len(nodes)):
        for b in range(a + 1, len(nodes)):
            lo = max(nodes[a].snapshot_index, nodes[b].snapshot_index) + 1
            hi = min(nodes[a].last_index, nodes[b].last_index)
            for i in range(lo, hi + 1):
                if nodes[a].entry_at(i) != nodes[b].entry_at(i):
                    return False
    return True


class TestQuietCluster(unittest.TestCase):
    def setUp(self):
        self.sim = Simulator(QUIET, seed=11)
        self.result = self.sim.run()

    def test_elects_exactly_one_leader(self):
        self.assertEqual(self.result.stats["elections"], 1)
        leaders = [n for n in self.sim.nodes if n.role is Role.LEADER]
        self.assertEqual(len(leaders), 1)

    def test_no_violations(self):
        self.assertEqual(self.result.violations, [])

    def test_every_operation_completes(self):
        stats = self.result.stats
        self.assertGreater(stats["ops_completed"], 100)
        self.assertLessEqual(stats["ops_pending"], QUIET.n_clients)

    def test_logs_converge(self):
        self.assertTrue(logs_agree(self.sim))

    def test_state_machines_converge(self):
        applied = min(n.last_applied for n in self.sim.nodes)
        self.assertGreater(applied, 50)
        # Every node applied the same command sequence over the range they all
        # still hold, so their state machines cannot have diverged.
        self.assertTrue(logs_agree(self.sim))
        leader = next(n for n in self.sim.nodes if n.role is Role.LEADER)
        for n in self.sim.nodes:
            if n.last_applied == leader.last_applied:
                self.assertEqual(n.sm.digest(), leader.sm.digest())

    def test_compaction_keeps_the_log_bounded(self):
        threshold = QUIET.raft.snapshot_threshold
        self.assertGreater(self.result.stats["snapshots"], 0)
        for n in self.sim.nodes:
            self.assertLessEqual(
                n.last_index - n.snapshot_index,
                threshold + self.result.stats["ops_started"],
            )
            self.assertLessEqual(n.snapshot_index, n.commit_index)


class TestSafetyUnderFaults(unittest.TestCase):
    def test_no_majority_means_no_progress_but_no_damage(self):
        """Split 5 nodes into 2+2+1: nothing can commit, nothing may break."""
        cfg = dataclasses.replace(QUIET, duration_ms=8_000)
        faults = [Fault(0, "partition", 500, 6_400, "fixed", ((0, 1), (2, 3), (4,)))]
        run = run_and_check(cfg, seed=5, faults=faults)
        completions = [
            o for o in run.sim.history.ops if o.completed and o.ret and 1_500 < o.ret < 6_300
        ]
        self.assertEqual(completions, [], "committed something without a majority")
        # and the cluster must recover once the partition heals
        self.assertGreater(run.sim.stats["ops_after_heal"], 0)
        self.assertEqual(list(run.failures), [])

    def test_survives_the_hostile_profile(self):
        for seed in range(1, 16):
            run = run_and_check(PROFILES["hostile"], seed)
            self.assertTrue(run.ok, f"seed {seed}: {run.first}")


class TestNodeRecovery(unittest.TestCase):
    def test_restart_keeps_durable_state_and_drops_volatile(self):
        node = RaftNode(0, (0, 1, 2), RaftConfig(), seed=1)
        node.on_tick(1_000)  # times out, becomes a (pre-)candidate
        node.current_term = 4
        node.voted_for = 2
        node.commit_index = 3
        durable = node.durable()

        node.role = Role.LEADER
        node.commit_index = 9
        node.restart(2_000, durable)

        self.assertEqual(node.current_term, 4)
        self.assertEqual(node.voted_for, 2)
        self.assertIs(node.role, Role.FOLLOWER)
        self.assertEqual(node.commit_index, 0)
        self.assertEqual(node.last_applied, 0)

    def test_single_node_cluster_elects_itself(self):
        node = RaftNode(0, (0,), RaftConfig(), seed=1)
        node.on_tick(10_000)
        self.assertIs(node.role, Role.LEADER)


class TestPreVote(unittest.TestCase):
    def test_pre_vote_does_not_touch_the_term(self):
        node = RaftNode(0, (0, 1, 2), RaftConfig(pre_vote=True), seed=1)
        node.on_tick(10_000)
        self.assertIs(node.role, Role.PRE_CANDIDATE)
        self.assertEqual(node.current_term, 0)
        self.assertIsNone(node.voted_for)

    def test_without_pre_vote_the_term_moves_immediately(self):
        node = RaftNode(0, (0, 1, 2), RaftConfig(pre_vote=False), seed=1)
        node.on_tick(10_000)
        self.assertIs(node.role, Role.CANDIDATE)
        self.assertEqual(node.current_term, 1)
        self.assertEqual(node.voted_for, 0)


if __name__ == "__main__":
    unittest.main()


class TestSnapshots(unittest.TestCase):
    """Section 7: compaction, and the InstallSnapshot path it forces."""

    def test_a_node_that_falls_far_behind_is_caught_up_by_snapshot(self):
        cfg = dataclasses.replace(QUIET, duration_ms=9_000)
        faults = [Fault(0, "crash", 400, 6_000, "fixed", node=4)]
        sim = Simulator(cfg, seed=21, faults=faults)
        result = sim.run()
        self.assertGreater(sim.nodes[4].snapshots_installed, 0)
        self.assertEqual(result.violations, [])
        # and it really did catch up
        leader = next(n for n in sim.nodes if n.role is Role.LEADER)
        self.assertGreater(sim.nodes[4].last_index, leader.last_index - 30)

    def test_compaction_survives_a_restart(self):
        cfg = dataclasses.replace(QUIET, duration_ms=6_000)
        faults = [Fault(0, "crash", 3_000, 3_400, "fixed", node=2)]
        sim = Simulator(cfg, seed=22, faults=faults)
        result = sim.run()
        node = sim.nodes[2]
        self.assertGreater(node.snapshot_index, 0)
        self.assertEqual(node.sm.digest(), sim.nodes[0].sm.digest())
        self.assertEqual(result.violations, [])

    def test_snapshot_carries_the_client_sessions(self):
        node = RaftNode(0, (0, 1, 2), RaftConfig(), seed=1)
        from raftlab.raft.types import Command

        node.sm.apply(Command(7, 3, "append", "k", ("a",)))
        snap = node.sm.snapshot()
        other = RaftNode(1, (0, 1, 2), RaftConfig(), seed=1)
        other.sm.restore(snap)
        # the retried request must not be applied a second time
        other.sm.apply(Command(7, 3, "append", "k", ("a",)))
        self.assertEqual(other.sm.data["k"], "a")


class TestMembership(unittest.TestCase):
    """Section 4.1: configuration changes."""

    def test_config_takes_effect_when_appended(self):
        from raftlab.raft.types import LogEntry, config_command

        node = RaftNode(0, (0, 1, 2, 3, 4), RaftConfig(), seed=1, members=(0, 1, 2))
        self.assertEqual(node.majority(), 2)
        node.on_appended(LogEntry(1, 1, config_command("add", 3, 1)))
        self.assertIn(3, node.members)
        self.assertEqual(node.majority(), 3)
        node.on_appended(LogEntry(1, 2, config_command("remove", 0, 2)))
        self.assertFalse(node.is_member())

    def test_a_node_outside_the_configuration_does_not_campaign(self):
        node = RaftNode(3, (0, 1, 2, 3, 4), RaftConfig(), seed=1, members=(0, 1, 2))
        node.on_tick(10_000)
        self.assertIs(node.role, Role.FOLLOWER)
        self.assertEqual(node.current_term, 0)

    def test_cluster_survives_membership_churn(self):
        for seed in range(1, 11):
            run = run_and_check(PROFILES["churn"], seed)
            self.assertTrue(run.ok, f"seed {seed}: {run.first}")
        run = run_and_check(PROFILES["churn"], 6)
        self.assertGreater(run.sim.stats["config_changes"], 0)


class TestReadPaths(unittest.TestCase):
    """Section 6.4: the three ways to answer a read."""

    def modes(self, mode):
        base = PROFILES["normal"]
        return dataclasses.replace(base, raft=dataclasses.replace(base.raft, read_mode=mode))

    def test_read_index_is_linearizable(self):
        for seed in range(1, 11):
            run = run_and_check(self.modes("read_index"), seed)
            self.assertTrue(run.ok, f"seed {seed}: {run.first}")

    def test_read_index_keeps_reads_out_of_the_log(self):
        through_log = run_and_check(self.modes("log"), 5)
        read_index = run_and_check(self.modes("read_index"), 5)
        self.assertGreater(read_index.sim.stats["reads_local"], 0)
        self.assertLess(read_index.sim.stats["committed"], through_log.sim.stats["committed"])

    def test_a_fresh_leader_does_not_answer_from_a_stale_commit_index(self):
        node = RaftNode(0, (0, 1, 2), RaftConfig(read_mode="read_index"), seed=1)
        node.role = Role.LEADER
        node.term_start_index = 12
        node.commit_index = 4
        self.assertEqual(node.read_barrier(), 12)
