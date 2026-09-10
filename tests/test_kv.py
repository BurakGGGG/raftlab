import unittest

from raftlab.kv.statemachine import KVStateMachine, apply_pure
from raftlab.raft.types import Command


class TestStateMachine(unittest.TestCase):
    def test_ops(self):
        self.assertEqual(apply_pure(None, "get", ()), (None, None))
        self.assertEqual(apply_pure(None, "put", ("a",)), ("a", None))
        self.assertEqual(apply_pure("a", "append", ("b",)), ("ab", None))
        self.assertEqual(apply_pure("a", "cas", ("a", "z")), ("z", True))
        self.assertEqual(apply_pure("a", "cas", ("q", "z")), ("a", False))

    def test_pure(self):
        """The checker relies on apply_pure having no hidden state."""
        for _ in range(3):
            self.assertEqual(apply_pure("x", "append", ("y",)), ("xy", None))

    def test_session_dedup(self):
        sm = KVStateMachine()
        sm.apply(Command(1, 1, "append", "k", ("a",)))
        sm.apply(Command(1, 1, "append", "k", ("a",)))  # retried
        self.assertEqual(sm.data["k"], "a")
        sm.apply(Command(1, 2, "append", "k", ("b",)))
        self.assertEqual(sm.data["k"], "ab")

    def test_session_returns_cached_result(self):
        sm = KVStateMachine()
        self.assertIs(sm.apply(Command(1, 1, "cas", "k", (None, "v"))), True)
        self.assertIs(sm.apply(Command(1, 1, "cas", "k", (None, "v"))), True)

    def test_keys_are_independent(self):
        sm = KVStateMachine()
        sm.apply(Command(1, 1, "put", "a", ("1",)))
        sm.apply(Command(1, 2, "put", "b", ("2",)))
        self.assertEqual(sm.digest(), (("a", "1"), ("b", "2")))


if __name__ == "__main__":
    unittest.main()
