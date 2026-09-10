"""The premise of the whole project: a run is a pure function of its seed."""

import hashlib
import subprocess
import sys
import textwrap
import unittest

from raftlab.runner import run_and_check
from raftlab.sim.config import PROFILES

DIGEST_SNIPPET = textwrap.dedent(
    """
    import hashlib, sys
    sys.path.insert(0, ".")
    from raftlab.runner import run_and_check
    from raftlab.sim.config import PROFILES
    r = run_and_check(PROFILES["hostile"], 4242)
    print(hashlib.sha256(digest(r).encode()).hexdigest())
    """
)


def digest(run) -> str:
    parts = [repr(sorted(run.sim.stats.items()))]
    parts += [o.label() for o in run.sim.history.ops]
    parts += [str(v) for v in run.failures]
    parts += [",".join(str(e) for e in n_log) for n_log in run.sim.trace]
    return "|".join(parts)


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_run(self):
        a = run_and_check(PROFILES["hostile"], 99, trace=True)
        b = run_and_check(PROFILES["hostile"], 99, trace=True)
        self.assertEqual(digest(a), digest(b))
        self.assertEqual(a.sim.trace, b.sim.trace)

    def test_different_seeds_differ(self):
        a = run_and_check(PROFILES["hostile"], 99)
        b = run_and_check(PROFILES["hostile"], 100)
        self.assertNotEqual(digest(a), digest(b))

    def test_reproducible_in_a_fresh_process(self):
        """Cross-process, so nothing may depend on address-sensitive hashing."""
        script = "import hashlib,sys\n" + \
            "sys.path.insert(0,'.')\n" + \
            "from raftlab.runner import run_and_check\n" + \
            "from raftlab.sim.config import PROFILES\n" + \
            "from tests.test_determinism import digest\n" + \
            "r=run_and_check(PROFILES['hostile'],4242,trace=True)\n" + \
            "print(hashlib.sha256(digest(r).encode()).hexdigest())\n"
        outs = []
        for _ in range(2):
            out = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, check=True
            )
            outs.append(out.stdout.strip())
        local = hashlib.sha256(
            digest(run_and_check(PROFILES["hostile"], 4242, trace=True)).encode()
        ).hexdigest()
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], local)

    def test_explicit_fault_list_reproduces_the_generated_one(self):
        """Replaying a saved schedule must give the same run as generating it."""
        from raftlab.sim.cluster import substream
        from raftlab.sim.faults import generate

        cfg = PROFILES["normal"]
        faults = generate(cfg, substream(77, "faults"))
        a = run_and_check(cfg, 77)
        b = run_and_check(cfg, 77, faults=faults)
        self.assertEqual(digest(a), digest(b))


if __name__ == "__main__":
    unittest.main()
