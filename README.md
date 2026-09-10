# raftlab

**A distributed consensus implementation, and the testing harness that proves it works.**

*[Türkçe sürüm](README.tr.md) · [Full results](RESULTS.md) · [How it works](docs/ARCHITECTURE.md) · [Engineering log](docs/ENGINEERING-LOG.md) · [Report](https://burakgggg.github.io/raftlab/)*

[![CI](https://github.com/BurakGGGG/raftlab/actions/workflows/ci.yml/badge.svg)](https://github.com/BurakGGGG/raftlab/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)
![Tests: 56](https://img.shields.io/badge/tests-56-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)

```
$ python3 -m raftlab run --seed 12 --profile churn --timeline

  node 0 |.....c....xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.......cccc...                                   |
  node 1 |.cLLxxxxxxxxxxxxx.................ccccccc..ccccccLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLL|
  node 2 |.....cLLLLLLLLLLLLLLLLLLLLLLLLLxxxxxxxx....ccccc.......ccc......................................|
  node 3 |                                                          ..xxxxxxxxxx..........................|
  node 4 |                    ..............cccccccxxxxxxxxxxxxxxxxxxxxxxxxxxxxx..........................|
  commit |                     ....::::::----------------------------------------=====+++++*****###%%%%%%@|  0..198
         |0         1         2        3         4         5        6         7        8         9        |  seconds

  L leader   c candidate   . follower   x crashed   ~ paused   (blank) outside the cluster
```

Ten seconds in the life of a five-node cluster: leaders elected and killed, a
long stretch where nothing can be agreed on, servers joining and leaving, and
the moment the cluster recovers and the committed log takes off again. The whole
run is reproducible from the number `12`.

---

## What this is, in plain language

**Consensus** is how a group of machines agrees on an ordered list of
instructions even though some of them are down, slow, or cut off from the
others. It is what keeps a database consistent when a server dies mid-write.
[Raft](https://raft.github.io/raft.pdf) is the algorithm most modern systems use
for it — etcd (and therefore Kubernetes), Consul, CockroachDB, TiKV.

Writing Raft is a weekend of work. **Knowing it is correct is the hard part**,
and that is what this project is really about.

The bugs that matter need a network partition to open *between two specific
messages*, or a leader to die in the microsecond after it wrote an entry and
before it copied it anywhere. Running a real cluster and hoping to get lucky
does not find those, and on the rare occasion it does, you cannot reproduce what
you saw.

So this project makes the entire world deterministic: **one thread, one virtual
clock, one seeded random number stream.** Message delays, packet loss, crashes,
GC pauses, clock drift, disk durability — all simulated, all replayable from a
single integer. That means:

* a failing run is a **number**, not a war story;
* simulated time is nearly free, so thousands of possible worlds get searched
  in minutes instead of one world being watched for hours;
* the whole cluster lives in one process, so a checker can inspect **every node
  after every single event** — impossible in a real distributed system;
* and a failure can be **shrunk automatically** to a minimal reproduction.

This is the approach behind [FoundationDB](https://www.youtube.com/watch?v=4fFDFbi3toc),
TigerBeetle and Antithesis. Everything here is written from scratch in pure
Python, with no dependencies.

## The headline results

| | |
| --- | --- |
| Correct implementation | clean across **82,600 simulated clusters** — including 5,000 randomly configured worlds |
| Deliberately planted bugs | **13 of 13 caught**, two of which no internal check can see |
| Real bugs found in my own code | **5**, each fixed and then measured |
| Throughput | **~700× real time** — 184 hours of cluster life in 16 minutes of wall clock |
| Checks | 10 safety invariants after *every* event, plus linearizability and liveness |

Full tables, commands and analysis: **[RESULTS.md](RESULTS.md)**.

## Try it in thirty seconds

```bash
git clone https://github.com/BurakGGGG/raftlab && cd raftlab
python3 -m raftlab run --seed 12 --profile churn --timeline   # watch one cluster live its life
python3 -m unittest discover -s tests -q                      # 56 tests, ~12s
python3 -m raftlab fuzz --bug blind_truncate --seeds 20       # plant a bug, watch it get caught
python3 -m raftlab shrink --seed 6 --bug commit_index_unclamped
```

The last command takes a failure that needed 21 injected faults and eight
seconds, and reduces it to a reproduction with **no faults at all and 627
milliseconds** — the bug was there all along, hiding behind ordinary message
timing.

## What the harness found

A test harness that has never caught anything is an untested claim. Thirteen
deliberate bugs — each one a mistake that has shipped in a real Raft
implementation — are compiled in one at a time, and the campaign measures how
many seeds each survives. More interesting are the **five bugs it found in code
I believed was correct**:

| what broke | how it showed up | the fix, measured |
| --- | --- | --- |
| Retransmission amplification | 183,560 messages to replicate 209 entries | one batch in flight per peer → 3,579 messages, 1.8× faster campaigns |
| Reads answered from a stale commit index | 2.2% of runs returned stale data | the §6.4 barrier → 0 in 6,000 runs |
| Read starvation under load | healthy cluster, 21 reads waiting forever | track acknowledgements per round → no stalls, 15% lower latency |
| A new cluster member never replicated to | 1% of runs wedged permanently | initialise replication state on config change → 0 in 400 runs |
| Slow recovery without pre-vote | 8.4% of runs served nothing for over a second | pre-vote → median recovery 227ms → 52ms |

And the finding I am most pleased with is one about **my own results**. I first
wrote up pre-vote as preventing a permanent loss of availability. Then I made
the liveness check confirm itself — a run that looks stalled gets replayed with
a longer tail — and the claim collapsed: nothing had ever been permanently down,
it had just been slow. The binary claim became a measured distribution, which is
both honest and more useful. That story is in
[the engineering log](docs/ENGINEERING-LOG.md).

## How it works

```
   seed ─▶ the world ─▶ the cluster ─▶ the oracle ─▶ repro.json
           latency        5 nodes,      10 invariants   minimal,
           loss, dup      pure state    linearizability  replayable
           partitions     machines      liveness              │
           crashes,           ▲                               │
           GC pauses          └───────── shrink: delete a fault, replay ─┘
           clock skew
```

* **The protocol** (`raftlab/raft/`) — leader election, log replication, crash
  recovery, log compaction with snapshots, cluster membership changes, three
  different read paths, and client sessions for exactly-once semantics. The node
  is a **pure state machine**: no clock, no sockets, no threads. Every entry
  point takes the current time and returns the messages to send, which is what
  makes the rest possible.
* **The world** (`raftlab/sim/`) — a single-threaded event loop over a virtual
  millisecond clock, with per-link latency profiles, packet loss and
  duplication, partitions evaluated at delivery time, process crashes, GC
  pauses, suspended VMs whose clocks stop, and an operator that adds and removes
  servers while the cluster runs.
* **The oracle** (`raftlab/check/`) — ten safety invariants checked
  incrementally after every event; a **linearizability checker** (the Wing &
  Gong search used by Porcupine and Knossos) that verifies the *service* was
  correct end to end, not just its internals; and a liveness check that confirms
  its own verdict before reporting one.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains all of it properly,
including what a "linearizability checker" is and why one is needed.

## What this project demonstrates

* **Testing things that are hard to test.** Deterministic simulation, fault
  injection, property-based checking, automatic test-case reduction.
* **Reading a specification and implementing it faithfully** — the Raft paper
  and dissertation, including the parts people skip (§4.1 membership changes,
  §6.4 read paths, §7 log compaction, §9.6 pre-vote).
* **Measuring instead of asserting.** Every claim in this repository is a number
  produced by a command that anyone can re-run.
* **Correcting myself in public.** Three false positives in my own checker and
  one over-claim in my own results are documented rather than quietly deleted.
* **Making a system legible** — a 20,000-line event trace collapsed into one
  picture, and a failing seed reduced to something a human can read.

## Layout

```
raftlab/
  raft/       the protocol: types.py, node.py, bugs.py (injectable faults)
  kv/         the replicated key/value store, with client sessions and snapshots
  sim/        the world: cluster.py (event loop), network.py, faults.py, client.py
  check/      the oracle: invariants.py, linearizability.py, history.py
  runner.py   one fully checked run          campaign.py  parallel seed sweeps
  shrink.py   delta-debugging to a minimal repro
  timeline.py the picture at the top of this file
tests/        56 tests: protocol, checker, determinism, and meta-tests that
              verify the harness still catches every planted bug
docs/         architecture, engineering log, and the published report
```

## Commands

```
python3 -m raftlab run       --seed N [--profile normal|hostile|slow|churn] [--timeline] [--trace]
python3 -m raftlab fuzz      --seeds 1:5000 [--bug B] [--profile P|swarm]
python3 -m raftlab campaign  --seeds 500      # correct build + every planted bug, four worlds
python3 -m raftlab recovery  --seeds 1000     # how fast the cluster comes back after a heal
python3 -m raftlab reads     --seeds 3000     # the three read paths, compared
python3 -m raftlab coverage  --seeds 400      # what the fuzzer actually reaches
python3 -m raftlab shrink    --seed N --bug B [--save repro.json] [--timeline]
python3 -m raftlab replay    repro.json
```

`make test`, `make demo`, `make campaign`, `make reads`, `make coverage` wrap the
common ones.

## Limitations

Stated plainly, because a project that claims no limits is not being honest:

* Membership changes are single-server add/remove; joint consensus and the
  §4.2.1 catch-up phase are not implemented.
* This tests the *protocol*, not an RPC stack, a real disk, or an operating
  system. A simulator can only find the bugs its model can express.
* The linearizability search is exponential in the worst case. It is bounded by
  a step budget and reports `UNKNOWN` rather than claiming a pass.
* The `hostile` and `slow` worlds can violate Raft's own timing assumption; the
  liveness checker reports that as a fact about the world, not about the code.

## License

MIT — see [LICENSE](LICENSE).
