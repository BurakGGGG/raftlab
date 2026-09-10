# Engineering log

The interesting parts of building this: what broke, how it was found, and what
the fix was worth. Roughly chronological.

Each entry follows the same shape, because that is how the work actually went:
**symptom → investigation → root cause → fix → measurement.**

---

## 1. The checker cried wolf three times before it caught anything

The first version of the safety checker reported violations on the *correct*
implementation. Every one was a bug in the checker, and every one taught
something about the property it was supposed to be enforcing.

**"A node holds a different entry at a committed index."** True, and perfectly
legal: a leader that was cut into a minority keeps accepting client writes into
its own log. Those entries are not committed *there*, and they get truncated
when it rejoins. The sound invariant is narrower — a node must never delete an
entry **it has itself committed** — which the node records at the moment it
truncates, because by the time the checker looks, the commit index has moved on.

**"A node became leader without a committed entry."** Also legal. A candidate
that was paused for 600ms woke up, found a quorum of stale vote replies, and
won an election for a term that had long since passed. It is harmless: it can
commit nothing, because every peer is now on a higher term. Leader completeness
applies to leaders of terms *above* the term an entry was committed in, and the
checker was ignoring that qualifier.

**"No client operation completed after the network healed."** Sometimes true,
sometimes just impatience — see entry 8, which is the most instructive failure
in this whole document.

**Lesson.** An oracle that reports things that are not violations is worse than
no oracle, because it trains you to ignore it. Writing down the *exact*
statement of each property — with its qualifiers — is the work.

## 2. Two bugs the oracle could not see, and the fault injector was not to blame

Three planted bugs survived every seed. The instinct is to inject harder:
correlated fault bursts, leader-targeted chaos, more seeds. That bought roughly
a 4× improvement on the hardest bug and nothing at all on the other two.

The actual problem was that the checker was watching for *consequences*:

* `commit_index_unclamped` — a follower that adopts the leader's commit index
  verbatim should eventually apply an entry that gets rolled back. It never did,
  because a conflicting append truncates the log all the way to the end, so a
  divergent tail never survives long enough to be applied. One correct mechanism
  was masking another's bug. But the *cause* is one comparison:
  `commitIndex > lastLogIndex` is impossible in correct Raft.
* `no_persist_vote` — a node that forgets its vote across a restart. Waiting for
  the textbook consequence means waiting for *two* candidates to each assemble a
  majority: a coincidence on top of a coincidence. Watching for the double vote
  itself is just as sound.

| bug | detection before | detection after |
| --- | --- | --- |
| `commit_index_unclamped` | 0 in 6,000 runs | ~9 runs in 10 |
| `no_persist_vote` | 0 in 6,000 runs | first catch at seed 38 |

**Lesson.** The sensitivity of the oracle matters as much as the reach of the
fault injector. Two four-line invariants beat every scheduling trick I tried.

## 3. 183,560 messages to replicate 209 entries

Some seeds in the randomly-configured `swarm` world ran for over a second of
wall clock and allocated hundreds of megabytes. Nothing was *incorrect* — no
invariant fired — the cluster was simply spending all of its bandwidth talking
to itself.

The leader answered every `AppendEntriesResp` with another `AppendEntries`. That
looks like healthy pipelining, and it is, until a packet is duplicated: then one
request/response chain becomes two, both permanent. At a 5% duplication rate the
chains multiply geometrically until the link saturates.

The fix is the window that real implementations already keep (etcd calls it
`Progress.Inflights`): at most one batch in flight per peer, ship more only once
the peer has acknowledged what is already on the wire, and let the heartbeat —
not a reflex to every packet — recover losses.

```
              messages / entries   worst run
before        183,560 / 209          1.12 s
after           3,579 / 242          0.13 s
```

Whole-campaign throughput went from 524× to 955× real time, which is the same
finding measured from the other end.

**Lesson.** Simulation catches resource bugs as readily as correctness bugs, and
a bug that only shows up under duplication is one a real cluster would hit at 3am.

## 4. A leader answering reads from a stale commit index

Serving reads through the log is correct and expensive: every read is replicated
like a write. Raft §6.4 offers two cheaper paths, and I implemented both. The
harness then reported stale reads in 2.2% of `read_index` runs.

Confirming leadership with a heartbeat round is only half the rule. A leader
that has just been elected inherits whatever commit index it happened to have as
a *follower*, and that can lag entries a previous leader really did commit. The
missing half is the barrier: **a leader may not answer a read until it has
committed an entry of its own term** — which is exactly what the no-op appended
on election is for.

With the barrier: 0 stale reads in 6,000 runs.

**Lesson.** "I implemented the optimisation from the paper" and "I implemented
the optimisation from the paper *correctly*" are different claims, and only one
of them is testable.

## 5. Read starvation with a perfectly healthy cluster

One seed in three thousand hung. The logs were identical on all five nodes, the
leader was stable, the network was quiet — and the leader was sitting on 21 read
requests it never answered.

Each read opened a fresh heartbeat round and reset the acknowledgement counter.
Under a steady read load, a read arriving before the previous round completed
discarded that round's replies, so no round ever reached a majority and every
read waited forever.

Tracking acknowledgements **per round** instead of keeping a single "current
round" fixed it, and dropped read latency 15% as a side effect.

**Lesson.** The bug was not in the protocol; it was in the bookkeeping around
it. A liveness property that only fails under sustained load is exactly what a
long-running randomized campaign is for.

## 6. A leader that never learned where a new member's log began

On the membership-churn world, 1% of runs wedged permanently. The cluster looked
fine: a stable leader, a healthy quorum, and two members that stayed completely
empty forever while counting towards the majority.

The leader had added a server to the configuration but never initialised
`next_index` for it. Its rejections were then treated as stale replies to a
probe that had never been sent, so the leader ignored them — forever.

Raft §4.1 says a leader initialises replication state for a server the moment
the configuration adds it. It says so for exactly this reason.

**Lesson.** The bug was one missing line in a function I had written five
minutes earlier. Reading the specification again *after* implementing is worth
more than reading it twice before.

## 7. Figure 8 is masked by a detail real implementations already have

The famous figure 8 of the Raft paper shows a committed entry being overwritten
when a leader commits an old-term entry on a majority count alone. Planting that
bug produced **no violations at all**.

The reason is a correct detail: a leader appends a no-op on election, and
committing that no-op commits the previous term's tail legitimately, closing
most of the window. With the no-op disabled the bug becomes reachable and stays
genuinely rare — 11 hits in 5,000 seeds, first at seed 1,033.

**Lesson.** "We never hit this bug" can mean "our code is right", or it can mean
"another part of our code is hiding it". Only an experiment distinguishes them.

## 8. The finding I got wrong

This is the one worth reading.

Without pre-vote (§9.6), the liveness check fired on about 1% of hostile runs:
after the network healed, no client operation completed in the quiet window. The
trace showed the classic pattern — two nodes taking turns unseating each other,
terms climbing, nothing committing. I implemented pre-vote and leader
stickiness, measured 0 failures in 3,000 runs, and wrote it up:

> Without pre-vote, one hostile world in seventy never recovers.

That claim was wrong. "No operation completed inside the window I chose" is not
the same statement as "the cluster is down", and I had quietly conflated them.

So the liveness check now **confirms itself**: a run that looks stalled is
replayed with the tail doubled, and only a cluster that is *still* silent is
reported. With that in place, every configuration recovers — pre-vote or not.
Nothing had ever been permanently down.

The honest measurement is a distribution, and it turned out to be more useful
than the claim it replaced:

| build | median | p90 | p99 | worst | over 1s |
| --- | ---: | ---: | ---: | ---: | ---: |
| hostile, no pre-vote | 227ms | 942ms | 2266ms | 3580ms | 8.4% |
| hostile, pre-vote | 52ms | 311ms | 592ms | 688ms | 0.0% |
| slow, no pre-vote | 439ms | 1195ms | 2641ms | 8647ms | 14.5% |
| slow, pre-vote | 230ms | 618ms | 1173ms | 1631ms | 1.5% |

Pre-vote does not save the cluster from an outage that was never going to
happen. What it does is cut median recovery four-fold and remove the
multi-second tail — the difference between a blip and a page.

**Lesson.** The most dangerous measurement is the one that agrees with what you
expected. A test harness should be able to check its own verdicts, and the
verdict that most needed checking was mine.

## 9. What the coverage report found

Every run records which interesting situations it reached. One category —
"a node restarted while an election was in progress" — sat at 0%, which would
have meant a whole class of scenarios was going untested.

It was not the fault schedule. The instrumentation call had landed in the wrong
place during an edit, and the coverage report was the only thing that could have
noticed.

**Lesson.** Measure what your tests actually exercise, not what you assume they
exercise. It is cheap and it is occasionally humbling.

---

## What I would do next

* **Joint consensus** (§4.6) so configurations can change by more than one
  server at a time, and the §4.2.1 catch-up phase so adding a slow server does
  not cost availability.
* **A faster core.** The event loop is pure Python; a run costs ~50ms. Ten times
  that throughput is ten times the seeds for the same electricity.
* **Coverage-guided seed selection** — prefer seeds that reach situations the
  campaign has not seen, instead of sampling uniformly.
* **A second implementation to test against.** The checker validates behaviour
  against the specification; running a real implementation's log through the same
  oracle would validate the oracle.
