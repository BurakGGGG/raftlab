import unittest

from raftlab.check.history import History, Op
from raftlab.check.linearizability import check_history, check_key


def op(i, client, name, args, invoke, ret, result, completed=True, key="k"):
    return Op(i, client, key, name, args, invoke, ret, result, completed)


class TestLinearizability(unittest.TestCase):
    def test_sequential_ok(self):
        self.assertTrue(check_key([
            op(0, 0, "put", ("a",), 0, 10, None),
            op(1, 0, "get", (), 20, 30, "a"),
        ]).ok)

    def test_stale_read_rejected(self):
        r = check_key([
            op(0, 0, "put", ("a",), 0, 10, None),
            op(1, 0, "get", (), 20, 30, None),
        ])
        self.assertFalse(r.ok)
        self.assertFalse(r.unknown)

    def test_concurrent_read_may_go_either_way(self):
        self.assertTrue(check_key([
            op(0, 0, "put", ("a",), 0, 50, None),
            op(1, 1, "get", (), 10, 30, None),
        ]).ok)
        self.assertTrue(check_key([
            op(0, 0, "put", ("a",), 0, 50, None),
            op(1, 1, "get", (), 10, 30, "a"),
        ]).ok)

    def test_impossible_value_rejected(self):
        self.assertFalse(check_key([
            op(0, 0, "put", ("a",), 0, 10, None),
            op(1, 1, "put", ("b",), 0, 10, None),
            op(2, 2, "get", (), 20, 30, "c"),
        ]).ok)

    def test_duplicate_apply_is_caught(self):
        """Exactly the anomaly client sessions exist to prevent."""
        self.assertFalse(check_key([
            op(0, 0, "append", ("x",), 0, 10, None),
            op(1, 0, "get", (), 20, 30, "xx"),
        ]).ok)

    def test_lost_update_is_caught(self):
        self.assertFalse(check_key([
            op(0, 0, "append", ("x",), 0, 10, None),
            op(1, 0, "append", ("y",), 20, 30, None),
            op(2, 0, "get", (), 40, 50, "x"),
        ]).ok)

    def test_cas_ordering(self):
        # both CAS operations claim success on the same old value: impossible
        self.assertFalse(check_key([
            op(0, 0, "put", ("a",), 0, 10, None),
            op(1, 1, "cas", ("a", "b"), 20, 40, True),
            op(2, 2, "cas", ("a", "c"), 20, 40, True),
            op(3, 3, "get", (), 50, 60, "b"),
        ]).ok)

    def test_pending_operation_is_free(self):
        self.assertTrue(check_key([
            op(0, 0, "put", ("a",), 0, None, None, completed=False),
            op(1, 1, "get", (), 20, 30, None),
        ]).ok)

    def test_pending_write_may_also_be_observed(self):
        self.assertTrue(check_key([
            op(0, 0, "put", ("a",), 0, None, None, completed=False),
            op(1, 1, "get", (), 20, 30, "a"),
        ]).ok)

    def test_random_sequential_history_is_linearizable(self):
        hist = History()
        t = 0
        for i in range(200):
            key = f"k{i % 3}"
            hist.ops.append(op(i, 0, "put", (str(i),), t, t + 1, None, key=key))
            t += 2
        self.assertTrue(check_history(hist).ok)

    def test_keys_checked_independently(self):
        hist = History()
        hist.ops.append(op(0, 0, "put", ("a",), 0, 10, None, key="x"))
        hist.ops.append(op(1, 0, "get", (), 20, 30, None, key="y"))  # different key: fine
        self.assertTrue(check_history(hist).ok)


if __name__ == "__main__":
    unittest.main()
