# Using this project on a CV

Copy-paste material, plus the answers to the questions an interviewer will
actually ask. Every number here is measured — see [RESULTS.md](../RESULTS.md)
for the command that produced it.

*Türkçe sürüm aşağıda: [Türkçe](#türkçe)*

---

## The one-liner

> **raftlab** — a from-scratch Raft consensus implementation and the
> deterministic simulation harness that tests it. Pure Python, no dependencies.
> `github.com/BurakGGGG/raftlab`

## CV bullets

### Short (two lines, when space is tight)

> **raftlab** — Raft consensus implementation + deterministic simulation testing
> harness (Python, 4.5k lines, no dependencies). 82,600 simulated clusters,
> 13/13 planted protocol bugs detected, 5 real defects found and fixed in my own
> code. `github.com/BurakGGGG/raftlab`

### Standard (a project entry with bullets)

> **raftlab** — distributed consensus and the testing that proves it
> *Personal project · Python 3, no dependencies · github.com/BurakGGGG/raftlab*
>
> * Implemented the Raft consensus algorithm from the paper — leader election,
>   log replication, crash recovery, log compaction, cluster membership changes
>   and three read paths — as a pure state machine with no I/O, so that entire
>   cluster lifetimes could be simulated deterministically.
> * Built a simulation harness that reproduces a five-node cluster's life from a
>   single integer seed: message loss, reordering, partitions, process crashes,
>   GC pauses, clock skew and suspended VMs. Runs at ~700× real time.
> * Designed a three-layer correctness oracle — 10 protocol invariants checked
>   after every event, an end-to-end **linearizability** checker (Wing & Gong
>   search with per-key partitioning), and a liveness check that confirms its own
>   verdict by replaying the failing run.
> * Validated the harness by compiling 13 deliberate protocol bugs modelled on
>   real-world Raft defects: **all 13 are detected**, two of them only by the
>   end-to-end checker.
> * Found and fixed **5 real defects in my own implementation**, including
>   unbounded retransmission amplification (183,560 → 3,579 messages for the same
>   work) and stale reads served by a newly elected leader; every fix verified by
>   measurement across thousands of seeds.
> * Automatic test-case reduction turns a failing seed into a minimal
>   reproduction — one case went from 21 injected faults over 8 seconds to zero
>   faults over 627ms.

### Detailed (for a portfolio page or a cover letter paragraph)

> I wanted to find out whether I could write a distributed consensus
> implementation *and know* it was correct, so I built both the implementation
> and the machinery that tests it. The testing approach — deterministic
> simulation, as used by FoundationDB and TigerBeetle — replaces every source of
> nondeterminism with a seeded random choice, so an entire cluster's life
> becomes a pure function of one integer. A failing run stops being a war story
> and becomes a number you can replay, shrink and put in a test suite.
>
> The part I am most pleased with is not the implementation. It is that the
> harness caught five real bugs in code I believed was correct, three false
> positives in my own checker, and one over-claim in my own write-up: I had
> reported that a missing optimisation caused permanent outages, and when I made
> the liveness check confirm its own verdicts, it turned out the clusters had
> only been slow, never down. The binary claim became a measured distribution.

## LinkedIn project entry

> **raftlab · Raft consensus + deterministic simulation testing**
>
> A from-scratch implementation of the Raft consensus algorithm (the algorithm
> behind etcd, Consul and CockroachDB) together with the test harness that
> proves it works: an entire five-node cluster's life — partitions, crashes,
> clock skew, packet loss — reproduced from a single random seed, with ten
> safety invariants and a linearizability checker watching every event.
>
> 82,600 simulated clusters. 13 of 13 deliberately planted protocol bugs
> detected. 5 real bugs found in my own code, each fixed and measured.
> Python 3, no dependencies, 56 tests.

---

## The 60-second spoken pitch

> Raft is the algorithm that keeps a group of servers agreeing on an ordered log
> — it is what etcd and therefore Kubernetes runs on. Writing it takes a
> weekend. Knowing it is correct is the hard part, because the bugs need a
> partition to open between two specific messages, or a leader to die right after
> it writes an entry and before it copies it.
>
> So I made the whole world deterministic. One thread, a virtual clock, one
> seeded random stream — every delay, every dropped packet, every crash comes out
> of that seed. Now a failing run is a number: I can replay it, and I can shrink
> it automatically down to the two faults that actually mattered.
>
> To prove the harness was worth anything, I compiled thirteen deliberate bugs
> into the implementation, each one a mistake that has shipped in a real Raft.
> All thirteen get caught. Two of them are invisible to every internal check —
> the log is replicated perfectly, it just contains a client's command twice — so
> those needed an end-to-end linearizability checker.
>
> And along the way it found five real bugs in my own code, plus one place where
> I had over-claimed in my own results. That last one is my favourite part.

## Questions you will get, and how to answer them

**"Walk me through the project."** Use the pitch above. Then offer the timeline
picture — it explains in five seconds what a paragraph cannot.

**"What was the hardest bug you found?"** The read starvation one (engineering
log §5). It is the best story because everything looked healthy: identical logs
on every node, a stable leader, a quiet network — and twenty-one client reads
that would never be answered. The cause was bookkeeping, not protocol: each new
read opened a fresh confirmation round and threw away the replies to the
previous one, so under a steady read load no round ever completed.

**"How do you test something you cannot reproduce?"** You make it reproducible
first. That is the whole idea: remove nondeterminism from the system under test
by owning time, the network and the disk, and then reintroduce it deliberately
from a seed you control.

**"How do you know your tests are any good?"** Because I measured it. Thirteen
deliberate bugs are compiled in one at a time and the campaign reports how many
seeds each survives — that is a test suite for the test suite. And a coverage
report records which interesting situations each run actually reached, which is
how I found that one whole category was silently never being exercised.

**"Tell me about a time you were wrong."** The pre-vote result (engineering log
§8). I measured a real effect, drew a stronger conclusion than the data
supported, and only caught it when I made the liveness check replay its own
failures before reporting them. The corrected result — a recovery-time
distribution instead of a yes/no — turned out to be more useful than the claim
it replaced.

**"What would you do differently?"** Write the invariants before the
implementation. Three of my first checker's reports were false positives, and
each one came from a property I had stated too strongly. Writing the exact
statement — with its qualifiers — is most of the work, and doing it first would
have caught the implementation bugs sooner.

**"Why Python? Isn't it slow?"** It is, and it does not matter here: simulated
time is decoupled from real time, so the constraint is seeds-per-minute, not
latency. A run costs about 50ms, which is 700× faster than the eight seconds of
cluster life it simulates. If I needed ten times the seeds I would port the
event loop, not the protocol.

**"What is the limitation of this approach?"** A simulator only finds the bugs
its model can express. There is no real RPC stack, no real disk, no operating
system here — a bug in serialization or an fsync that lies would go completely
unnoticed. Deterministic simulation replaces integration testing badly and
replaces reasoning about protocols very well.

## Numbers worth remembering

| | |
| --- | --- |
| 82,600 | simulated clusters in the final campaign |
| 825 M | events, each checked against 10 invariants |
| 184 hours | of simulated cluster life, in ~16 minutes of wall clock |
| ~700× | real time, on 12 cores |
| 13 / 13 | planted protocol bugs detected |
| 5 | real bugs found in my own implementation |
| 3 | false positives found in my own checker |
| 0 | violations in 5,000 randomly configured worlds |
| 56 | tests, ~12 seconds |
| 4,500 | lines of Python, zero dependencies |

## If you are asked about AI assistance

Answer plainly: say which parts you directed and which parts a tool wrote, and
be ready to explain any line of it. The commit history in this repository is
transparent about it. What a good interviewer is testing is whether you
understand the system, and that question is answered by the engineering log —
the debugging, the false positives, the corrected claim — not by who typed which
line. Know that material and the question stops being a threat.

---

# Türkçe

## Tek cümle

> **raftlab** — sıfırdan yazılmış bir Raft konsensüs implementasyonu ve onu
> sınayan deterministik simülasyon altyapısı. Saf Python, bağımlılık yok.
> `github.com/BurakGGGG/raftlab`

## CV maddeleri

> **raftlab** — dağıtık konsensüs ve onu kanıtlayan test altyapısı
> *Kişisel proje · Python 3, bağımlılıksız · github.com/BurakGGGG/raftlab*
>
> * Raft konsensüs algoritmasını makaleden yola çıkarak sıfırdan implemente
>   ettim: lider seçimi, log replikasyonu, çökme kurtarma, log sıkıştırma,
>   üyelik değişiklikleri ve üç farklı okuma yolu. Düğüm, hiç I/O yapmayan saf
>   bir durum makinesi olarak tasarlandı; küme ömrünün deterministik olarak
>   simüle edilebilmesini sağlayan şey bu.
> * Beş düğümlü bir kümenin tüm yaşamını tek bir tamsayı tohumdan yeniden üreten
>   simülasyon altyapısı kurdum: paket kaybı, yeniden sıralama, ağ bölünmeleri,
>   süreç çökmeleri, GC duraklamaları, saat kayması ve askıya alınmış sanal
>   makineler. Gerçek zamanın ~700 katı hızda çalışıyor.
> * Üç katmanlı bir doğruluk denetçisi tasarladım: her olaydan sonra denetlenen
>   10 protokol değişmezi, uçtan uca **linearizability** denetleyicisi (anahtar
>   bazlı bölümlemeli Wing & Gong araması) ve kendi kararını yeniden oynatarak
>   doğrulayan bir canlılık denetimi.
> * Altyapının işe yaradığını kanıtlamak için gerçek dünyada görülmüş 13 protokol
>   hatasını kasıtlı olarak koda gömdüm: **13'ünün 13'ü de yakalanıyor**, ikisi
>   yalnızca uçtan uca denetleyiciyle.
> * Kendi implementasyonumda **5 gerçek hata** bulup düzelttim; bunlardan biri
>   aynı iş için 183.560 mesaj üreten sınırsız yeniden iletim çoğalmasıydı
>   (3.579'a indi). Her düzeltme binlerce tohum üzerinde ölçülerek doğrulandı.
> * Otomatik test küçültme, başarısız bir tohumu minimum tekrar-üretime
>   indiriyor: bir vaka 8 saniyede 21 arızadan, 627 milisaniyede sıfır arızaya
>   indi.

## 60 saniyelik anlatım

> Raft, bir grup sunucunun sıralı bir log üzerinde anlaşmasını sağlayan
> algoritma — etcd'nin, dolayısıyla Kubernetes'in üzerinde çalıştığı şey. Yazmak
> bir hafta sonu sürer; asıl zor kısmı doğru olduğunu *bilmek*. Çünkü önemli
> hatalar, bölünmenin tam olarak iki belirli mesaj arasında açılmasını, ya da
> liderin girdiyi yazdıktan hemen sonra, kopyalayamadan ölmesini gerektirir.
>
> Ben de dünyanın tamamını deterministik hale getirdim: tek thread, sanal saat,
> tohumlanmış tek bir rastgelelik akışı. Her gecikme, her düşen paket, her çökme
> o tohumdan çıkıyor. Böylece başarısız bir koşu bir sayıya dönüşüyor: yeniden
> oynatabiliyorum ve otomatik olarak gerçekten önemli olan iki arızaya kadar
> küçültebiliyorum.
>
> Altyapının bir işe yaradığını kanıtlamak için, gerçek Raft implementasyonlarında
> görülmüş on üç hatayı kasıtlı olarak koda gömdüm. On üçü de yakalanıyor. İkisi
> hiçbir iç denetimin göremeyeceği türden — log kusursuz replike edilmiş, sadece
> bir istemcinin komutunu iki kez içeriyor — onlar için uçtan uca bir
> linearizability denetleyicisi gerekti.
>
> Bu arada kendi kodumda beş gerçek hata, bir de kendi sonuçlarımda bir abartı
> buldu. En sevdiğim kısmı sonuncusu.

## Sık sorulacak sorular

**"En zor bulduğun hata neydi?"** Okuma açlığı vakası: her şey sağlıklı
görünüyordu — bütün düğümlerde aynı log, sabit bir lider, sessiz bir ağ — ama
lider, asla cevaplanmayacak 21 okuma isteğinin üzerinde oturuyordu. Sebep
protokolde değil, etrafındaki muhasebedeydi.

**"Yeniden üretemediğin bir şeyi nasıl test edersin?"** Önce yeniden üretilebilir
hale getirirsin. Projenin tüm fikri bu: zamanı, ağı ve diski test edilen
sistemden alıp simülatöre vermek, sonra belirsizliği kontrol ettiğin bir tohumdan
kasıtlı olarak geri koymak.

**"Testlerinin iyi olduğunu nereden biliyorsun?"** Ölçtüm. On üç kasıtlı hata
teker teker koda gömülüyor ve kampanya her birinin kaç tohum dayandığını
raporluyor — yani test paketinin test paketi var. Ayrıca her koşu hangi ilginç
duruma ulaştığını kaydediyor; koca bir kategorinin hiç test edilmediğini böyle
fark ettim.

**"Yanıldığın bir anı anlat."** Pre-vote sonucu. Gerçek bir etkiyi ölçtüm ama
verinin desteklediğinden daha güçlü bir sonuç çıkardım; ancak canlılık denetimine
kendi kararını yeniden oynatma adımını ekleyince fark ettim. Düzeltilmiş sonuç —
evet/hayır yerine bir toparlanma süresi dağılımı — yerini aldığı iddiadan daha
faydalı çıktı.

**"Neden Python? Yavaş değil mi?"** Yavaş, ve burada önemi yok: simüle zaman
gerçek zamandan bağımsız, dolayısıyla kısıt gecikme değil, dakikada koşulan tohum
sayısı. Bir koşu ~50 ms sürüyor ve simüle ettiği sekiz saniyelik küme yaşamının
700 katı hızında. On kat daha fazla tohuma ihtiyacım olsa protokolü değil, olay
döngüsünü taşırdım.

**"Yapay zekâ desteği aldın mı?"** Açıkça söyle: hangi kısmı sen yönlendirdin,
hangi kısmı araç yazdı ve her satırını açıklayabildiğini göster. Bu depodaki
commit geçmişi bu konuda şeffaf. İyi bir mülakatçının ölçtüğü şey sistemi anlayıp
anlamadığın; o sorunun cevabı da mühendislik günlüğünde — hata ayıklamalar,
yanlış pozitifler, düzeltilen iddia — kimin hangi satırı yazdığında değil.
