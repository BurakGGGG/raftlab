"""Meta-tests: does the test harness actually catch bugs?

A simulator that has never caught anything is an untested claim.  Each case
below compiles the implementation with one deliberate protocol bug and asserts
that the checker finds it within a seed budget -- and that the budget is small
enough to be a regression test rather than a research project.
"""

import dataclasses
import os
import unittest

from raftlab.raft.bugs import Bugs
from raftlab.runner import run_and_check
from raftlab.sim.config import PROFILES

SLOW = os.environ.get("RAFTLAB_SLOW") == "1"


def first_catch(bug: str, budget: int, profile: str = "normal", **over):
    """Search seeds in order until the bug is caught.  The budgets in the tests
    below are the measured first-catch seed with headroom, so a regression that
    makes a bug harder to find shows up as a test failure."""
    from raftlab.sim.config import swarm

    for seed in range(1, budget + 1):
        cfg = swarm(seed) if profile == "swarm" else PROFILES[profile]
        if over:
            cfg = dataclasses.replace(cfg, raft=dataclasses.replace(cfg.raft, **over))
        run = run_and_check(cfg, seed, Bugs.of(bug))
        if not run.ok:
            return seed, run.first
    return None, None


class TestBugsAreCaught(unittest.TestCase):
    def check(self, bug, budget, expect, profile="normal", **over):
        seed, violation = first_catch(bug, budget, profile, **over)
        self.assertIsNotNone(seed, f"{bug} survived {budget} seeds on {profile}")
        expected = expect if isinstance(expect, tuple) else (expect,)
        self.assertIn(violation.kind, expected, f"{bug} -> {violation}")

    def test_blind_truncate(self):
        self.check("blind_truncate", 5, "TruncatedCommitted")

    def test_apply_before_commit(self):
        self.check("apply_before_commit", 10, "StateMachineSafety")

    def test_commit_index_unclamped(self):
        self.check("commit_index_unclamped", 5, "CommitBeyondLog")

    def test_no_log_up_to_date_check(self):
        self.check("no_log_up_to_date_check", 30, "LeaderCompleteness")

    def test_no_prev_log_check(self):
        # skipping the term half of the consistency check shows up either as two
        # logs disagreeing or as two state machines applying different commands
        self.check("no_prev_log_check", 30, ("LogMatching", "StateMachineSafety"))

    def test_no_session_dedup_is_invisible_to_the_invariants(self):
        """Every Raft invariant holds; only the end-to-end checker sees it."""
        seed, violation = first_catch("no_session_dedup", 20)
        self.assertIsNotNone(seed, "no_session_dedup survived 20 seeds")
        self.assertEqual(violation.kind, "Linearizability")
        run = run_and_check(PROFILES["normal"], seed, Bugs.of("no_session_dedup"))
        self.assertEqual(run.sim.violations, [], "an invariant fired after all")

    def test_accept_stale_terms(self):
        # a stale leader's commit index is the first thing to give way, but a
        # deleted committed entry is an equally valid symptom
        self.check("accept_stale_terms", 30, ("CommitBeyondLog", "TruncatedCommitted"))

    @unittest.skipUnless(SLOW, "rare: set RAFTLAB_SLOW=1")
    def test_no_persist_vote(self):
        # measured: first caught at seed 38 of the swarm profile
        self.check("no_persist_vote", 120, "VoteSafety", profile="swarm")

    @unittest.skipUnless(SLOW, "very rare: set RAFTLAB_SLOW=1")
    def test_figure8_commit(self):
        # measured: first caught at seed 1033 of the hostile profile, and only
        # with the election no-op disabled -- that no-op is what masks it
        self.check(
            "figure8_commit", 1200, "LeaderCompleteness",
            profile="hostile", noop_on_elect=False,
        )


class TestCorrectBuildIsClean(unittest.TestCase):
    def test_no_false_positives(self):
        budget = 200 if SLOW else 40
        for profile in ("normal", "hostile", "slow"):
            for seed in range(1, budget + 1):
                run = run_and_check(PROFILES[profile], seed)
                self.assertTrue(run.ok, f"{profile} seed {seed}: {run.first}")

    def test_histories_are_linearizable(self):
        for seed in range(1, 11):
            run = run_and_check(PROFILES["hostile"], seed)
            self.assertTrue(run.lin.ok, f"seed {seed}: {run.lin}")


class TestShrinking(unittest.TestCase):
    def failing_seed(self, bug, budget=20):
        seed, _ = first_catch(bug, budget)
        self.assertIsNotNone(seed, f"no failing seed for {bug} within {budget}")
        return seed

    def test_shrink_keeps_the_bug_and_removes_the_noise(self):
        from raftlab.shrink import shrink

        rep = shrink(PROFILES["normal"], self.failing_seed("blind_truncate"), Bugs.of("blind_truncate"))
        self.assertIsNotNone(rep)
        self.assertEqual(rep.kind, "TruncatedCommitted")
        self.assertLess(len(rep.faults), rep.original_faults + 1)
        self.assertLess(rep.cfg.duration_ms, rep.original_ms)
        again = rep.run(trace=False)
        self.assertEqual(again.first.kind, rep.kind)

    def test_repro_survives_a_json_round_trip(self):
        from raftlab.shrink import Repro, shrink

        bug = "commit_index_unclamped"
        rep = shrink(PROFILES["normal"], self.failing_seed(bug), Bugs.of(bug))
        self.assertIsNotNone(rep)
        back = Repro.from_json(rep.to_json())
        self.assertEqual(back.run(trace=False).first.kind, rep.kind)


if __name__ == "__main__":
    unittest.main()


class TestLivenessCheck(unittest.TestCase):
    """The liveness check confirms a failure by replaying with a longer tail, so
    it must still fire for a cluster that is genuinely dead."""

    def test_a_permanently_split_cluster_is_reported(self):
        from raftlab.sim.faults import Fault

        cfg = PROFILES["normal"]
        forever = [Fault(0, "partition", 400, 10**9, "fixed", ((0, 1), (2, 3), (4,)))]
        run = run_and_check(cfg, 3, faults=forever)
        self.assertFalse(run.ok)
        self.assertEqual(run.first.kind, "NoProgress")

    def test_a_slow_recovery_is_not_reported_as_an_outage(self):
        """A cluster that comes back late is late, not broken."""
        for seed in range(1, 40):
            run = run_and_check(PROFILES["hostile"], seed)
            self.assertNotEqual(
                getattr(run.first, "kind", ""), "NoProgress", f"seed {seed}"
            )
