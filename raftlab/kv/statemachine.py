"""The replicated state machine: a key/value store with get/put/cas/append.

Two properties matter for the rest of the project:

1.  ``apply_pure`` is a *pure* function of (value, op) for a single key.  The
    linearizability checker reuses it as its model, so the checker can never
    drift from the implementation.
2.  Keys are independent, which lets the checker exploit the locality theorem
    (Herlihy & Wing 1990): a history is linearizable iff every per-key
    subhistory is.  That turns one intractable search into many small ones.
"""

from __future__ import annotations

from typing import Any

from ..raft.types import Command

Value = str | None


def apply_pure(value: Value, op: str, args: tuple[Any, ...]) -> tuple[Value, Any]:
    """Apply one operation to one key's value.  Returns (new_value, result)."""
    if op == "get":
        return value, value
    if op == "put":
        return args[0], None
    if op == "append":
        return (value or "") + args[0], None
    if op == "cas":
        old, new = args
        if value == old:
            return new, True
        return value, False
    raise ValueError(f"unknown op {op!r}")


class KVStateMachine:
    """Deterministic state machine with client sessions for exactly-once apply."""

    __slots__ = ("data", "sessions", "dedup")

    def __init__(self, dedup: bool = True) -> None:
        self.dedup = dedup
        self.data: dict[str, str] = {}
        # client -> (last seq applied, cached result)
        self.sessions: dict[int, tuple[int, Any]] = {}

    def apply(self, cmd: Command) -> Any:
        if cmd.is_noop() or cmd.op == "config":
            # Membership changes travel through the same log but are consensus
            # state, not application state.
            return None
        last = self.sessions.get(cmd.client)
        if self.dedup and last is not None and cmd.seq <= last[0]:
            # Duplicate delivery of a retried request: return the cached reply
            # instead of applying the command a second time.
            return last[1]
        value = self.data.get(cmd.key)
        new_value, result = apply_pure(value, cmd.op, cmd.args)
        if new_value is None:
            self.data.pop(cmd.key, None)
        else:
            self.data[cmd.key] = new_value
        self.sessions[cmd.client] = (cmd.seq, result)
        return result

    def digest(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self.data.items()))

    # -- snapshotting (section 7) --------------------------------------
    def snapshot(self) -> tuple[Any, ...]:
        """An immutable copy of everything the log would otherwise have to
        replay: the data *and* the client sessions, because forgetting the
        sessions would let a retried request apply twice after a restore."""
        return (
            tuple(sorted(self.data.items())),
            tuple(sorted((c, s, r) for c, (s, r) in self.sessions.items())),
        )

    def restore(self, snap: tuple[Any, ...]) -> None:
        data, sessions = snap
        self.data = dict(data)
        self.sessions = {c: (s, r) for c, s, r in sessions}
