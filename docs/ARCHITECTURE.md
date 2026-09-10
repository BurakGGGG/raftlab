# How raftlab works

Written for someone who has not read the Raft paper. If you have, skip to
[The simulator](#the-simulator).

---

## 1. The problem being solved

Suppose five servers must agree on an ordered list of instructions — "set x=1",
"set y=2" — so that any of them can answer questions about the current state and
give the same answer. Now let servers crash, let the network drop and reorder
messages, let one half of the cluster be cut off from the other, and let clocks
disagree. Getting all five to agree anyway is the **consensus** problem.

**Raft** solves it by electing one server as *leader*. Clients talk to the
leader; the leader appends each instruction to its log and copies it to the
others; once a majority has stored an entry, it is *committed* and can never be
lost. If the leader dies, the survivors elect a new one, and the rules of the
election guarantee that whoever wins already has every committed entry.

Everything after that is detail — and the details are where the bugs live.

## 2. Why testing it is the hard part

A normal test suite runs the happy path and a handful of failures you thought
of. The failures that break consensus are the ones nobody thinks of: a partition
that opens between two specific messages, a leader that dies in the instant
after appending an entry and before replicating it, a duplicated packet that
arrives after a node has already moved on.

Three things make those bugs almost impossible to catch conventionally:

1. **They are rare.** They need a precise interleaving of independent events.
2. **They are not reproducible.** When one does happen, the timing that produced
   it is gone.
3. **They are invisible.** The cluster keeps running; the damage shows up later
   as a value that should not exist.

Deterministic simulation testing answers all three at once.

## 3. The core idea: make the world a function of a seed

Every source of nondeterminism is replaced by a decision made by a seeded
pseudo-random generator, and the whole cluster runs in **one thread on a virtual
clock**:

```python
node.on_message(now, envelope)  -> [messages to send]
node.on_tick(now)               -> [messages to send]
node.restart(now, durable)      -> volatile state destroyed
```

The node has no clock of its own, opens no sockets, and starts no threads. The
simulator owns time, the network and the disk; the node owns only the protocol.
That single design decision is what everything else rests on.

A run is then a pure function:

```
run(config, seed) -> (history, violations)
```

Same seed, same result — verified by a test that runs the same seed in two
separate processes and compares a hash of the entire event trace.

## 4. The simulator

`raftlab/sim/cluster.py` is a priority queue of events ordered by virtual time.
Popping an event may push more events; the loop runs until the clock passes the
end of the run. Eight simulated seconds of cluster life cost about 50
milliseconds of real time.

| what is modelled | how |
| --- | --- |
| message latency | per-link profiles — about 6% of links are 6–14× slower than the rest, and asymmetric |
| packet loss and duplication | per-message probability; duplicates arrive at a different time, so ordering breaks |
| reordering | falls out of per-message latency; nothing enforces FIFO |
| process crash | volatile state destroyed; only what was persisted survives |
| GC pause | the process stays alive but stops: timers freeze, the inbox floods in on resume |
| suspended VM | a pause that **also stops the clock**, so the node wakes up believing no time has passed |
| network partition | evaluated at *delivery* time, so messages already in flight are lost |
| clock skew | every node has its own epoch offset and its own tick rate (±2%) |
| disk durability | durable state is snapshotted after every event; a restart restores exactly that and nothing else |
| membership change | an operator adds and removes servers while the cluster is running |

Faults are generated **as data** before the run starts — a list of
`(kind, start, end, target)` records. That is what makes shrinking possible:
the shrinker deletes entries from a list and replays, rather than trying to
steer a random stream.

Some faults are targeted (`crash the leader`, `isolate the leader`, `put the
leader in a minority`) and some arrive in **correlated bursts**, because real
outages are not independent coin flips and consensus bugs cluster around
leadership changes.

Four fixed worlds ship with the project — `normal`, `hostile` (timers barely
separated, 6% loss), `slow` (wide-area latency), `churn` (membership keeps
moving) — plus `swarm`, where **every seed draws its own world**: timeouts,
batch sizes, loss rates, client count, fault mix. That last one is *swarm
testing* (Groce et al., ISSTA 2012), and it finds things no fixed profile does.

## 5. The oracle: three independent layers

A simulator only finds bugs if something is watching.

### Layer 1 — ten safety invariants, after every event

The five from the Raft paper (election safety, leader append-only, log matching,
leader completeness, state machine safety) plus five operational ones (term
monotonicity, committed-entry stability, vote safety, commit-index containment,
snapshot sanity).

All of them are **incremental**: each log entry is validated once, when it is
appended, using a rolling prefix hash, rather than rescanning every log on every
event. That is the difference between a full check costing 10⁵ events per run
and being unaffordable.

Two of the ten are not in the paper, and they are the two that matter most in
practice. They watch *causes* rather than *consequences* — a node casting two
different votes in one term, and a commit index pointing past the end of a log.
Both are impossible in correct Raft, both are four lines, and both moved bug
detection by orders of magnitude. See the engineering log for why.

### Layer 2 — linearizability, end to end

Every internal invariant can hold while the *service* still returns an
impossible value. **Linearizability** is the end-to-end correctness property:
there must exist some order of the client operations, consistent with real time,
in which each one returns what a single-threaded store would have returned.

Deciding that is NP-complete in general, so the checker uses the two things that
make it practical:

* **Locality** (Herlihy & Wing, 1990): a history is linearizable if and only if
  each per-key sub-history is. One intractable search becomes many small ones.
* **The Wing & Gong search with Lowe's optimisations** — the algorithm behind
  Porcupine and Knossos: depth-first over "which operation goes next", a doubly
  linked list to lift and unlift candidates in O(1), and a memo table on
  (set of linearized operations, model state) to cut the exponential re-explore.

Operations that never returned — the client timed out and gave up — are treated
as pending: they may be placed anywhere after they were invoked, with an
unconstrained return value, which is exactly the freedom a real client has.

The model the checker uses is the *same function* the state machine uses, so the
two can never drift apart.

### Layer 3 — liveness, with confirmation

Safety alone is not enough: a cluster that never loses data because it never
does anything is still broken. Every fault heals before the run ends, so the
cluster must serve clients again in the quiet tail.

The window is not a fixed number of milliseconds — it is
`6 × election timeout + 2 × client timeout`, so the same assertion means the same
thing on a LAN and in a wide-area cluster. And a run that looks stalled is
**replayed with the tail doubled** before anything is reported: a cluster
repairing a badly diverged log can legitimately need longer than one election,
and "it did not finish inside my window" is not the same claim as "it is down".

## 6. Proving the harness works: injectable bugs

Thirteen flags in `raftlab/raft/bugs.py` compile the implementation with one
deliberate protocol mistake each — every one of them a mistake that has shipped
in a real Raft implementation. The campaign measures how many seeds each bug
survives:

```
$ python3 -m raftlab campaign --seeds 500
```

Two of the thirteen are invisible to every internal invariant: dropping client
sessions, and dropping them again when restoring a snapshot. The log stays
perfectly replicated — it just contains a client's command twice. Only the
linearizability checker can see those, which is the entire argument for having
one.

## 7. From a seed to a bug report

A failing seed is not yet a bug report. The shrinker (`raftlab/shrink.py`) runs
delta debugging over the fault list — drop a fault, replay, keep the deletion if
the same violation still fires — then shortens the run and reduces the client
count, and repeats until nothing more can go.

What comes out is usually two or three faults and a few hundred milliseconds,
saved as a JSON file that replays identically on any machine. `raftlab timeline`
then draws the run as a grid so the shape of the failure is visible at a glance.

## 8. Design decisions worth knowing

* **The node returns messages instead of sending them.** Everything else follows
  from this. It is also why the same node code runs unchanged in a test, a fuzz
  campaign and a shrinker replay.
* **Separate RNG streams per subsystem** (network, faults, each client, each
  node). Changing the number of clients does not perturb the network's random
  choices, which keeps shrinking stable.
* **Persist-then-send is modelled by construction.** The simulator snapshots
  durable state at the end of each event, before any message it produced is
  delivered, so it is impossible to accidentally test a node that sends before
  it persists.
* **The checker never trusts the implementation.** Prefix hashes are computed by
  the checker from what it observes, not reported by the node.
* **Coverage is measured, not assumed.** Every run records which interesting
  situations it reached — snapshot installs, leader crashes during elections,
  two nodes believing they lead — and the campaign reports the distribution. It
  has already caught one instrumentation gap that made a whole category look
  untested.
