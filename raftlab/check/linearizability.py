"""Linearizability checking for the recorded client history.

Raft's internal invariants can all hold while the *service* still returns a
stale or impossible value -- a bad read path, a duplicate apply, a lost session
reply.  The only end-to-end correctness statement for a KV store is
linearizability: there exists a total order of the operations, consistent with
real time, in which every operation returns what a single-threaded store would.

Deciding that is NP-complete in general, so this uses the two things that make
it practical:

  * **Locality** (Herlihy & Wing 1990): a history is linearizable iff each
    per-object subhistory is.  Keys are independent here, so one huge search
    becomes several small ones.
  * **Wing & Gong search with Lowe's optimisations** (the algorithm behind
    Porcupine/Knossos): depth-first over "which operation goes next", with a
    doubly linked list to lift/unlift candidates in O(1) and a memo table on
    (set of linearized ops, model state) to cut the exponential re-exploration.

Operations that never returned (the client gave up or the run ended) are treated
as pending: they may be linearized at any point after they were invoked, with an
unconstrained return value -- exactly the freedom a real client has when a
request times out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..kv.statemachine import apply_pure
from .history import History, Op

CALL, RET = 1, 0  # sort order at equal timestamps: returns first
INF = 1 << 62


@dataclass
class LinResult:
    ok: bool
    unknown: bool = False
    key: str | None = None
    detail: str = ""
    steps: int = 0
    witness: list[Op] = field(default_factory=list)

    def __str__(self) -> str:
        if self.ok:
            return f"linearizable ({self.steps} search steps)"
        if self.unknown:
            return f"UNKNOWN: search budget exhausted on key {self.key} ({self.steps} steps)"
        return f"NOT LINEARIZABLE on key {self.key}: {self.detail}"


def check_key(ops: list[Op], max_steps: int = 2_000_000) -> LinResult:
    """Wing & Gong search over one key's subhistory."""
    if not ops:
        return LinResult(True)

    # ---- build the event list: (time, kind, op position) ----
    events: list[tuple[int, int, int]] = []
    for i, o in enumerate(ops):
        events.append((o.invoke, CALL, i))
        events.append((o.ret if o.completed and o.ret is not None else INF, RET, i))
    events.sort(key=lambda e: (e[0], e[1]))

    n = len(events)
    head, tail = n, n + 1
    kind = [0] * (n + 2)
    opix = [0] * (n + 2)
    match = [-1] * (n + 2)  # call entry -> its return entry
    nxt = [0] * (n + 2)
    prv = [0] * (n + 2)

    ret_pos: dict[int, int] = {}
    for pos, (_, k, i) in enumerate(events):
        kind[pos], opix[pos] = k, i
        if k == RET:
            ret_pos[i] = pos
    for pos, (_, k, i) in enumerate(events):
        if k == CALL:
            match[pos] = ret_pos[i]

    order = list(range(n))
    prev = head
    for pos in order:
        nxt[prev] = pos
        prv[pos] = prev
        prev = pos
    nxt[prev] = tail
    prv[tail] = prev

    def lift(call: int) -> None:
        ret = match[call]
        nxt[prv[call]] = nxt[call]
        prv[nxt[call]] = prv[call]
        nxt[prv[ret]] = nxt[ret]
        prv[nxt[ret]] = prv[ret]

    def unlift(call: int) -> None:
        ret = match[call]
        nxt[prv[ret]] = ret
        prv[nxt[ret]] = ret
        nxt[prv[call]] = call
        prv[nxt[call]] = call

    # ---- depth first search ----
    state: Any = None
    linearized = 0  # bitset of op positions
    cache: set = set()
    stack: list[tuple[int, Any, int]] = []
    steps = 0
    best_depth = -1
    best_remaining: list[int] = []

    entry = nxt[head]
    while nxt[head] != tail:
        if steps > max_steps:
            return LinResult(False, unknown=True, key=ops[0].key, steps=steps)
        steps += 1
        # kind[tail] is RET, so reaching the tail falls through to backtracking
        if entry != tail and kind[entry] == CALL:
            o = ops[opix[entry]]
            new_state, result = apply_pure(state, o.op, o.args)
            allowed = (not o.completed) or result == o.result
            if allowed:
                bit = 1 << opix[entry]
                key_ = (linearized | bit, new_state)
                if key_ not in cache:
                    cache.add(key_)
                    stack.append((entry, state, linearized))
                    linearized |= bit
                    state = new_state
                    lift(entry)
                    depth = len(stack)
                    if depth > best_depth:
                        best_depth = depth
                        best_remaining = _remaining(nxt, head, tail, kind, opix)
                    entry = nxt[head]
                    continue
            entry = nxt[entry]
            continue

        # A return whose call is still pending in the list: nothing further may
        # be linearized, so undo the most recent choice and try the next one.
        if not stack:
            witness = [ops[i] for i in best_remaining][:12]
            return LinResult(
                False,
                key=ops[0].key,
                detail=(
                    f"{len(ops)} operations, no valid order exists"
                    f" (deepest prefix: {max(best_depth, 0)} of {len(ops)})"
                ),
                steps=steps,
                witness=witness,
            )
        entry, state, linearized = stack.pop()
        unlift(entry)
        entry = nxt[entry]

    return LinResult(True, steps=steps)


def _remaining(nxt, head, tail, kind, opix) -> list[int]:
    out, cur = [], nxt[head]
    while cur != tail:
        if kind[cur] == CALL:
            out.append(opix[cur])
        cur = nxt[cur]
    return out


def check_history(history: History, max_steps: int = 2_000_000) -> LinResult:
    total_steps = 0
    for _key, ops in sorted(history.by_key().items()):
        r = check_key(ops, max_steps)
        total_steps += r.steps
        if not r.ok:
            r.steps = total_steps
            return r
    return LinResult(True, steps=total_steps)
