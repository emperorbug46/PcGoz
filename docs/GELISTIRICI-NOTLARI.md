# PcGöz — Geliştirici Notları

> **Not:** Bu dosya geliştirme sırasında tutulan teknik notlar, ölçümler ve mimari kararlardır; genel kurulum ve kullanım için [README.tr.md](../README.tr.md) dosyasına bakın.

---

PcGöz, Claude'un Windows masaüstünü **görmesi** ve **kullanması** için MCP sunucusudur.

Üç ayak birbirine bağlı:

1. **Microsoft UI Automation** — ekrandaki öğelerin rolü, metni, durumu ve tam koordinatı
2. **Ekran görüntüsü** — üzerine UIA öğelerinin numaralı kutuları çizilmiş hâlde
3. **SendInput** — gerçek fare ve klavye

Kritik olan 2 ile 1'in **aynı numara uzayını** paylaşması: ağaçta `ref_7` diye görünen buton,
ekran görüntüsünde `7` etiketli kutudur. Görme ile eylem arasındaki bağ burasıdır.

## Kurulum ve Kayıt Notları

```bash
uv sync
```

Kayıt `~/.claude.json` içinde kullanıcı düzeyinde, yani her projede açık:

```json
"mcpServers": {
  "pcgoz": {
    "type": "stdio",
    "command": "C:\\yol\\PcGoz\\.venv\\Scripts\\python.exe",
    "args": ["-m", "pcgoz"]
  }
}
```

`uv run` yerine venv'in python'u doğrudan çağrılıyor: MCP sunucusu Claude Code her açıldığında
başlatılıyor, o anda PATH'te `uv` olmayabilir ya da bağımlılık çözümlemesi ağa takılabilir —
ikisi de araçların sessizce görünmemesi demek. Bu yol deterministik ve anında.
**Bağımlılık değiştirirsen `uv sync` çalıştırman gerekir.**

**Kullanım becerisi de kurulmalı** — ayrı bir adım, MCP kaydı onu getirmiyor:

```powershell
New-Item -ItemType Junction -Path "$HOME\.claude\skills\masaustu-kullanimi" -Target C:\yol\PcGoz\skills\masaustu-kullanimi
```

Beceri "ne zaman PcGöz, ne zaman başka araç" yönlendirmesini ve tuzakları taşıyor. 2026-09-24'e
kadar hiçbir yere kurulmamıştı: repoda duruyordu, hiçbir oturuma ulaşmıyordu; sunucu 23 Eylül'den
beri 21 kez çağrılmıştı. Junction repoya bağlı olduğu için beceri repoyla birlikte güncellenir.
`--doctor` kurulu olup olmadığına bakar.

Kayıttan sonra Claude Code yeniden başlatılmalı. Doğrulama:

```bash
uv run pcgoz --doctor
```

`--doctor` çalışabilirliğe **ve** kaydın hâlâ bu klasörü gösterip göstermediğine bakar. En sinsi
arıza budur: klasör taşınır, sunucu hiç başlamaz, araçlar yokmuş gibi olur ve hata görünmez.
Dört durumu ayırt eder — komut diskte yok, hiç kayıtlı değil, sadece proje kapsamında, başka bir
Python'u gösteriyor — ve her birinde ne yapılacağını yazar. Sunucu modülünün import edilip
edilemediğine de bakar ve edilemiyorsa izini basar. (`--selftest` sadece çalışabilirliğe
bakar, kayda bakmaz.)

`uv` PATH'te değilse aynı komutlar venv'in python'uyla çalışır:

```bash
C:\yol\PcGoz\.venv\Scripts\python.exe -m pcgoz --doctor
C:\yol\PcGoz\.venv\Scripts\python.exe -m pcgoz --repair
```

`--repair` comtypes'ın UIA önbelleğini silip yeniden üretir, sonra `--doctor` çalıştırır.
`--stats` denetim günlüğünü özetler. Sürüm tek yerde: `src/pcgoz/__init__.py`;
`plugin.json`'daki elle tutuluyor, `--doctor` uyuşmazlığı yakalar.
Bayat önbellek için gerekmez (aşağıya bak); önbellek başka bir şekilde bozulduysa temiz başlangıç.

**Sunucu açılamazsa ölmez, yedek modda açılır.** Aynı 13 araç adını sunar; her biri ve sunucunun
tanıtım metni arızanın sebebini ve bu iki komutu söyler. Eskiden süreç ölüyordu ve Claude'da
sadece `CONNECTION_CLOSED` görünüyordu — araç yok, sebep yok.

## Eklenti Olarak Kurulum Notları

Depo aynı zamanda bir Claude Code eklentisidir: `.claude-plugin/plugin.json`, `.mcp.json` ve
`skills/masaustu-kullanimi/SKILL.md`. Eklenti yolu `${CLAUDE_PLUGIN_ROOT}` ile kendi konumuna
göre çözüldüğü için mutlak yol sabitlemez.

```bash
claude plugin install C:\yol\PcGoz
```

Tek oturumluk denemek için: `claude --plugin-dir C:\yol\PcGoz`

**Dikkat:** Eklentiyi kurarsan `~/.claude.json`'daki elle yapılmış `pcgoz` kaydını kaldır —
yoksa aynı sunucu iki kez tanımlı olur.

**Doğrulanmadı:** Eklenti kurulumu claude CLI ortamında henüz doğrudan test edilmedi / doğrulanmadı
(masaüstü ortamında test edilmiş olup npm CLI ile doğrulanmadı). Dosya yapısı, JSON'lar,
`SKILL.md` ön bilgisi ve `${CLAUDE_PLUGIN_ROOT}` yerine gerçek yol konarak sunucunun ayağa kalkması
test edildi; Claude Code'un manifesti eklenti olarak kabul edip etmediği bağımsız CLI ile doğrulanmadı.

## Araçlar

**Görme** — hiçbiri girdi göndermez, tamamen güvenli

| Araç | İş |
|---|---|
| `pc_windows` | Açık pencereler + **açık menüler** ayrı bölümde |
| `pc_read` | Erişilebilirlik ağacı → her öğeye `ref_N`. `filter`: interactive / all / text |
| `pc_find` | Rol/metin/değer içinde arama, en fazla 20 sonuç |
| `pc_text` | Pencerenin metni: belge içeriği + arayüz yazıları |
| `pc_screenshot` | PNG; `annotate=True` ref numaralarını kutu olarak çizer |
| `pc_ocr` | Ekrandaki yazıyı OCR ile okur; her satır ekran koordinatı ve tıklama merkeziyle |

Menüler ayrı bir kategori: Windows'ta sağ tık menüsü *sahipli* (`GW_OWNER`) bir üst düzey
penceredir ve normal pencere listesinden elenir. `pc_read`'i hwnd vermeden çağırırsan, ekranda
açık bir menü varsa onu okur ve bunu çıktıda söyler — çünkü menü açıkken ekranın sahibi odur.

**Eylem**

| Araç | İş |
|---|---|
| `pc_invoke` | UIA deseniyle eylem — fareye dokunmadan. **Önce bu denenmeli** |
| `pc_do` | Tıkla / yaz / tuş / kaydır / sürükle / bekle |
| `pc_wait_for` | Bir öğe belirene ya da kaybolana kadar bekle |
| `pc_clipboard` | Pano oku/yaz |
| `pc_focus` | Pencereyi öne al |
| `pc_window` | Pencereyi kapat / küçült / büyüt / geri getir / taşı |
| `pc_batch` | Birden fazla aracı tek çağrıda sırayla çalıştır, ilk hatada dur |

`pc_do`'ya ekran görüntüsündeki bir pikseli `space="screenshot"` ile verebilirsin; ekrana
çevirmeyi araç yapar (pencere yakalaması, bölge, `scale` fark etmez). `pc_window close`
pencerenin X düğmesidir (`WM_CLOSE`): süreci öldürmez, kaydedilmemiş iş varsa uygulama kendi
sorusunu sorar. Görev çubuğu ve masaüstü yönetilmez — onlara `WM_CLOSE` "Windows'u kapat"
iletişim kutusunu açar.

`pc_invoke` neden tercih edilir: odağı çalmaz, imleci oynatmaz, pencere örtülüyken de çalışır,
ve araya açılan bir menü girse bile yanlış şeye tıklama ihtimali yoktur.

**Her eylem ne değiştiğini söyler.** `pc_do` ve `pc_invoke` eylemden önce hedef pencerenin
anlık görüntüsünü alır, sonra ekran durulana kadar izler ve farkı özetler:

```
Button "Degistir" calistirildi (Invoke).

Sonuc (0.3 sn izlendi):
pencere icerigi: 1 yeni, 0 kayboldu, 1 degisti
  + Text "Kaydedildi" [ref_11]
  ~ Edit "ilk" [ref_2]: deger "ilk" -> "degisti"
```

Ön plan değişimi, açılan/kapanan pencere ve menüler, başlık değişimi ve odak da raporlanır.
Yeni öğeler ref'leriyle gelir, yani çoğu zaman ayrıca `pc_read` gerekmez. Hiçbir şey
değişmediyse bunu açıkça söyler; `invoke` etkisiz kaldıysa tıklamayı önerir. `type` ayrıca
metnin hangi öğeye gittiğini ve öğenin yeni değerini söyler; değer değişmediyse uyarır.
Maliyeti eylem başına 0.3–1.2 sn; `observe=False` ile kapatılır. `pc_batch` özeti varsayılan
olarak sadece son eylem adımında alır. Aynı rol ve adla hem kaybolup hem beliren öğe (sekme
değişiminde yeniden çizilen düğme) "kayboldu" diye bildirilmez, `(yeniden olusturuldu)` diye
işaretlenir.

Ağır sayfada da ucuz kalması için izleme sadece **ekrandaki** öğeleri ister: süzgeci UIA'nın
kendisi uygular. Ölçüldü (Brave, 1500 satırlık sayfa, 9055 öğe):

| | önce | sonra |
|---|---|---|
| bir izleme turu | 1382 ms | 177 ms |
| odağı okumak (odak sayfa belgesindeyken) | 473 ms | 1 ms |
| eylem + izleme | 2149 ms, "hâlâ değişiyordu" | 612 ms, duruldu |

İkinci satır ayrı bir hataydı: tek öğe okuyan odak ve noktadaki öğe sorguları da alt ağaç
istiyordu, yani sayfa belgesi odaktayken her turda 9000 öğe çekiliyordu.

**Okuma araçları da sadece ekrandakini çeker** (0.5.0; `pc_read`, `pc_find`, `pc_text`,
`pc_wait_for`). Ekran dışı öğeler zaten hiçbir kipte gösterilmiyordu — `render` ve `pc_find`
onları sonradan atıyordu — ama önce çekiliyordu. Bunun bir de doğruluk bedeli vardı:
`MAX_ELEMENTS` (4000) belge sırasıyla kestiği için **aşağı kaydırılmış uzun bir sayfada görünen
satırlar hiç gelmiyordu**; `pc_read` sadece tarayıcı çerçevesini gösteriyor, `pc_find` görünen
düğmeyi bulamıyordu. `tests/agir_sayfa.py` ile ölçüldü (1500 satır, ~9000 öğe):

| | önce | sonra |
|---|---|---|
| `pc_find`, soğuk (erişilebilirlik uyandırma dahil) | 4724 ms | 1348 ms |
| `pc_find`, ılık | 861 ms | 154 ms |
| `pc_read` | 869 ms | 155 ms |
| sona kaydırılmış sayfada son satır | bulunamadı | bulundu |

Aynı ölçümde ikinci bir kusur çıktı: kardeş katlama adsız kapsayıcıları (tablo satırı)
kendi adlarına bakıp "aynı" sayıyordu. Ekrandaki son 18 satır `... ayni turden 18 oge daha`
diye gizleniyordu. Artık adı olmayan kapsayıcıda alt ağacın ilk metnine bakılıyor. Bir uygulama
`IsOffscreen`'i yanlış bildirirse süzgeç `PCGOZ_ONSCREEN_ONLY=0` ile kapatılır.

## Güvenlik Notları

Model **serbest + log**: onay sorusu yok, ama şunlar var:

- **Acil durdurma:** `Ctrl+Alt+Shift+Esc` — aç/kapa. Basıldığı anda girdi üreten her araç
  hata döndürür; okuma araçları çalışmaya devam eder. Yazmanın *ortasında* da keser.
  Kısayol başkası tarafından tutuluyorsa sırayla `Pause`, `F12`, `Backspace` denenir; **hiçbiri
  alınamazsa girdi araçlarının her cevabına uyarı eklenir** — durdurulamayan bir aracın sessizce
  çalışması kabul edilebilir değil.
  **Dondurma bütün PcGöz süreçlerinde ortak.** Birden fazla Claude oturumu açıksa her birinin
  kendi sunucusu var, kısayolu ise yalnızca biri tutabiliyor; dondurma süreç içinde kalsaydı
  Esc sadece o sunucuyu durdurur, öteki oturum fareyi sürmeye devam ederdi. Artık adlı bir
  olay nesnesi (`Local\PcGozAcilDurdurma`) paylaşılıyor: biri dondurunca hepsi donar, hangi
  kısayolla basılırsa basılsın çözülür. Yönetici hakkıyla ve normal çalışan süreçler aynı
  nesneyi açabiliyor (ölçüldü). Esc'i tutan sunucu kapanırsa yedek kısayoldaki diğerleri 5 sn
  içinde Esc'i devralır — kullanıcının bildiği kısayol hep aynı kalsın. `--doctor` hangi
  kısayolun alındığını ve ortak nesnenin durumunu gösterir. Testler kendi nesnelerine yazar
  (canlı sunucuyu dondurup çözmesinler), okurken ortak olana da bakar.
- **Eylem sürerken kullanıcının fiziksel fare/klavyesi kilitlenir — kullanıcı o an kullanıyor
  olsa da** (0.6.0, kullanıcının isteği). Eskiden son 1,5 sn'de insan girdisi varsa eylem
  `UserBusy` ile reddediliyordu; başka bir oturum tam bu yüzden takıldı. Artık reddedilmiyor,
  kilit hemen alınıyor:
  - Yazılan metne harf karışmaz, tıklama/sürükleme kaymaz.
  - Kilit eylem ve "ne değişti" izlemesi boyunca sürer. Bütün eylem araçlarında geçerli
    (`pc_do`, `pc_invoke`, `pc_focus`, `pc_window`); `pc_batch`'te bütün toplu iş boyunca.
    Okuma araçları kilitlemez.
  - Kilitliyken Ctrl+Alt+Shift+Esc yine çalışır ve kilidi hemen kaldırır.
  - Kilit en fazla 30 sn (toplu işte 60 sn) sürer.

  Kapatmak için `PCGOZ_BLOCK_INPUT=0`; eski "meşgulse reddet" davranışı
  `PCGOZ_REFUSE_WHEN_BUSY=1`.
- **Kilit anında basılı tuş ve fare düğmesi.** Kullanıcı tam o anda Ctrl/Shift/Alt/Win ya da bir
  fare düğmesini tutuyorsa PcGöz'ün yazdığı harf kısayola döner (`a` → Ctrl+A), sürüklenen şey
  PcGöz'ün hedefine taşınır. Bu yüzden:
  - Önce kullanıcının bırakması beklenir (en fazla 1,5 sn). Kilit bırakmaları geçirir, yeni
    basmaları yutar; sürüklenen şey kullanıcının bıraktığı yere düşer.
  - Süre dolarsa sentetik bırakma gönderilir. Alt ve Win'den önce atanmamış bir "maske" tuşu
    (`0xE8`) gider; tek başına Alt bas-bırak menü çubuğunu, Win Başlat'ı açardı (ölçüldü:
    maskesiz menü moduna giriyor, maskeli girmiyor).
  - CapsLock/NumLock aç/kapa tuşu olduğu için dokunulmaz.
- **Oturumlar arası sıra.** Her Claude oturumunun kendi PcGöz sunucusu var. Eski "meşgul" kuralı
  farkında olmadan onları sıraya sokuyordu: birinin gönderdiği girdi, öteki oturuma insan
  girdisi gibi görünüyordu. Artık adlı bir kilit (`Local\PcGozGirdiKilidi`) aynı anda tek
  oturumun girdi sürmesine izin veriyor.
  - Öteki en fazla 20 sn bekler, sonra "başka bir PcGöz oturumu çalışıyor" der.
  - Kilidi tutan süreç çökerse kilit hemen sıradakine geçer.
- **Sol üstte uyarı.** Kilit sürerken ekranın sol üstünde yarı saydam bir kutu çıkar:
  `PcGöz çalışıyor — fare ve klavye kilitli · Durdurmak için Ctrl+Alt+Shift+Esc`.
  - Basılı tuş beklenirken "Basılı tuşu bırakın", acil durdurmada kırmızı "PcGöz durduruldu"
    der.
  - Tıklama içinden geçer, odak çalmaz.
  - PcGöz'ün kendi ekran görüntüsüne ve OCR'ına girmez (`WDA_EXCLUDEFROMCAPTURE`); pencere
    listesinde ve "ne değişti" özetinde görünmez.
  - Kilit bittikten ~0,4 sn sonra kaybolur; ardışık eylemlerde yanıp sönmez.
  - Kapatmak için `PCGOZ_OVERLAY=0`.
- **Denetim günlüğü:** `%USERPROFILE%\.pcgoz\actions.jsonl` (`PCGOZ_LOG_DIR` ile değişir).
  0.4.0'a kadar `%LOCALAPPDATA%\PcGoz`'daydı ve bu sessizce ikiye bölünüyordu: Claude
  masaüstü uygulaması bir MSIX paketi, Windows paketin süreçlerinin `AppData`'ya yazdıklarını
  `%LOCALAPPDATA%\Packages\Claude_…\LocalCache\Local`'a yönlendiriyor. Paketin içindeki sunucu
  günlüğü orada tutuyordu, paket dışından çalışan `--stats` gerçek `AppData`'ya bakıp "kayıt
  yok" diyordu. Kullanıcı klasörü sanallaştırılmıyor. Eski yerlerdeki günlükler her başlangıçta
  (sunucu, `--doctor`, `--stats`) yeni yere aktarılır; özel bir günlük yeri verilmişse
  (testler) eskiye dokunulmaz. Her eylem, hedefi ve yazılan
  metinle birlikte, eylemden *önce* yazılır. Ayrıca her araç çağrısının sonucu (başarı ya da
  hata türü: `UserBusy`, `StaleRef`, `NotResponding`…) ve süresi; okuma araçları dahil, ama
  onların argümanları yazılmaz. Eylemin ön kaydı ile sonucu aynı `call` kimliğini taşır,
  `pc_batch` adımları `batch` ile topluya bağlanır. 5 MB'da döner. Özet:
  `python -m pcgoz --stats` (araç başına çağrı, hata türleri, p50/p95 süre). Testler gerçek
  günlüğe yazmaz (`tests/_gunluk.py`).
- **Tek sert blok:** UIA `IsPassword` alanına ve `consent.exe` / `credentialuibroker.exe` /
  `logonui.exe` süreçlerine yazma reddedilir. Bu bir onay kapısı değil, kapalı bir yol.
  `key` eylemi de kapsamda: bu hedeflere sadece gezinme/silme tuşları gider (tab, enter, esc,
  oklar, home/end, backspace, delete, ctrl+a). Eskiden `pc_clipboard set` + `key ctrl+v` ya da
  tuş tuş `key a` ile engel aşılabiliyordu; yasak listesi hep bir yolu kaçırdığı için izin
  listesi seçildi.
- **Yapıştırma panoyu ödünç alır, geri verir.** Uzun metin panodan yapıştırılırken
  kullanıcının panosu (resim, dosya, özel biçimler dahil) saklanır ve hedef metni *okuduktan
  sonra* geri konur. Geçici metin Win+V geçmişine ve bulut panosuna girmez.
  **26H2 (0.5.1):** `CrossDeviceService.exe` (cihazlar arası pano) her pano değişikliğini
  ~200 ms sonra okuyor ve hiçbir "izleme dışı" işaretini dinlemiyor. Hedefin okuması sanılıyor,
  pano erken geri konuyor, geç okuyan hedefe **kullanıcının eski panosu** yapıştırılıyordu.
  Artık yalnızca hedef pencerenin süreçlerinden gelen okuma sayılıyor (`windows.input_pids`).
  Arka plan okuyucularına (`BACKGROUND_READERS`) metin hiç verilmiyor: gecikmeli sunum beklemede
  kalıyor, geç okuyan hedef yine yakalanıyor (ölçüldü: 406 ms, 1000 ms). Geçici metin cihazlar
  arası panoya da gitmiyor.

Ayrıca **bayat ref eski koordinata tıklamaz**. Pencere kapandıysa ya da hedefin üstü başka bir
uygulamayla örtüldüyse araç hata döndürür — çünkü o koordinatta artık başka bir şey vardır.

`PCGOZ_DRY_RUN=1` ile okuma araçları çalışır, girdi araçları sadece loglar.

## Bilinen Sınırlar

- **Yönetici pencereleri, sunucu yükseltilmemişse erişilemez** (UIPI). Claude Code yönetici
  olarak başlatılırsa sunucu da yükseltilmiş çalışır ve bu pencereler açılır — Görev
  Yöneticisi'nde okuma, `pc_invoke` ve gerçek tıklama denendi. UAC istemi (güvenli masaüstü)
  her durumda kapalı. `--doctor` "Yonetici" satırında hangisi olduğunu söyler.
- **Güvenli masaüstü aşılamaz, ama artık söylenir.** UAC onayı, Ctrl+Alt+Del ekranı ve kilit
  ekranı `Winlogon` masaüstünde çizilir; oraya SYSTEM dışında hiçbir süreç erişemez. Eskiden
  bu sırada `SendInput` sessizce boşa gidiyor, ekran görüntüsü kararmış geliyordu; araçlar
  sebebini söylemeden başarısız oluyordu. Artık girdi araçları (`pc_do`, `pc_invoke`,
  `pc_focus`, `pc_window`, `pc_batch`) girdi masaüstü `Default` değilse hiçbir şey göndermeden
  "GÜVENLİ MASAÜSTÜ açık, kullanıcı yanıtlamalı" der. `pc_screenshot` da resmin güncel
  olmayabileceğini notuna yazar. Canlıda sadece normal durum doğrulandı; kilit ekranını denemek
  oturumu kilitlerdi. Reddetme yolu birim testinde sahte masaüstü adıyla deneniyor.
- **Yönetici hakkıyla başlatılan Brave çöküyor** (0xC0000005, ölçüldü 2026-09-24). PcGöz'ün
  sorunu değil, ama yükseltilmiş bir kabuktan tarayıcı açmak gerekirse normal haklarla
  açılmalı; `tests/kapsam.py` bunu Explorer üzerinden yapıyor.
- **Chromium'da `pc_invoke` pencereyi öne alabilir.** Web düğmesine desenle basmak Brave'i ön
  plana getirdi (ölçüldü); "odağı çalmaz" kuralı yerel Windows denetimleri için geçerli.
- **Qt / oyunlar / kendi çizen arayüzler** UIA'ya bir şey vermez. Bu durumda `pc_ocr` ile yazı
  ve koordinatı okunur, `pc_screenshot` ile gözle bakılır. OCR hata yapabilir (`79/79` →
  `79179`); simgeleri ve yazısız düğmeleri görmez.
- **Donmuş uygulama okunamaz, ama artık kilitlemez.** Yanıt vermeyen pencereye yapılan istek
  0,5 sn'de "yanıt vermiyor" hatasıyla döner; ekran görüntüsü ekrandan kırpılır (üstü
  örtülüyse üstteki görünür).
- **Çoklu monitör gerçek bir ikinci ekranla doğrulanmadı.** Koordinat dönüşümü, negatif
  başlangıçlı ve farklı boyutlu sanal masaüstleri için her piksel sütununda test ediliyor
  (`tests/birim.py`); `pc_windows` birden fazla ekran varsa her birinin konumunu ve ölçeğini
  listeler. Farklı ölçekli iki ekranın sınırında kalan pencereler denenmedi.
- **Odak kilidi.** Ön planda başka bir süreç varken (ör. kullanıcı görev çubuğuna tıklamışsa)
  Windows `SetForegroundWindow`'u reddeder; ölçüldü: iş parçacığı bağlama, Alt hilesi,
  `SwitchToThisWindow` hiçbiri aşmıyor. Tıklama ise aşıyor. 0.5.0'dan beri `pc_focus` bu
  durumda başlık çubuğuna kendisi tıklıyor, imleci sonra yerine koyuyor. Tıklanacak noktanın
  iki şartı var:
  - pencere o noktayı `WM_NCHITTEST` ile `HTCAPTION` (sürükleme alanı) sayıyor. Brave, Claude
    ve Win11 Not Defteri başlığı kendileri çiziyor; sekme ya da düğme değil;
  - orada gerçekten o pencere var, üstü örtülü değil.

  Böyle bir nokta yoksa (başlık tamamen örtülü) araç eskisi gibi açıkça uyarır. Görev çubuğu ve
  masaüstü hiçbir zaman nokta vermez.
- **Parola bloğu sağ tık menüsünü görmez.** Parola alanında sağ tık → "Yapıştır" menü öğesine
  `pc_invoke` ya da sürükle-bırak engellenmez: menü açıldığında odak parola alanından menüye
  geçer. Blok yazma, değer atama ve tuşları kapatır; model niyetle aşmaya çalışırsa aşabilir.
- **Ekran görüntüsü boyutu modele göre.** Varsayılan sınır yüksek çözünürlük kuşağının
  (2576 px / 3,75 MP); daha eski bir modelle (Haiku 4.5, Sonnet 4.6 ve öncesi)
  `PCGOZ_MAX_EDGE=1568` ve `PCGOZ_MAX_PIXELS=1150000` verilmeli, yoksa API resmi bir kez daha
  küçültür ve koordinatlar kayar.

## Gerçek Kullanım Notları

**2026-09-24 görev turu.** Beş gerçek iş, sadece PcGöz araçlarıyla yapıldı; uygulamayı açmak
için kabuk kullanıldı:

| Görev | PcGöz çağrısı | Sonuç |
|---|---|---|
| Ayarlar > Ekran: ölçeği oku | 2 | %125 (Önerilen) |
| Not Defteri: yaz, Farklı Kaydet ile scratchpad'e kaydet | 4 (2'si toplu iş) | dosya birebir aynı: Türkçe karakterler, uzun tire, `79/79` |
| Görev Yöneticisi: belleğe göre sırala, en çoğunu bul | 3 | sıralama `pc_invoke` ile, fare oynamadan |
| Dosya Gezgini: README.md boyutu | 2 | 35 KB (kabukla doğrulandı: 34.943 bayt) |
| SadePDF (Qt + WebEngine): arayüzü oku | 1 | okundu |

Hiç hata çıkmadı. Odak geri düşüşü gerçekte ilk kez burada devreye girdi: Not Defteri öne
alınamadı, `pc_focus` başlık çubuğuna tıklayıp aldı. Turda bulunan beş pürüz aynı gün
düzeltildi:

1. **Kapalı açılır kutunun seçimi görünmüyordu.** WinUI'de seçim öğenin değeri değil, seçili
   çocuğu. Satırda artık `secim="125% (Önerilen)"` yazıyor; ayrıca `pc_read ref_id` gerekmiyor.
2. **Görünmez yön işaretleri.** Dosya Gezgini tarihleri `‎24.‎09.…` diye veriyor. Ad,
   değer, AutomationId ve yardım metninden atılıyor.
3. **Qt nesne yolları ad gibi görünüyordu.** Qt, adı olmayan öğeye `QApplication.MainWindow.
   QWebEngineView.QObject.viewer…` veriyor; SadePDF çıktısının yarısı buydu. Artık boş
   sayılıyor. Aynı bütçede gösterilen öğe 45'ten 61'e çıktı.
4. **`pc_find` öğenin değerinde aramıyordu.** Adı olan bir kutunun içeriği (Gezgin'de
   "Değiştirme tarihi" = `24.09.2026 14:42`) bulunamıyordu. `pc_wait_for`'da da aynı eksik
   vardı.
5. **Yeni kodu almak için yeniden bağlama gerekmiyor.** Bu oturumun sunucu süreci durdurulursa
   Claude Code onu bir sonraki araç çağrısında kendisi yeniden başlatıyor. Aynı bilgisayardaki
   öteki oturumların sunucularına dokunulmamalı; hangisinin hangi oturuma ait olduğu ebeveyn
   `claude.exe`'nin `--resume` kimliğinden anlaşılır.

## Mimari ve Tasarım Kararları

Hepsi ölçüme ya da yaşanmış bir arızaya dayanıyor; kaldırılırsa araç *sessizce* yanlış çalışır.

**UIA tipleri `comtypes.client.GetModule` ile yükleniyor**, `from comtypes.gen import
UIAutomationClient` ile değil. comtypes'ın ürettiği sarmalayıcı `UIAutomationCore.dll`'in
mtime'ına kilitli; 2026-08-31'deki Windows güncellemesi dll'i değiştirince doğrudan import
`ImportError: Typelib different than module` verdi ve sunucu 23 gün boyunca hiç açılmadı.
`GetModule` bayat modülü yakalayıp yeniden üretiyor (0.2 s).

**Araçlar `mcp.tool` ile değil `_tool` ile kayıtlı.** mcp 2.x, `ToolError` dışındaki her
istisnanın metnini gizleyip modele sadece `Error executing tool X` gönderiyor. Bu aracın hata
mesajları ise modele yazılmış talimatlar — "DONDURULDU, kullanıcıya sor", "kullanıcı meşgul",
"bayat ref, tazele". `_tool` her istisnayı metniyle `ToolError`'a çeviriyor. Modüldeki adlar ham
fonksiyon kalıyor, yani doğrudan çağıran testler `StaleRef`/`Frozen` türlerini yakalayabiliyor.

**Donmuş pencere hiçbir aracı kilitlemez.** Bütün araçlar tek iş parçacığında sırayla çalıştığı
için donmuş tek bir uygulama hepsini durdurabiliyordu. Ayrı süreçte dondurulan bir test
penceresinde ölçüldü:

| | önce | şimdi |
|---|---|---|
| `pc_read` (UIA) | 20,7 sn (pencere kendine gelene kadar) | 0,50 sn, "yanıt vermiyor" |
| `pc_screenshot` (PrintWindow) | 14,7 sn | 0,53 sn, ekrandan kırpılmış |
| `pc_invoke` | — | 0,50 sn, "yanıt vermiyor" |

Üç katman: (1) pencereye 500 ms zaman aşımlı boş mesajla (`WM_NULL`) yoklama — Windows'un kendi
"yanıt vermiyor" işareti ancak 5 sn sonra geliyor; (2) eski `CUIAutomation` yerine
`CUIAutomation8`, bağlantı 2 sn / işlem 10 sn zaman aşımıyla — eski sınıf bu ayarı hiç
sunmuyor; (3) iş parçacığı bir çağrıda 90 sn'den fazla takılırsa sonraki istekler beklemeden
hata döner, zaman aşımına uğrayan istek de **sonradan çalıştırılmaz** — yoksa model "başarısız"
sandığı bir tıklamanın saniyeler sonra gerçekleştiğini bilemezdi. Not: `CUIAutomation8` çok
satırlı Win32 Edit'i `Document` rolünde veriyor.

**OCR Windows'un kendisinden** (`Windows.Media.Ocr`, `winrt-*` paketleri). Kurulu dil neyse
onu kullanır (örneğin Türkçe için `tr`). Paketler sadece `pc_ocr` çağrılınca yükleniyor, bozulurlarsa
sunucu yine açılır. `find` araması aksan ve büyük/küçük harf duyarsız — OCR `ş/s`, `ı/i`
karıştırabiliyor, `casefold` da Türkçe İ/ı'yı eşleştirmiyor — ve satırın değil eşleşen
kelimelerin kutusunu verir.

**OCR görüntüyü 2× BICUBIC büyütüp okur** (0.5.0; tam ekran ~0,25 sn). `tests/ocr_olcum.py`
ile ölçüldü; iki ölçüm birbirini düzeltti:

| | 1× | 2× BICUBIC | 3× | 2×/3× LANCZOS |
|---|---|---|---|---|
| gerçek pencere, bulunan metin /8 (11/13/16 px) | 4/4/5 | **5/6/5** | 6/3/3 | 3/3/3 · 6/3/3 |
| sentetik sayfa, karakter hata oranı | %31,9 | %15,7 | %13,1 | %13,7 · %9,8 |

Yalnız sentetik sayfaya (düz zemin) bakılsaydı LANCZOS 3× seçilecekti. Oysa gerçek bir Win32
penceresinde LANCZOS düğme yazılarını **tamamen siliyor**: kenarlığın halka artıkları yazıyı
kenarlığa karıştırıyor. 3× de 13–16 px arayüz yazısını bozuyor. Denenip bırakılan bir fikir
daha var: iki ölçekte okuyup farklı çıkan satırı "belirsiz" işaretlemek. Sentetik sayfada hiç
yanlış alarm vermedi, gerçek pencerede doğru satırların 11–13/37'sini işaretledi. Üçte biri
yanlış olan bir işaret modele gürültüden başka bir şey vermez, üstelik süreyi ikiye katlıyordu.
Kalan tipik hatalar: `v0.4.0` → `vo.4.o`, `\` → `X`. Sayı, tarih, yol gibi kritik metni gözle
doğrula.

**"Ne değişti" özeti eylemin kendi cevabında.** Denetim günlüğünde (30–31 Ağustos, 239
eylem) eylemler arası 5–40 sn vardı: model her eylemden sonra sonucu ayrıca okuyordu. Bir kez
de `pc_invoke` "başarılı" dönüp hiçbir şey yapmamış, aynı düğmeye 4 sn sonra fiziksel olarak
tıklanmıştı; aynı metin de üç kez yazılmıştı. Öğeler iki anlık görüntü arasında RuntimeId ile
eşleniyor. Ölçüldü: görüntü başına 20–60 ms, odak 15 ms, pencere listesi 0,5 ms; ekran iki
ardışık görüntü aynı olana kadar izleniyor (en fazla 1,2 sn).

**Aynı öğe her okumada aynı ref'i alır.** Kimlik (RuntimeId) ve rol aynıysa eski numara
veriliyor. Eskiden her okuma yeni numara veriyordu: bir günde ref_136'ya çıkılıyordu, bir önceki
`pc_read` çıktısındaki numara ile yeni ekran görüntüsündeki kutu tutmuyordu. RuntimeId okumak
süreçler arası çağrı değil (229 öğe için 1,3 ms). Rol farklıysa kimlik yeniden kullanılmış
sayılıyor ve yeni numara veriliyor.

**Çıktılarda satır sonları `\n` olarak gösterilir.** Ağaç ve özet satır satır okunuyor; çok
satırlı bir değer (belge, çok satırlı kutu) satırı bölüyor ve devamı başka bir öğe
sanılıyordu.

**Ekran görüntüsü model sınırını aşmıyorsa küçültülmez.** Sınır içindeki resimde güncel
modellerin koordinatları piksellerle 1:1 eşleniyor: 1920x1080 ekran olduğu gibi gider ve
resimdeki konum doğrudan `pc_do` koordinatıdır. Eskiden uzun kenar sabit 1568'e iniyordu —
modele gereksiz bir `/ 0.817` hesabı yaptırıyordu, ve 1568x882 = 1,38 MP ile eski modellerin
sınırını da aşıyordu. `scale` ile yakınlaştırma da sınırda kesilir; aşan resmi API geri
küçültür ve bildirilen ölçek tutmaz. Kutular çizilmeden önce her öğenin konumu tazelenir
(öğe başına ~0,2 ms): ağaç son `pc_read`'den kalıyor, arada pencere kaydıysa kutular eski
yere çiziliyordu.

**Home/End tek başınayken genişletilmiş bayraksız gönderilir.** Sistemde AutoHotkey gibi araçlar
genişletilmiş Home/End'i tutuyorsa `key home` / `key end` uygulamaya hiç ulaşmıyordu.
Değiştiriciyle birlikte ise genişletilmiş kalmak zorunda — bayraksız Home numpad 7 sayılır ve
NumLock açıkken Windows Shift'i sahte bir bırakmayla düşürür, `shift+home` seçim yapmaz. Üç
durum da `tests/tus_probu.py` ile ölçüldü.

**Tek karakterli tuşlar hedef pencerenin düzeninde çözülür** ve gereken değiştirici eklenir.
Eskiden Shift bilgisi atılıyordu: Türkçe Q'da `key "?"` → `*`, `key "@"` → `q` gidiyordu.

**Yapıştırma gecikmeli sunumla (delayed rendering).** Panoya metin değil bir "söz" konur;
hedef okumaya kalkınca Windows `WM_RENDERFORMAT` gönderir, yani okumanın anı kesin bilinir ve
pano ondan sonra geri konur. Eskiden sabit 150 ms bekleniyordu: 400 ms'de okuyan bir hedef
kullanıcının *eski* panosunu yapıştırıyordu (`tests/pano.py` bunu karşılaştırmalı gösterir).
Hedef 2 sn'de okumazsa araç bunu söyler.

**DPI farkındalığı** `__main__.py`'nin ilk satırlarında, herhangi bir import'tan önce.
Örneğin %125 ölçekli bir ekranda farkında olmayan bir süreç ekranı `1536x864` görüyor, gerçeği
`1920x1080`. Ayarlanmazsa her koordinat %25 kayar.

**`BuildUpdatedCache` + `TreeScope_Subtree`** ile ağaç okuma. Tek COM çağrısı bütün alt ağacı
istenen özelliklerle getiriyor (155 öğe / 40 ms), sonrasındaki gezinme tamamen bellekte (1 ms).
Özellik-başına-COM-çağrısı yaklaşımı aynı ağaçta saniyeler sürer.

**Yazma gecikmesi 25-40 ms.** 6-18 ms'de Windows 11 Not Defteri'nde ardışık karakterler
birleşip son harfe çöküyordu: `ABCDEFGHIJ` → `DDDDEFGHIJ`, `123` → `333`. Uygulama mesaj
kuyruğunu boşaltmaya fırsat bulamadan `KEYEVENTF_UNICODE` olayları üst üste biniyor.
Düşürmeden önce `tests/smoke.py`'deki pano round-trip testi çalıştırılmalı.

**Tarayıcıda "sayfa geldi mi" ölçütü `Document` düğümünün boyutu**, penceredeki toplam öğe
sayısı değil. Chromium erişilebilirliği tembel açar; pencere hazır görünürken sayfa ağacı boş
gelir. Soğuk bir tarayıcıda `youtube.com` açılırken ölçüldü:

| t | toplam | `Document` altı | içerik alanı |
|---|---|---|---|
| 0.1 s | 76 | −1 (yok) | 15 |
| 0.9 s | 91 | 1 | 24 |
| 1.7 s | 120 | 29 | 48 |
| 2.5 s | 129 | 38 | 57 ← sayfa geldi |

İçerik alanı sayacı ilk andan itibaren 15 — hiçbir eşiğin altına düşmüyor, yani "sayfa yok"
durumunu asla yakalamıyor. `Document` altı ise −1 → 1 → 29 diye keskin ayırıyor. Ayrıca
`WM_GETOBJECT` dürtmesi şart: dürtme gönderilmeden ağaç 7 saniye boyunca 57 öğede kaldı.

Erken çıkış "durağan **ve** dolu" olmayı bekler. Sadece durağanlığa bakmak yetmiyor: ağaç
0.1–0.9 s arasında 76'dan 91'e gidip bir süre öyle kalıyor, bu da "oturdu" gibi görünüyor ama
sayfa daha gelmemiş. Buna karşılık gerçekten seyrek pencereler (görev çubuğu gibi) bekleme
yapmaz — bir ara sürümde her okuma 2.67 s sürüyordu, şimdi 0.04 s.

**Desteklenmeyen desenlerin özellikleri okunmaz.** UIA, elemanın desteklemediği bir özelliği
sorduğunda `None` değil *varsayılan* döndürüyor. Kontrol edilmezse her metin düğümü `belirsiz`
durumlu ve `deger="0.0"` görünüyor, hatta adsız düğümler `"0.0"` diye adlanıyordu. Ölçüldü:
aynı 700 karakterlik bütçede 9 öğe yerine 14 öğe sığıyor.

**Girdi kilidi `BlockInput` değil, düşük seviye kanca** (`WH_KEYBOARD_LL`, `WH_MOUSE_LL`).
`BlockInput` acil durdurma kısayolunu da yutar (onu sadece Ctrl+Alt+Del çözer); kanca ise
olayın fiziksel mi `SendInput` mı olduğunu ayırt ediyor — kullanıcınınki yutulur, PcGöz'ünkü
geçer. Kısayolu kanca kendisi yakalıyor (kilit `RegisterHotKey`'i de yutuyor). Sadece *basma*
ve hareket yutulur, *bırakma* geçer: kilit başladığında Shift basılıysa ya da fare düğmesi
basılı sürükleniyorsa bırakması yutulsa tuş takılı kalırdı. Kanca ayrı iş parçacığında ve
sadece kilit süresince kurulu — Python'daki bir kanca her fare olayında GIL bekler, boşta
kurulu durup sistemi yavaşlatmasın. Kurma/kaldırma < 1 ms. Canlı ölçüldü: `smoke.py` boyunca
kullanıcının 427 fiziksel olayı yutuldu, test yarıda kesilmedi. (Tuzak: `GetModuleHandleW`'nin
dönüş tipi verilmezse 64 bit tanıtıcı kırpılıyor ve `SetWindowsHookEx` WinError 126 veriyor.)

**Fare koordinatı tavan yuvarlamasıyla normalize ediliyor.** `SendInput` mutlak koordinatı
0..65535; Windows geri çevirirken aşağı yuvarlıyor (`piksel = sol + n·genişlik/65536`). Eski
formül (`round(x·65535/(w−1))`) imleci konumların ~%10'unda 1 px yanlış yere götürüyordu
(ölçüldü: `SendInput` + `GetCursorPos`, x'te 7/68, y'de 3/39); `(x·65536 + w − 1) // w` ile 0.

**Aramalar Türkçe-duyarlı ve aksansız** (`textutil.fold`). `casefold` Türkçede yetmiyor:
`"İptal".casefold()` noktalı `i̇ptal` veriyor — `pc_find("iptal")` "İptal" düğmesini, pencere
sorgusu "kayıt" "KAYIT"ı bulamıyordu. `pc_find`, `pc_wait_for`, pencere sorguları ve OCR
araması aynı katlamayı kullanıyor.

Metin `KEYEVENTF_UNICODE` ile yazılıyor (`wVk=0`, `wScan=kod noktası`), `VkKeyScan` ile değil —
bu yol aktif klavye düzeninden bağımsız, yani Türkçe Q, Türkçe F, US fark etmeksizin
`ğüşıöçİĞ` doğru çıkıyor.

## Test Paketi Notları

```bash
uv run python tests/smoke.py
```

Not Defteri açıp içinde çalışır (**fare ve klavyeyi ~20 saniye kullanır; bu sürede fare ve
klavye kilitlidir**), sonunda kaydetmeden kapatır. Türkçe karakter round-trip'i, ref kutularının
hizası, bayat ref reddi dahil 16 kontrol. Kullanıcı verisine dokunmamak için: Not Defteri öne
alınamazsa hiçbir şey göndermez; açılan pencere boş ve tek sekmeli değilse (önceki oturum geri
yüklendiyse) ona sadece "yeni pencere" gönderir; süreci öldürmez, kendi yazdığını silip
pencereyi `WM_CLOSE` ile kapatır. Nasıl biterse bitsin (acil durdurma dahil) temizliği klavye
kullanmadan (`WM_SETTEXT`) yapar.

```bash
uv run python tests/kilit_canli.py
```

**Kullanıcıyla birlikte:** 3 sn sonra fare ve klavye 8 sn kilitlenir; o sırada fareyi oynat, tuşlara
bas. Kaç fiziksel olayın yutulduğunu ve imlecin kıpırdayıp kıpırdamadığını söyler. Fiziksel
girdinin yutulduğu `SendInput` ile ölçülemez (programla gönderilen her olay kilitten geçer);
yutma kararının mantığı `birim.py`'de sahte olaylarla da test ediliyor.

```bash
uv run python tests/kapsam.py
```

Girdi göndermez. Açık uygulamaların erişilebilirlik kapsamı, UWP inişi, tarayıcı soğuk açılışı,
acil durdurma, parola bloğu (gerçek bir parola kutusunda, `pc_do` üzerinden), ekran görüntüsü
kutularının okumadan sonra değişen pencereyi izlemesi, "ne değişti" özeti (etkili ve etkisiz
`invoke` ile), iki okumada aynı ref'lerin verilmesi, pencere yönetimi (görev çubuğu koruması
DRY_RUN altında), donmuş pencere (ayrı süreçte 12 sn dondurulur, ~13 sn sürer) ve OCR (bulunan
merkezin Win32'den alınan düğme dikdörtgeninin içinde olması). Test pencereleri en üstte açılır
ama odak çalmaz; pencere yönetimi testi büyütme sırasında ekranı yarım saniye kaplar.
Tarayıcı testi **geçici profilli ayrı bir Brave** açar (kullanıcının Brave'ine, geçmişine
dokunmaz; sonunda yalnızca o profilin süreçleri beklenir ve klasör silinir; Brave yoksa test atlanır).
UWP testi yalnızca kendi açtığı Ayarlar penceresini `WM_CLOSE` ile kapatır — eskiden `ApplicationFrameHost`'u
`taskkill` ile öldürüyordu, bu kullanıcının bütün UWP pencerelerini kapatırdı.

```bash
uv run python tests/agir_sayfa.py
```

Girdi göndermez. Geçici profilli ayrı bir Brave'de 1500 satırlık yerel bir sayfa açar ve okuma
araçlarının süresini ölçer: soğuk ve ılık `pc_find`, `pc_read`, `pc_window close`. Doğruluğu
da denetler: sayfa UIA ile sona kaydırılınca son satır `pc_find` ve `pc_read`'de görünmeli
(Brave yoksa çalıştırılamaz ve 1 döner).
Tarayıcı yardımcıları `tests/_tarayici.py`'de; `kapsam.py` de onları kullanıyor.

```bash
uv run python tests/ocr_olcum.py
```

Girdi göndermez; birkaç saniye en üstte bir test penceresi görünür. OCR'ın büyütme ölçeğini ve
yöntemini seçmek için iki ölçüm yapar:
- gerçek pencere: bilinen metinli Edit ve düğmeler, 3 yöntem × 1/2/3×;
- sentetik sayfa: karakter hata oranı ve tam ekran süresi.

`ocr.SCALE` ya da `ocr.RESAMPLE` değiştirilecekse önce bunu çalıştır.

```bash
uv run python tests/birim.py
```

Girdi göndermez, pencere açmaz — kullanıcı bilgisayar başındayken de güvenle çalışır. Kısayol
ayrıştırma (üretilen tuş aynı düzende geri çevrilince aynı karakteri vermeli), parola alanına
tuş izin listesi, ekran görüntüsü ölçeği, kalıcı ref kuralları, "ne değişti" özetinin metni,
`space="screenshot"` çevirisi, iş parçacığı güvenlik ağı (zaman aşımına uğrayan istek sonradan
çalışmıyor mu), OCR araması, Türkçe arama katlaması, ağacın metne çevrilmesi, çok ekranlı
koordinat dönüşümü (her piksel sütunu), `--stats` özeti, girdi kilidi kararları (fiziksel olay
yutulur, PcGöz'ünki geçer, bırakma geçer, kilitliyken acil durdurma çalışır, süre dolunca
kalkar), acil dondurmanın süreçler arası ortaklığı (ikinci bir süreç dondurmayı görüyor ve
çözebiliyor), kısayolun sahibi bırakınca geri alınması (kullanılmayan F21/F22 ile).

```bash
uv run python tests/pano.py
```

Girdi göndermez; panoyu kullanır ama başta saklayıp sonda geri koyar. Resim ve özel biçimlerin
korunması, geç okuyan hedef (0 / 400 / 1000 ms), Win+V geçmişi dışlaması, zaman aşımı.

```bash
uv run python tests/tus_probu.py
```

**Girdi gönderir (~2 sn), ama sadece kendi penceresine** — her gönderimden önce ön plandaki
pencerenin kendisi olduğunu kontrol eder. Uygulamaya hangi tuşun *ulaştığını* ölçer (araya
giren kancaları yakalar), gerçek `ctrl+v` ile bir Edit'e yapıştırmayı dener ve `pc_do type`'ın
hedefini ve yeni değerini doğru bildirdiğini, salt okunur kutuda "değer değişmedi" uyarısını
verdiğini kontrol eder. Son olarak imleci ~110 noktaya götürüp her birinde tam o piksele
düştüğünü ölçer (tıklamaz, imleci eski yerine koyar). Girdi kilidinin gerçek kancalarla
kurulup kalktığını ve kilitliyken PcGöz'ün kendi tuşunun ulaştığını da dener. Odak kilidi
pencereyi öne almayı engellerse kendi başlık çubuğuna tıklar — sadece o nokta gerçekten kendi
penceresiyse.

```bash
uv run python tests/mcp_istemci.py
```

Girdi göndermez, pencere açmaz. Sunucuyu Claude Code gibi stdio üzerinden başlatıp protokolle
konuşur — diğer testler fonksiyonları doğrudan çağırdığı için şu iki arızayı göremez: sunucunun
hiç açılmaması ve hata metninin modele ulaşmaması. Yedek modu da import'u bilerek bozarak dener.

**Tarayıcı testleri hakkında iki kural** — ikisi de hata yapıldıktan sonra yazıldı:

1. **Pencereyi `windows.close_window` ile kapat, süreci öldürme.** Chromium'da her pencerenin
   `window_pid`'i *ana tarayıcı sürecini* gösterir; o pid'e `taskkill` atmak kullanıcının bütün
   sekmelerini kapatır.
2. **Test tarayıcısını `--user-data-dir` ile ayrı profilde çalıştır.** Ayrı süreç demek, test
   pencerelerini kullanıcınınkilerden pid ile kesin ayırmak demek. Başlığa bakarak ayırmaya
   çalışmak kırılgan: `"MİLYON".upper()` ASCII `"MILYON"` ile eşleşmediği için bir eleme
   filtresi sessizce çalışmadı ve yanlış pencere kapatıldı.
