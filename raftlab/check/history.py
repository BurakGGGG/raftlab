"""Recorded client history: the input to the linearizability checker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Op:
    id: int
    client: int
    key: str
    op: str
    args: tuple[Any, ...]
    invoke: int  # simulated time the client issued the request
    ret: int | None = None  # simulated time the reply reached the client
    result: Any = None
    completed: bool = False  # False => the client never learned the outcome
    attempts: int = 1

    def label(self) -> str:
        arg = ",".join(repr(a) for a in self.args)
        res = repr(self.result) if self.completed else "?"
        end = self.ret if self.ret is not None else "..."
        return f"c{self.client} {self.op}({self.key}{',' + arg if arg else ''}) -> {res}  [{self.invoke}..{end}]"


@dataclass
class History:
    ops: list[Op] = field(default_factory=list)

    def completed(self) -> list[Op]:
        return [o for o in self.ops if o.completed]

    def by_key(self) -> dict:
        out: dict = {}
        for o in self.ops:
            out.setdefault(o.key, []).append(o)
        return out

    def latencies(self, kind: str = "any") -> list[int]:
        out = []
        for o in self.ops:
            if not o.completed or o.ret is None:
                continue
            if kind == "read" and o.op != "get":
                continue
            if kind == "write" and o.op == "get":
                continue
            out.append(o.ret - o.invoke)
        out.sort()
        return out

    @staticmethod
    def pct(values: list[int], q: float) -> int:
        if not values:
            return 0
        return values[min(len(values) - 1, int(q * len(values)))]

    def stats(self) -> dict:
        done = self.completed()
        reads, writes = self.latencies("read"), self.latencies("write")
        return {
            "ops": len(self.ops),
            "completed": len(done),
            "pending": len(self.ops) - len(done),
            "retries": sum(o.attempts - 1 for o in self.ops),
            "reads": len(reads),
            "read_p50": self.pct(reads, 0.50),
            "read_p99": self.pct(reads, 0.99),
            "write_p50": self.pct(writes, 0.50),
            "write_p99": self.pct(writes, 0.99),
        }
