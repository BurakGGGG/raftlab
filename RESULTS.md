# Results

Everything below is reproducible from a clean checkout: each section names the
command that produced it. Numbers come from a 12-core desktop machine.

## The campaign

`python3 -m raftlab campaign --seeds 500`

The correct build, plus the same implementation compiled with one deliberate
protocol bug at a time, across four worlds. `caught` counts seeds in which any
check fired; `ops/run` and `elect` are there to show the runs were doing real
work rather than failing to start.

```
configuration              profile   seeds caught   rate  first  ops/run  elect  detected as
--------------------------------------------------------------------------------------------
correct                    normal      500      0   0.0%      -      214    3.8  (clean)
figure8_commit*            normal      500      0   0.0%      -      216    3.8  (clean)
no_persist_vote            normal      500      1   0.2%     99      214    3.8  VoteSafety x1
no_log_up_to_date_check    normal      500    117  23.4%      1      179    3.7  LeaderCompleteness x113, CommittedEntryChanged x3, NoProgress x1
blind_truncate             normal      500    500 100.0%      1        2    1.1  TruncatedCommitted x488, CommitBeyondLog x12
no_prev_log_check          normal      500     95  19.0%      3      187    3.7  LogMatching x91, StateMachineSafety x4
commit_index_unclamped     normal      500    452  90.4%      2       71    2.8  CommitBeyondLog x452
apply_before_commit        normal      500    500 100.0%      1       35    1.5  SnapshotBeyondCommit x452, StateMachineSafety x48
accept_stale_terms         normal      500    148  29.6%     14      169    3.5  CommitBeyondLog x83, TruncatedCommitted x55, LeaderAppendOnly x9, LeaderCompleteness x1
snapshot_accepts_stale     normal      500    176  35.2%      2      181    3.6  CommitBeyondLog x176
snapshot_forgets_sessions  normal      500      0   0.0%      -      215    3.8  (clean)
config_on_commit           normal      500      0   0.0%      -      214    3.8  (clean)
config_concurrent          normal      500      0   0.0%      -      214    3.8  (clean)
no_session_dedup           normal      500    173  34.6%      1      169    3.8  Linearizability x171, NoProgress x2
correct                    hostile     500      0   0.0%      -      109    4.8  (clean)
figure8_commit*            hostile     500      0   0.0%      -      111    4.8  (clean)
no_persist_vote            hostile     500      2   0.4%    392      109    4.8  VoteSafety x2
no_log_up_to_date_check    hostile     500    184  36.8%      9       83    4.4  LeaderCompleteness x181, StateMachineSafety x2, CommittedEntryChanged x1
blind_truncate             hostile     500    500 100.0%      1        2    1.2  TruncatedCommitted x480, CommitBeyondLog x19, LeaderCompleteness x1
no_prev_log_check          hostile     500    224  44.8%      9       77    4.3  LogMatching x220, StateMachineSafety x4
commit_index_unclamped     hostile     500    406  81.2%      1       44    3.5  CommitBeyondLog x406
apply_before_commit        hostile     500    500 100.0%      1       29    2.0  SnapshotBeyondCommit x309, StateMachineSafety x191
accept_stale_terms         hostile     500    246  49.2%      1       71    3.9  TruncatedCommitted x109, CommitBeyondLog x108, LeaderAppendOnly x26, LeaderCompleteness x3
snapshot_accepts_stale     hostile     500     52  10.4%      3      105    4.8  CommitBeyondLog x52
snapshot_forgets_sessions  hostile     500      1   0.2%    228      109    4.8  Linearizability x1
config_on_commit           hostile     500      0   0.0%      -      109    4.8  (clean)
config_concurrent          hostile     500      0   0.0%      -      109    4.8  (clean)
no_session_dedup           hostile     500    195  39.0%      3       81    4.9  Linearizability x190, NoProgress x5
correct                    slow        500      0   0.0%      -      129    3.1  (clean)
figure8_commit*            slow        500      0   0.0%      -      129    3.1  (clean)
no_persist_vote            slow        500      5   1.0%    221      128    3.1  VoteSafety x5
no_log_up_to_date_check    slow        500    103  20.6%      2      107    3.1  LeaderCompleteness x103
blind_truncate             slow        500    500 100.0%      1        1    1.4  TruncatedCommitted x484, CommitBeyondLog x14, LeaderCompleteness x2
no_prev_log_check          slow        500    126  25.2%      1      102    3.1  LogMatching x125, StateMachineSafety x1
commit_index_unclamped     slow        500    350  70.0%      1       56    2.8  CommitBeyondLog x350
apply_before_commit        slow        500    500 100.0%      1       33    1.7  SnapshotBeyondCommit x398, StateMachineSafety x102
accept_stale_terms         slow        500     98  19.6%      1      106    2.9  TruncatedCommitted x54, CommitBeyondLog x21, LeaderAppendOnly x20, LeaderCompleteness x3
snapshot_accepts_stale     slow        500    151  30.2%      3      112    3.1  CommitBeyondLog x151
snapshot_forgets_sessions  slow        500      0   0.0%      -      129    3.1  (clean)
config_on_commit           slow        500      0   0.0%      -      129    3.1  (clean)
config_concurrent          slow        500      0   0.0%      -      129    3.1  (clean)
no_session_dedup           slow        500    117  23.4%      5      107    3.1  Linearizability x117
correct                    churn       500      0   0.0%      -      222    4.1  (clean)
figure8_commit*            churn       500      0   0.0%      -      222    4.1  (clean)
no_persist_vote            churn       500      0   0.0%      -      222    4.1  (clean)
no_log_up_to_date_check    churn       500    175  35.0%      7      173    3.9  LeaderCompleteness x172, ElectionSafety x2, CommittedEntryChanged x1
blind_truncate             churn       500    500 100.0%      1        3    1.1  TruncatedCommitted x483, CommitBeyondLog x16, LeaderCompleteness x1
no_prev_log_check          churn       500    110  22.0%      4      188    3.9  LogMatching x107, StateMachineSafety x3
commit_index_unclamped     churn       500    481  96.2%      1       45    2.0  CommitBeyondLog x481
apply_before_commit        churn       500    500 100.0%      1       35    1.4  SnapshotBeyondCommit x459, StateMachineSafety x41
accept_stale_terms         churn       500    143  28.6%      6      176    3.7  CommitBeyondLog x63, TruncatedCommitted x60, LeaderAppendOnly x20
snapshot_accepts_stale     churn       500    108  21.6%      6      200    4.0  CommitBeyondLog x108
snapshot_forgets_sessions  churn       500      1   0.2%     43      221    4.1  Linearizability x1
config_on_commit           churn       500      5   1.0%      2      218    4.1  LeaderCompleteness x3, ElectionSafety x2
config_concurrent          churn       500      5   1.0%     27      221    4.2  LeaderCompleteness x4, StateMachineSafety x1
no_session_dedup           churn       500    211  42.2%      1      178    4.1  Linearizability x211
--------------------------------------------------------------------------------------------
56 configurations, 28000 runs, 260,272,088 events, 3179.8 simulated minutes in 287.1s wall (665x real time)

* figure8_commit is run with noop_on_elect=False (see the code comment).
```

The correct build is clean on all 2,000 runs. Membership bugs only show up in
the `churn` world, because only there does the configuration ever change — which
is the argument for having more than one world.


## Nothing is ever permanently down

`python3 -m raftlab ablation --seeds 1000`

The same three builds, now judged by the confirmed liveness check:

```
configuration            profile   seeds caught   rate  first  ops/run  elect  detected as
------------------------------------------------------------------------------------------
correct/plain            normal     1000      0   0.0%      -      201    4.3  (clean)
correct/pre-vote         normal     1000      0   0.0%      -      213    3.9  (clean)
correct/pre-vote+sticky  normal     1000      0   0.0%      -      214    3.8  (clean)
correct/plain            hostile    1000      0   0.0%      -       88    5.2  (clean)
correct/pre-vote         hostile    1000      0   0.0%      -      108    5.0  (clean)
correct/pre-vote+sticky  hostile    1000      0   0.0%      -      108    4.8  (clean)
correct/plain            slow       1000      0   0.0%      -      114    3.8  (clean)
correct/pre-vote         slow       1000      0   0.0%      -      128    3.2  (clean)
correct/pre-vote+sticky  slow       1000      0   0.0%      -      127    3.1  (clean)
------------------------------------------------------------------------------------------
9 configurations, 9000 runs, 99,179,452 events, 1200.0 simulated minutes in 112.7s wall (639x real time)
```

Zero in 9,000 runs, pre-vote or not. This table exists to be honest about what
the recovery-time table above replaced.


## Section 6.4, measured: three ways to answer a read

`python3 -m raftlab reads --seeds 3000`

The same seeds, the same faults, the same world -- only the read path changes.
Reads through the log replicate every read like a write. **ReadIndex** confirms
leadership with one heartbeat round and answers from the leader's own state
machine. A **leader lease** answers with no round trip at all, for as long as
the leader believes its lease holds.

A lease assumes clocks have bounded error. In this fault model a pause can also
*stop the clock* -- a suspended VM -- and a leader that wakes up believes almost
no time has passed:

```
read path      seeds  stale reads  read p50  read p99  log/run  msgs/op
-----------------------------------------------------------------------
through log     3000     0 (0.0%)       71m     2616m      115     22.1
read-index      3000     0 (0.0%)       77m     2923m       80     21.4
lease           3000     4 (0.1%)       30m     2372m       89     18.9

configuration     profile   seeds caught   rate  first  ops/run  elect  detected as
-----------------------------------------------------------------------------------
reads/log         hostile    3000      0   0.0%      -      108    4.9  (clean)
reads/read_index  hostile    3000      0   0.0%      -      106    5.0  (clean)
reads/lease       hostile    3000      4   0.1%   1347      118    4.9  Linearizability x4
-----------------------------------------------------------------------------------
3 configurations, 9000 runs, 96,114,794 events, 1200.0 simulated minutes in 99.8s wall (722x real time)
  lease: seed 1347: Linearizability: 36 operations, no valid order exists (deepest prefix: 10 of 36) [key k2]
  lease: seed 1353: Linearizability: 40 operations, no valid order exists (deepest prefix: 27 of 40) [key k1]
  lease: seed 1969: Linearizability: 44 operations, no valid order exists (deepest prefix: 20 of 44) [key k0]
```

With clock freezes turned off (`--freeze 0.0`) the lease is sound in every run:

```
read path      seeds  stale reads  read p50  read p99  log/run  msgs/op
-----------------------------------------------------------------------
through log     3000     0 (0.0%)       71m     2621m      116     22.1
read-index      3000     0 (0.0%)       76m     2918m       80     21.5
lease           3000     0 (0.0%)       29m     2463m       89     18.8

configuration     profile   seeds caught   rate  first  ops/run  elect  detected as
-----------------------------------------------------------------------------------
reads/log         hostile    3000      0   0.0%      -      109    4.9  (clean)
reads/read_index  hostile    3000      0   0.0%      -      106    5.0  (clean)
reads/lease       hostile    3000      0   0.0%      -      119    4.9  (clean)
-----------------------------------------------------------------------------------
3 configurations, 9000 runs, 96,145,216 events, 1200.0 simulated minutes in 98.9s wall (728x real time)
```

So this is not a proof that leases are wrong; it is a measurement of the
assumption they rest on, and of what happens when it fails. Note also what the
table does *not* show: ReadIndex is not faster than a log write here (both cost
one round trip). Its win is a log 30% smaller -- less disk, less replication,
less to snapshot.


## What the fuzzer actually reaches

`python3 -m raftlab coverage --seeds 400`

A campaign that never produces a snapshot install has not tested
InstallSnapshot, however many seeds it burned. Every run records which
interesting situations it reached:

```
situation reached                   runs    share
-------------------------------------------------
client retried more than once       1600   100.0%  ############################
client op took over a second        1599    99.9%  ###########################
snapshot taken                      1599    99.9%  ###########################
leadership changed hands            1581    98.8%  ###########################
leader crashed                      1481    92.6%  #########################
restart during election             1445    90.3%  #########################
leader cut into a minority          1337    83.6%  #######################
crash during election               1266    79.1%  ######################
snapshot installed                  1228    76.8%  #####################
leader paused                       1097    68.6%  ###################
clock frozen                         993    62.1%  #################
log truncated                        960    60.0%  ################
two nodes believe they lead          898    56.1%  ###############
stale leader in partition            762    47.6%  #############
config changed                       396    24.8%  ######
-------------------------------------------------
1600 runs
```

This table has already earned its place once: `restart during election` sat at
0% for a while, which turned out to be a misplaced instrumentation call rather
than a property of the fault schedule.


## The rare ones

`python3 -m raftlab fuzz --bug B --profile P --seeds 5000`

Three bugs need thousands of seeds. `swarm` draws a different world for every
seed -- timeouts, batch size, loss and duplication rates, client count, key
count, fault mix -- which is swarm testing (Groce et al., ISSTA 2012).

```
configuration   profile   seeds caught   rate  first  ops/run  elect  detected as
none           swarm      5000      0   0.0%      -      223    4.3  (clean)
figure8_commit  hostile    5000     11   0.2%   1033      109    4.9  LeaderCompleteness x11
no_persist_vote  swarm      5000     28   0.6%     38      222    4.3  VoteSafety x28
snapshot_forgets_sessions  normal     5000      6   0.1%   1643      214    3.8  Linearizability x6
```

The first row matters most: **5,000 randomly configured worlds, and the correct
implementation is clean in every one.** A checker that reports violations under
adverse timing is only useful if it stays silent when the code is right.


## Cost

| stage | runs | events | simulated | wall |
| --- | ---: | ---: | ---: | ---: |
| campaign (56 configurations) | 28,000 | 260.3 M | 53.0 h | 287 s |
| recovery-time distribution | 6,000 | -- | 20.0 h | 118 s |
| liveness ablation | 9,000 | 99.2 M | 20.0 h | 110 s |
| read paths, twice | 18,000 | 192.3 M | 40.0 h | 195 s |
| coverage | 1,600 | 19.8 M | 4.0 h | 22 s |
| four deep sweeps | 20,000 | 253.6 M | 47.2 h | 220 s |
| **total** | **82,600** | **825 M+** | **184 h** | **~16 min** |

Roughly **700x real time** on twelve cores, with ten invariants checked against
every event in that column.


---

## What the harness found in this implementation

Five real bugs, none of them planned. Each was found by running an
implementation I believed was correct across thousands of seeds, and each was
fixed and then *measured* rather than assumed.

### 1. Raft-as-written recovers slowly after a partition heals — and my first
measurement of it was wrong

Every safety invariant held over thousands of seeds. Then the liveness check
started firing: on about 1% of hostile runs, no client request completed in the
quiet window after the network healed.

```
t=   6730  node 2: candidate -> leader   (term 21)
t=   6757  node 0: follower  -> candidate (term 22)
t=   6770  node 2: leader    -> follower  (term 22)
t=   6907  node 0: candidate -> leader    (term 22)
t=   6937  node 2: follower  -> candidate (term 23)
   ... terms climb, nobody stays leader, nothing commits
```

Two nodes take turns unseating each other: each election carries a higher term,
and a higher term forces a working leader to stand down. Nothing there is a
coding mistake — it is the algorithm as figure 2 describes it, and it is exactly
why the Raft dissertation adds **pre-vote** (§9.6) and **leader stickiness**
(§4.2.3). I implemented both, measured 0 failures, and wrote it up as "pre-vote
prevents a permanent loss of availability".

That claim was wrong, and the harness is what caught it. "No operation completed
inside the window I picked" is not the same as "the cluster is down"; a cluster
repairing a badly diverged log in an adverse network can legitimately need
longer than one election to come back. So the liveness check now **confirms**
itself: a run that looks stalled is replayed with the tail doubled, and only a
cluster that is *still* silent is reported. With that confirmation in place,
every configuration — pre-vote or not — recovers eventually. Nothing was ever
permanently down.

The real effect is a distribution, not a yes/no, and it is worth more than the
claim it replaced:

```
build                      runs   median     p90     p99    worst  over 1s  never
---------------------------------------------------------------------------------
hostile/plain              1000     227m    942m   2266m    3580m     8.4%      0
hostile/pre-vote           1000      52m    311m    592m     688m     0.0%      0
hostile/pre-vote+sticky    1000      56m    334m    564m     822m     0.0%      0
slow/plain                 1000     439m   1195m   2641m    8647m    14.5%      0
slow/pre-vote              1000     230m    618m   1173m    1631m     1.5%      0
slow/pre-vote+sticky       1000     243m    644m   1158m    1486m     1.4%      0
```

Pre-vote does not save the cluster from an outage that was never going to
happen. What it does is cut the median recovery several-fold and remove the
multi-second tail entirely — which is the difference between a blip and a page.

### 2. Unbounded retransmission amplification

Some seeds ran for over a second of wall clock and allocated hundreds of
megabytes: **183,000 messages to replicate 209 entries**, where the honest
number is about two thousand.

Answering every `AppendEntriesResp` with another `AppendEntries` looks like
healthy pipelining — until a packet is duplicated. Then one request/response
chain becomes two, both permanent, and at a 5% duplication rate the chains
multiply until the link saturates. The protocol stays correct throughout; the
cluster simply spends all its bandwidth talking to itself.

The fix is the window real implementations already keep (etcd calls it
`Progress.Inflights`): at most one batch in flight per peer, ship more only once
the peer has acknowledged what is already on the wire, and let the heartbeat —
not a reflex to every packet — recover losses.

```
              messages / entries   worst run
before        183,560 / 209          1.12 s
after           3,579 / 242          0.13 s
```

### 3. A leader that answered reads from a stale commit index

`ReadIndex` (§6.4) looked right and was not: 2.2% of runs served a stale read.
Confirming leadership with a heartbeat round is only half the rule. A leader
that has just been elected inherits whatever commit index it happened to have as
a follower, and that can lag entries a previous leader really did commit. The
missing half is the barrier: a leader may not answer a read until it has
committed an entry **of its own term** — which is what the no-op appended on
election is for. With the barrier, stale reads went to zero across 3,000 seeds.

### 4. Read starvation under a steady read load

After the barrier, one seed in three thousand still hung: the cluster was
healthy, all logs identical, and the leader was sitting on 21 read requests it
never answered.

Each read opened a fresh heartbeat round and reset the ack counter — so a read
arriving before the previous round was answered discarded that round's replies.
Under a steady read load no round ever reached a majority, and every read waited
forever while the cluster looked perfectly fine. Tracking acks *per round*
instead of keeping a single "current round" fixed it, and dropped read latency
by 15% as a side effect.

### 5. A leader that never learned where a new member's log began

On the churn profile, 1% of runs stalled permanently. The leader had added a
server to the configuration but never initialised `next_index` for it, so its
rejections were treated as stale replies to a probe that had never been sent.
The new member stayed empty forever while the majority requirement counted it —
and the cluster wedged.

Section 4.1 says a leader initialises replication state for a server the moment
the configuration adds it. It says so for exactly this reason.

---

## Three false positives in my own checker

An oracle that cries wolf is worse than no oracle. All three of these looked
like implementation bugs and were not:

* **A minority node holding a different entry at a committed index.** Perfectly
  legal: the entry is uncommitted *there*, and it will be truncated when the
  node rejoins. The sound invariant is narrower — a node must never delete an
  entry it has itself committed, which the node records at the moment it
  truncates.
* **A slow candidate that wakes up and wins a stale term.** Leader Completeness
  applies to leaders of terms *above* the term an entry was committed in. A
  paused candidate that resumes and collects old votes is harmless: it can
  commit nothing.
* **A liveness window measured in milliseconds.** 1.6 s is generous on a LAN and
  absurd in a wide-area cluster where one split vote costs more than a second.
  The window is now `6 × election timeout + 2 × client timeout`, and the
  assertion is skipped entirely when the run does not leave that much quiet time.

---

## Two bugs the oracle could not see, and one it is the only way to see

Three injected bugs initially survived every seed. The instinct is to blame the
fault injector and inject harder. That was wrong for two of them:

* `commit_index_unclamped` — a follower that takes the leader's commit index
  verbatim. Its *consequences* are masked by another, correct mechanism: because
  a conflicting append truncates the log all the way to the end, a divergent
  tail never survives long enough to be applied. Its *cause* is one comparison:
  `commitIndex > lastLogIndex` is impossible in correct Raft. Checking the cause
  moved detection from 0 in 6,000 runs to nine runs in ten.
* `no_persist_vote` — a node that forgets its vote across a restart. Waiting for
  two leaders to actually emerge means waiting for both candidates to assemble a
  majority. Watching for the double vote itself finds it hundreds of seeds
  sooner, and is just as sound an invariant.

**Lesson: the sensitivity of the oracle matters as much as the reach of the
fault injector.**

The mirror image is `no_session_dedup` and `snapshot_forgets_sessions`, which no
internal invariant can see at all. The log is replicated perfectly; it simply
contains a client's command twice, or a snapshot arrives without the client
sessions and a retry is applied a second time after the restore. Only the
end-to-end linearizability checker catches those — which is the entire argument
for having one.

---

## Figure 8 is masked by a detail real implementations already have

`figure8_commit` removes the rule that a leader may only commit entries from its
own term — the bug in figure 8 of the Raft paper, where a committed entry is
later overwritten. With the implementation as it ships, it produces *no*
violations at all, because a correct detail hides it: a leader appends a no-op
on election, and committing that no-op commits the previous term's tail
legitimately, closing most of the window.

With the no-op disabled the bug becomes reachable and stays genuinely rare (see
the sweep above) — a fair reflection of reality, since this class of bug has
shipped in production Raft implementations and survived for years.

---

## What a failure looks like

The fuzzer reports a seed. The shrinker deletes faults and replays until only
what matters is left. Sometimes what matters is nothing at all:

```
$ python3 -m raftlab shrink --seed 6 --bug commit_index_unclamped
repro: seed=6 bug=commit_index_unclamped nodes=5 clients=4 duration=627ms
violation: CommitBeyondLog: node 2 has commit index 9 but only 8 log entries
reduced from 21 faults / 8000ms to 0 faults / 627ms in 26 replays
faults:
  (none: the bug needs no faults at all)
```

Twenty-one faults and eight seconds turn out to be irrelevant: ordinary message
timing is enough. When faults *are* the point, the run is also drawn as a grid --
one column per time slice, one row per node -- next to the schedule that caused
it:

```
repro: seed=2 bug=config_on_commit nodes=5 clients=4 duration=10000ms
violation: ElectionSafety: nodes 0 and 2 are both leader in term 4
reduced from 8 faults / 10000ms to 6 faults / 10000ms in 27 replays
faults:
  [   574..  1662] crash the leader
  [  1110..  3324] partition 1,2 | 0,3,4
  [  3740..  7200] crash the leader
  [  5817..  6020] partition (isolate_leader)
  [  5891..  7200] pause the leader
  [  6970..  7200] crash the leader

timeline  seed=2  104ms per column  (10000ms total)

  node 0 |..............ccccccccccccccccccc......cccccLLLLLLLLLLLLL~~~~~~~~~~~~~..|
  node 1 |........ccLLLLLLLLLLLLLLLLLLLLLLL                                       |
  node 2 |..LLLLxxxxxxxxxx...................cxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.c|
  node 3 |                                              ............cLLLLLLLLLxx..|
  node 4 |                                             .............c..........ccc|
  commit |     ............:::::::::::::::::::-------------===+***#######%%%%@@@@@|  0..71
         |0         1         2        3         4         5        6         7   |  seconds

  L leader   c candidate   . follower   x crashed   ~ paused   (blank) outside the configuration
```

Node 1 leads the old configuration while node 0 campaigns in the new one; the
blanks are nodes outside the configuration entirely, and the commit sparkline
shows exactly where the cluster stopped agreeing. Two leaders in one term is
what section 4.1 warns about when a configuration is applied on commit instead
of on append.

