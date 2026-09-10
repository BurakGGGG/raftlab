# raftlab

**Dağıtık bir konsensüs implementasyonu ve onun doğru çalıştığını kanıtlayan test altyapısı.**

*[English version](README.md) · [Tüm sonuçlar](RESULTS.md) · [Nasıl çalışıyor](docs/ARCHITECTURE.md) · [Mühendislik günlüğü](docs/ENGINEERING-LOG.md) · [Rapor](https://burakgggg.github.io/raftlab/tr.html)*

[![CI](https://github.com/BurakGGGG/raftlab/actions/workflows/ci.yml/badge.svg)](https://github.com/BurakGGGG/raftlab/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Bağımlılık: yok](https://img.shields.io/badge/dependencies-none-brightgreen)
![Test: 56](https://img.shields.io/badge/tests-56-brightgreen)
![Lisans: MIT](https://img.shields.io/badge/license-MIT-lightgrey)

```
$ python3 -m raftlab run --seed 12 --profile churn --timeline

  node 0 |.....c....xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.......cccc...                                   |
  node 1 |.cLLxxxxxxxxxxxxx.................ccccccc..ccccccLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLL|
  node 2 |.....cLLLLLLLLLLLLLLLLLLLLLLLLLxxxxxxxx....ccccc.......ccc......................................|
  node 3 |                                                          ..xxxxxxxxxx..........................|
  node 4 |                    ..............cccccccxxxxxxxxxxxxxxxxxxxxxxxxxxxxx..........................|
  commit |                     ....::::::----------------------------------------=====+++++*****###%%%%%%@|  0..198
         |0         1         2        3         4         5        6         7        8         9        |  saniye
```

Beş düğümlü bir kümenin on saniyesi: seçilip öldürülen liderler, hiçbir şeyin
üzerinde anlaşılamayan uzun bir dönem, kümeye katılıp ayrılan sunucular ve
kümenin toparlanıp commit edilmiş log'un yeniden fırladığı an. Koşunun tamamı
`12` sayısından yeniden üretilebiliyor.

---

## Bu ne, sade dille

**Konsensüs**, bir grup makinenin — bazıları çökmüşken, yavaşken ya da
diğerlerinden kopmuşken bile — sıralı bir komut listesi üzerinde anlaşmasıdır.
Bir sunucu yazma işleminin ortasında öldüğünde veritabanını tutarlı tutan şey
budur. [Raft](https://raft.github.io/raft.pdf), modern sistemlerin çoğunun bunun
için kullandığı algoritma: etcd (dolayısıyla Kubernetes), Consul, CockroachDB,
TiKV.

Raft'ı yazmak bir hafta sonu işi. **Asıl zor kısmı doğru olduğunu bilmek** — ve
bu proje esasen bununla ilgili.

Önemli hatalar, ağ bölünmesinin *tam olarak iki belirli mesaj arasında*
açılmasını, ya da liderin bir girdiyi yazdıktan sonra, herhangi bir yere
kopyalayamadan mikrosaniyeler içinde ölmesini gerektirir. Gerçek bir küme
çalıştırıp şansa güvenmek bunları bulmaz; nadiren bulduğunda da gördüğünüzü
yeniden üretemezsiniz.

Bu yüzden proje dünyanın tamamını deterministik hale getiriyor: **tek thread,
sanal saat, tohumlanmış tek bir rastgelelik akışı.** Mesaj gecikmeleri, paket
kaybı, çökmeler, GC duraklamaları, saat kayması, disk dayanıklılığı — hepsi
simüle ediliyor, hepsi tek bir tamsayıdan yeniden oynatılabiliyor. Bunun anlamı:

* başarısız bir koşu bir hikâye değil, bir **sayı**;
* simüle zaman neredeyse bedava, yani tek bir dünyayı saatlerce izlemek yerine
  binlerce olası dünya dakikalar içinde taranıyor;
* tüm küme tek bir süreçte yaşadığı için denetçi **her düğüme, her olaydan
  sonra** bakabiliyor — gerçek bir dağıtık sistemde imkânsız olan şey;
* ve bir hata **otomatik olarak** minimum tekrar-üretime küçültülebiliyor.

Bu yaklaşım [FoundationDB](https://www.youtube.com/watch?v=4fFDFbi3toc),
TigerBeetle ve Antithesis'in kurulduğu yaklaşımın aynısı. Buradaki her şey saf
Python ile sıfırdan yazıldı, hiçbir bağımlılık yok.

## Sonuçlar, özetle

| | |
| --- | --- |
| Doğru implementasyon | **82.600 simüle küme**'de temiz — 5.000 rastgele yapılandırılmış dünya dahil |
| Kasıtlı yerleştirilmiş hatalar | **13'ün 13'ü yakalanıyor**, ikisini hiçbir iç denetim göremiyor |
| Kendi kodumda bulunan gerçek hatalar | **5 tane**, her biri düzeltilip ölçüldü |
| Hız | **gerçek zamanın ~700 katı** — 184 saatlik küme yaşamı, 16 dakika duvar saatinde |
| Denetimler | *her* olaydan sonra 10 güvenlik değişmezi, artı linearizability ve canlılık |

Tüm tablolar, komutlar ve analiz: **[RESULTS.md](RESULTS.md)**.

## Otuz saniyede dene

```bash
git clone https://github.com/BurakGGGG/raftlab && cd raftlab
python3 -m raftlab run --seed 12 --profile churn --timeline   # bir kümenin ömrünü izle
python3 -m unittest discover -s tests -q                      # 56 test, ~12 sn
python3 -m raftlab fuzz --bug blind_truncate --seeds 20       # hata göm, yakalanışını izle
python3 -m raftlab shrink --seed 6 --bug commit_index_unclamped
```

Son komut, 21 enjekte edilmiş arıza ve sekiz saniye gerektiren bir hatayı
**hiç arıza içermeyen, 627 milisaniyelik** bir tekrar-üretime indiriyor — hata
en başından beri oradaydı, sıradan mesaj zamanlamasının arkasına saklanmıştı.

## Altyapının bulduğu şeyler

Hiçbir şey yakalamamış bir test altyapısı, sınanmamış bir iddiadan ibarettir. On
üç kasıtlı hata — her biri gerçek bir Raft implementasyonunda görülmüş — teker
teker koda gömülüyor ve kampanya her birinin kaç tohum dayandığını ölçüyor. Ama
daha ilginci, altyapının **doğru olduğuna inandığım kodda bulduğu beş hata**:

| ne bozuldu | nasıl ortaya çıktı | düzeltme ve ölçüm |
| --- | --- | --- |
| Yeniden iletim çoğalması | 209 girdiyi replike etmek için 183.560 mesaj | peer başına tek uçuş penceresi → 3.579 mesaj, 1,8× daha hızlı kampanya |
| Bayat commit indeksinden okuma | koşuların %2,2'si eski veri döndürdü | §6.4 bariyeri → 6.000 koşuda 0 |
| Yük altında okuma açlığı | sağlıklı küme, sonsuza dek bekleyen 21 okuma | tur başına onay takibi → tıkanma yok, %15 daha düşük gecikme |
| Replike edilmeyen yeni üye | koşuların %1'i kalıcı tıkandı | yapılandırma değişince replikasyon durumunu başlat → 400 koşuda 0 |
| Pre-vote'suz yavaş toparlanma | koşuların %8,4'ünde 1 sn'den uzun hizmet kesintisi | pre-vote → medyan toparlanma 227ms → 52ms |

En memnun olduğum bulgu ise **kendi sonuçlarımla** ilgili. Önce pre-vote'u
"kalıcı erişilemezliği önlüyor" diye yazmıştım. Sonra canlılık denetimini kendini
doğrulayacak hale getirdim — tıkanmış görünen koşu, kuyruğu uzatılarak yeniden
oynatılıyor — ve iddia çöktü: hiçbir küme kalıcı olarak ölmemişti, sadece yavaş
toparlanmıştı. İkili iddia ölçülmüş bir dağılıma dönüştü; hem daha dürüst hem
daha faydalı. O hikâye [mühendislik günlüğünde](docs/ENGINEERING-LOG.md).

## Nasıl çalışıyor

```
   tohum ─▶ dünya ─────▶ küme ────────▶ denetçi ──────▶ repro.json
            gecikme      5 düğüm,       10 değişmez     minimum,
            kayıp, kopya  saf durum     linearizability oynatılabilir
            bölünmeler    makineleri    canlılık             │
            çökmeler,        ▲                               │
            GC durakları     └────── küçült: bir arıza sil, yeniden oynat ─┘
            saat kayması
```

* **Protokol** (`raftlab/raft/`) — lider seçimi, log replikasyonu, çökme
  kurtarma, anlık görüntülerle log sıkıştırma, üyelik değişiklikleri, üç farklı
  okuma yolu ve tam-bir-kez semantiği için istemci oturumları. Düğüm **saf bir
  durum makinesi**: saat yok, soket yok, thread yok. Her giriş noktası zamanı
  alır ve gönderilecek mesajları döndürür — geri kalan her şeyi mümkün kılan şey
  bu.
* **Dünya** (`raftlab/sim/`) — sanal milisaniye saati üzerinde tek thread'li olay
  döngüsü: link bazlı gecikme profilleri, paket kaybı ve kopyalanması, teslim
  anında değerlendirilen bölünmeler, süreç çökmeleri, GC duraklamaları, saati
  duran askıya alınmış VM'ler ve çalışırken sunucu ekleyip çıkaran bir operatör.
* **Denetçi** (`raftlab/check/`) — her olaydan sonra artımlı olarak denetlenen on
  güvenlik değişmezi; *servisin* uçtan uca doğru olduğunu sınayan bir
  **linearizability denetleyicisi** (Porcupine ve Knossos'un kullandığı Wing &
  Gong araması); ve kararını bildirmeden önce kendini doğrulayan bir canlılık
  denetimi.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) hepsini düzgünce anlatıyor —
"linearizability denetleyicisi" ne demek ve neden gerekli dahil.

## Bu proje neyi gösteriyor

* **Test edilmesi zor şeyleri test etmeyi.** Deterministik simülasyon, arıza
  enjeksiyonu, özellik tabanlı denetim, otomatik test küçültme.
* **Bir spesifikasyonu okuyup sadakatle implemente etmeyi** — Raft makalesi ve
  tezi, insanların atladığı kısımlar dahil (§4.1 üyelik değişiklikleri, §6.4
  okuma yolları, §7 log sıkıştırma, §9.6 pre-vote).
* **İddia etmek yerine ölçmeyi.** Bu depodaki her iddia, herkesin yeniden
  çalıştırabileceği bir komutun ürettiği bir sayı.
* **Kendini açıkça düzeltmeyi.** Kendi denetçimdeki üç yanlış pozitif ve kendi
  sonuçlarımdaki bir abartı, sessizce silinmek yerine belgelendi.
* **Bir sistemi okunabilir kılmayı** — 20.000 satırlık bir olay izi tek bir
  resme, başarısız bir tohum insanın okuyabileceği bir şeye indirgeniyor.

## Dizin düzeni

```
raftlab/
  raft/       protokol: types.py, node.py, bugs.py (enjekte edilebilir hatalar)
  kv/         replike edilen anahtar/değer deposu, oturumlar ve anlık görüntüler
  sim/        dünya: cluster.py (olay döngüsü), network.py, faults.py, client.py
  check/      denetçi: invariants.py, linearizability.py, history.py
  runner.py   tam denetlenmiş tek koşu        campaign.py  paralel tohum taraması
  shrink.py   minimum tekrar-üretime indirgeme (delta debugging)
  timeline.py bu dosyanın başındaki resim
tests/        56 test: protokol, denetçi, determinizm ve altyapının hâlâ her
              gömülü hatayı yakaladığını doğrulayan meta-testler
docs/         mimari, mühendislik günlüğü ve yayımlanmış rapor
```

## Komutlar

```
python3 -m raftlab run       --seed N [--profile normal|hostile|slow|churn] [--timeline] [--trace]
python3 -m raftlab fuzz      --seeds 1:5000 [--bug B] [--profile P|swarm]
python3 -m raftlab campaign  --seeds 500      # doğru sürüm + her gömülü hata, dört dünya
python3 -m raftlab recovery  --seeds 1000     # küme iyileşmeden sonra ne kadar hızlı dönüyor
python3 -m raftlab reads     --seeds 3000     # üç okuma yolu, karşılaştırmalı
python3 -m raftlab coverage  --seeds 400      # fuzzer gerçekte neye ulaşıyor
python3 -m raftlab shrink    --seed N --bug B [--save repro.json] [--timeline]
python3 -m raftlab replay    repro.json
```

`make test`, `make demo`, `make campaign`, `make reads`, `make coverage` sık
kullanılanları sarmalıyor.

## Sınırlar

Açıkça yazıyorum, çünkü hiçbir sınırı olmadığını iddia eden proje dürüst değildir:

* Üyelik değişiklikleri tek sunucu ekleme/çıkarma biçiminde; birleşik konsensüs
  ve §4.2.1'deki yakalama aşaması implemente edilmedi.
* Bu, *protokolü* test eder; bir RPC yığınını, gerçek bir diski veya işletim
  sistemini değil. Bir simülatör yalnızca modelinin ifade edebildiği hataları
  bulabilir.
* Linearizability araması en kötü durumda üsteldir; adım bütçesiyle sınırlıdır ve
  bütçe dolunca "geçti" demek yerine `UNKNOWN` bildirir.
* `hostile` ve `slow` dünyaları Raft'ın kendi zamanlama varsayımını ihlal
  edebilir; canlılık denetçisi bunu kod hakkında değil, o dünya hakkında bir
  ifade olarak raporlar.

## Lisans

MIT — [LICENSE](LICENSE) dosyasına bakın.
