---
name: masaustu-kullanimi
description: Windows masaüstünü PcGöz ile görme ve kullanma. Ekranda ne olduğunu okumak, pencere/menü bulmak, tıklamak, yazmak gerektiğinde kullan — "şu programda şuna bak", "şuraya tıkla", "ekranda ne yazıyor", "şu uygulamayı aç ve şunu yap" gibi isteklerde. Ayrıca geliştirdiğin bir masaüstü uygulamasını (WinForms/WPF/WinUI/Electron/Qt/Win32) derleyip çalıştırdıktan sonra arayüzünü doğrulamak için — kullanıcı istemese de. Sadece Windows.
---

# Masaüstünü görme ve kullanma

## Ne zaman PcGöz, ne zaman başka araç

**PcGöz'e uzan:**
- Kullanıcı açık bir uygulamadan bahsediyor: "şu pencerede ne yazıyor", "şu ayarı değiştir",
  "bu program neden takıldı".
- **Bir masaüstü uygulaması geliştirdin ya da değiştirdin.** Derleyip çalıştırdıktan sonra
  arayüzüne bak: pencere açıldı mı, düğmeler ve metinler doğru mu, tıklayınca ne oluyor,
  hata iletişim kutusu çıktı mı. Derlemenin geçmesi arayüzün çalıştığını göstermez; bunu
  kullanıcıya bırakma. `pc_read` + `pc_invoke` çoğu zaman ekran görüntüsünden ucuzdur.
- Sadece arayüzden yapılabilen Windows işleri (bazı Ayarlar sayfaları, kurulum sihirbazları).

**Başka araç daha iyi:**
- **Web sayfasının içeriği** → tarayıcı araçları (uygulama içi tarayıcı ya da Chrome). Sayfaya
  DOM düzeyinde erişirler; PcGöz'ün gördüğü, tarayıcının erişilebilirlik ağacıdır.
- **Dosya, kayıt defteri, süreç, servis** → kabuk. Aynı bilgi için pencereye bakma.

Araçlar `mcp__pcgoz__*` önekiyle gelir ve **ertelenmiştir** — ilk kullanımdan önce `ToolSearch`
ile yükle, hepsini tek çağrıda:

```
ToolSearch: select:mcp__pcgoz__pc_windows,mcp__pcgoz__pc_read,mcp__pcgoz__pc_screenshot,mcp__pcgoz__pc_find,mcp__pcgoz__pc_text,mcp__pcgoz__pc_do,mcp__pcgoz__pc_invoke,mcp__pcgoz__pc_batch,mcp__pcgoz__pc_wait_for,mcp__pcgoz__pc_clipboard,mcp__pcgoz__pc_focus,mcp__pcgoz__pc_window,mcp__pcgoz__pc_ocr
```

Temel akış: `pc_windows` → `pc_read` → `pc_invoke` / `pc_do`. Birkaç adımı önceden
biliyorsan (tıkla, yaz, enter) `pc_batch` ile tek çağrıda çalıştır.

## Okuma ipuçları

- `pc_read`/`pc_find` sadece **ekranda görünen** öğeleri verir. Aşağıda kalan için kaydır
  (`pc_do scroll`) ve tekrar oku.
- `pc_find` ad, değer ve AutomationId içinde arar. Bir kutunun içeriğini ("24.09.2026"),
  açılır kutunun seçimini (`secim="..."`) doğrudan bulursun.
- Uygulamayı açmak için PcGöz'de araç yok; kabukla başlat (`Start-Process`), sonra
  `pc_windows` ile bul.

## Tıklamadan önce `pc_invoke` dene

`pc_invoke` UIA desenini kullanır: fareyi oynatmaz, odağı çalmaz, pencere örtülüyken de çalışır
ve araya bir ipucu balonu girse bile yanlış şeye gitmez. `pc_do left_click` geri düşüş yolu.
İstisna: tarayıcıda (Chromium) web düğmesine `invoke` pencereyi öne alabilir.

## Boş ağacı "içerik yok" sanma

`pc_read` çıktısında **"içerik alanında sadece N öğe var"** uyarısı varsa, gördüğün ağaç eksik
olabilir — sayfa hâlâ yükleniyor ya da uygulama erişilebilirlik bilgisi vermiyor olabilir.
O ağaca bakıp "ekranda bir şey yok" sonucuna varma. `pc_screenshot` ile **gözle** doğrula;
gerçekten boşsa koordinatla çalış, yükleniyorsa `pc_wait_for` ile bekle.

## Menüler ayrı pencerelerdir

Sağ tık menüsü ve açılır listeler ayrı birer üst düzey penceredir. `pc_windows` bunları
**"AÇIK MENÜ"** başlığı altında ayrı gösterir; `pc_read`'i hwnd vermeden çağırırsan açık menüyü
kendiliğinden okur ve bunu söyler. Menüyü kapatıp açtıysan ref'ler değişir — yeniden oku.
(Aynı öğe ekranda kaldıkça her okumada aynı ref'i alır; değişen, yeniden oluşturulan öğelerdir.)

Kaydetme/onay dialogları her zaman ayrı pencere değildir; bazı uygulamalarda pencere *içinde*
bir içerik dialogu olarak gelir. İkisi de normal `pc_read` ile bulunur.

## Sonucu doğrula, niyeti değil

`pc_do` ve `pc_invoke` sonuçta ekranda ne değiştiğini söyler: açılan pencere/menü, odak,
yeni/değişen öğeler (ref'leriyle). Önce onu oku; çoğu zaman ayrıca `pc_read` gerekmez.

- **"Görünür değişiklik yok"** dediyse eylem etkisiz kalmıştır. Aynı şeyi tekrarlama: `invoke`
  ise `pc_do left_click` dene, tıklama ise hedefi yeniden oku.
- `type` sonucundaki **"Hedef:"** satırı metnin nereye gittiğini, **"değer DEĞİŞMEDİ"** uyarısı
  tutmadığını söyler. Uyarı geldiyse tekrar yazmadan önce odağı düzelt.
- Özet sadece hedef pencereyi izler. Kritik sonuçları (kaydedildi mi, gönderildi mi) yine
  `pc_text` ya da `pc_screenshot` ile doğrula.

## Türkçe metin ve uzun yazı

Yazma klavye düzeninden bağımsızdır, `ğüşıöçİĞ` doğru çıkar. 200 karakterin üstü otomatik
olarak pano üzerinden yapıştırılır. Uygulamanın her tuşa tek tek tepki vermesi gerekiyorsa
(arama kutusu, otomatik tamamlama) `paste=False` ile zorla.

Yapıştırma sonucu hedefin metni okuyup okumadığını söyler. "OKUMADI" uyarısı geldiyse odak
büyük ihtimalle metin alanında değildi — `pc_text` ile bak, tekrar yapıştırmadan önce odağı düzelt.
Kullanıcının panosu her durumda geri konur.

## Ekran görüntüsü koordinatları

Ekran model sınırına sığıyorsa (1920x1080 sığar) resim küçültülmez: sonuç "Ölçek 1" diyorsa
resimdeki (x, y) doğrudan `pc_do` koordinatıdır. Aksi hâlde (pencere yakalaması, bölge,
`scale`) hesap yapma: `pc_do(coordinate=[x, y], space="screenshot")` ver, çeviriyi araç yapar.

## Pencereler ve donmuş uygulamalar

- `pc_focus` Windows odak kilidine takılırsa başlık çubuğuna kendisi tıklar ("odak kilidi
  vardı" der). Yine de "öne ALINAMADI" derse başlık örtülüdür: `key`/`type` yanlış pencereye
  gider — hedefte görünen bir yere `pc_do left_click` yap ya da kullanıcıdan iste.
- Kapat / küçült / büyüt / taşı için `pc_window`. `close` X düğmesidir — kaydedilmemiş iş varsa
  uygulamanın sorusu açılır, sonuçta görünür. Süreci öldürmek için `taskkill` KULLANMA.
- "yanıt vermiyor" hatası: uygulama donmuş ya da çok meşgul. Araç beklemeden döner; birkaç
  saniye sonra tekrar dene, sürerse kullanıcıya söyle. `pc_windows` böyle pencereleri
  **YANIT VERMİYOR** diye işaretler.

## Tarayıcı işlerinde

- Chromium'da her pencerenin süreç kimliği **ana tarayıcı sürecidir**. Tek pencere kapatmak
  için `taskkill` KULLANMA — kullanıcının bütün sekmelerini kapatır. Pencereyi kapatma
  mesajıyla kapat.
- Soğuk bir tarayıcıda sayfa ağacı pencere göründükten ~2.5 sn sonra gelir. `pc_read` bunu
  kendisi bekler, ama ilk okuma birkaç saniye sürebilir.

## Yapamayacakları

- **Yönetici hakkıyla çalışan pencereler** (UIPI) — Claude Code normal haklarla çalışıyorsa
  okunamaz, tıklanamaz; yönetici olarak başlatıldıysa erişilir (`--doctor` "Yonetici" satırı).
  Yükseltilmiş bir oturum bile UAC istemini çözmez: o güvenli masaüstünde çizilir, hiçbir süreç
  oraya erişemez. "GÜVENLİ MASAÜSTÜ açık" hatası gelirse tekrar deneme; kullanıcıya UAC/kilit
  ekranını yanıtlamasını söyle. Yükseltilmiş bir kabuktan tarayıcı başlatma — Brave öyle açılınca çöküyor.
- **Parola alanları** — yazma, değer atama ve karakter/yapıştırma tuşları sert şekilde
  engellidir; sadece gezinme tuşları (tab, enter, esc, oklar) geçer. Kullanıcının kendisi girmeli.
- **UIA vermeyen uygulamalar** (oyun, bazı Qt arayüzleri, canvas, uzak masaüstü) — `pc_ocr`
  yazıyı ekran koordinatıyla verir; `find="kaydet"` tıklanacak kelimenin merkezini bulur, merkezi
  `pc_do coordinate` ile ver. OCR hata yapabilir ve simgeleri görmez; `pc_screenshot` ile doğrula.

## Kullanıcı fareyi kullanıyorsa

Eylem araçları kullanıcı o an fareyi/klavyeyi kullanıyor olsa da **reddetmez**: kullanıcının
fiziksel girdisi eylem ve izleme boyunca **kilitlenir**, ekranın sol üstünde yarı saydam bir uyarı
çıkar (Ctrl+Alt+Shift+Esc yine çalışır). Kullanıcı Ctrl/Alt gibi bir tuşu basılı tutuyorsa araç
bırakmasını kısa süre bekler, gerekirse kendisi bırakır. `force` parametresi artık etkisiz.

Kullanıcı aktifken **hedefi `ref` ile ver ya da önce `pc_focus`**: odaktaki pencere kullanıcının
yazdığı pencere olabilir, hedefsiz `key`/`type` oraya gider.

"Başka bir PcGöz oturumu çalışıyor" hatası gelirse başka bir Claude oturumu o an masaüstünde
eylem yapıyordur (aynı anda tek oturum girdi sürebilir); birkaç saniye sonra tekrar dene. Acil durdurma o anda açık **bütün** PcGöz oturumlarını durdurur; "DONDURULDU" hatası
geldiyse kendin çözemezsin, hatadaki kısayolu kullanıcıya söyle. `pc_batch` kilidi bütün toplu iş boyunca tutar, yani çok adımlı bir işi kullanıcı
adım aralarında bölemez. Uzun işlerde kullanıcıya önceden söyle: fare birkaç saniye
kıpırdamayacak.

## Bozulursa

Sunucu açılamazsa **yedek modda** açılır: araçlar görünür ama her çağrı "PcGoz BAŞLATILAMADI"
diye arızayı anlatır. Tekrar deneme; kullanıcıya söyle ve teşhis komutunu öner:

```bash
<PcGoz klasörü>\.venv\Scripts\python.exe -m pcgoz --doctor
```

`--doctor` kaydın hâlâ doğru yeri gösterip göstermediğini de kontrol eder. Önbellek bozulduysa
`--repair`. Araçlar hiç görünmüyorsa (`CONNECTION_CLOSED`) venv ya da kayıt bozuktur — yine
`--doctor`. Onarımdan sonra Claude Code yeniden başlatılmalı.
