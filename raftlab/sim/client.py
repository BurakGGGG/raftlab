"""Closed-loop clients that retry until they get an answer.

Retries are what make a KV store's history interesting: the same logical
operation may reach the log twice, so the store needs client sessions to stay
linearizable.  A client records one *logical* operation (first attempt to final
reply) in the history; retries are invisible to the checker, exactly as they are
invisible to a real application.
"""

from __future__ import annotations

import random

from ..check.history import History, Op
from ..raft.types import CLIENT_BASE, ClientRequest, Command, Envelope
from .config import SimConfig

OP_MIX = (("put", 35), ("get", 30), ("cas", 20), ("append", 15))


class ClientSim:
    def __init__(self, cid: int, cfg: SimConfig, rng: random.Random, history: History) -> None:
        self.cid = cid
        self.addr = CLIENT_BASE + cid
        self.cfg = cfg
        self.rng = rng
        self.history = history
        self.seq = 0
        self.attempt = 0
        self.current: Op | None = None
        self.target = rng.randrange(cfg.n_nodes)
        self.known: dict[str, str | None] = {}
        self.ops_started = 0

    # ------------------------------------------------------------------
    def _choose(self) -> tuple[str, str, tuple]:
        key = f"k{self.rng.randrange(self.cfg.n_keys)}"
        r = self.rng.randrange(sum(w for _, w in OP_MIX))
        op = OP_MIX[-1][0]
        acc = 0
        for name, w in OP_MIX:
            acc += w
            if r < acc:
                op = name
                break
        token = f"{self.cid}-{self.seq}"
        if op == "put":
            return op, key, (token,)
        if op == "append":
            return op, key, (token[-1],)
        if op == "cas":
            old = self.known.get(key)
            if self.rng.random() < 0.3:
                old = f"{self.rng.randrange(self.cfg.n_clients)}-{self.rng.randrange(20)}"
            return op, key, (old, token)
        return op, key, ()

    def begin(self, now: int) -> Envelope:
        op, key, args = self._choose()
        self.seq += 1
        self.attempt += 1
        self.ops_started += 1
        self.current = Op(
            id=len(self.history.ops),
            client=self.cid,
            key=key,
            op=op,
            args=args,
            invoke=now,
        )
        self.history.ops.append(self.current)
        return self._send()

    def _send(self) -> Envelope:
        assert self.current is not None, "no operation in flight"
        cmd = Command(self.cid, self.seq, self.current.op, self.current.key, self.current.args)
        return Envelope(self.addr, self.target, ClientRequest(cmd))

    def retry(self, now: int, hint: int | None = None) -> Envelope:
        """Same seq -> the cluster must not apply the operation twice."""
        assert self.current is not None
        self.attempt += 1
        self.current.attempts += 1
        if hint is not None and 0 <= hint < self.cfg.n_nodes:
            self.target = hint
        else:
            self.target = self.rng.randrange(self.cfg.n_nodes)
        return self._send()

    def on_reply(self, now: int, reply) -> Envelope | None:
        if self.current is None or reply.seq != self.seq:
            return None  # stale reply for an operation we already resolved
        if not reply.ok:
            return self.retry(now, reply.leader_hint)
        self.current.ret = now
        self.current.result = reply.value
        self.current.completed = True
        if self.current.op == "get":
            self.known[self.current.key] = reply.value
        elif self.current.op == "put":
            self.known[self.current.key] = self.current.args[0]
        elif self.current.op == "cas" and reply.value is True:
            self.known[self.current.key] = self.current.args[1]
        else:
            self.known.pop(self.current.key, None)
        self.current = None
        return None
