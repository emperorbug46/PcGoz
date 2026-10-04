"""Uygulama kapsami ve guvenlik mekanizmasi testleri.

Elle calistirilir: uv run python tests/kapsam.py
Fare ve klavye kullanmaz. Kendi test pencerelerinde UIA eylemleri ve pencere
yonetimi yapar (en ustte acilir, odak calmaz); test_uwp Ayarlar'i, test_browser
_coldstart gecici profilli ayri bir Brave acar (kullanicinin Brave'ine dokunmaz;
sonunda profil silinir).
"""

from __future__ import annotations

import ctypes

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import re  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from pcgoz import config, safety, server, uia, windows, worker  # noqa: E402
from pcgoz.refs import registry  # noqa: E402
import _gunluk  # noqa: E402,F401 -- gercek denetim gunlugunu kirletme
import _tarayici  # noqa: E402

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  [{'OK  ' if ok else 'HATA'}] {name}" + (f" -- {detail}" if detail else ""))


def test_coverage() -> None:
    """Acik olan her pencereyi oku, hangi uygulama turunun ne verdigini gor."""
    print("\n1) Acik pencerelerde erisilebilirlik kapsami")
    infos = worker.call(windows.list_windows)
    interesting = [w for w in infos if not w.minimized and w.width > 200]
    if not interesting:
        check("okunacak pencere var", False, "acik pencere yok")
        return

    uwp_seen = False
    for w in interesting:
        try:
            tree = worker.call(uia.snapshot_window, w.hwnd)
            count = tree.count()
            kind = ""
            if w.cls == "ApplicationFrameWindow":
                kind = " [UWP]"
                uwp_seen = True
                # Gercek icerik ApplicationFrameHost'un altindaki CoreWindow'da;
                # inis calisiyorsa agacta o sinifi gormeliyiz.
                has_core = any("CoreWindow" in n.cls for n in tree.walk())
                kind += " CoreWindow'a inildi" if has_core else " CoreWindow YOK"
            elif w.cls.startswith("Chrome_WidgetWin"):
                kind = " [Chromium/Electron]"
            print(f"     {w.process:24s} {count:5d} oge{kind}  -- {w.title[:34]}")
            check(f"{w.process} okundu", count >= 1, f"{count} oge")
        except Exception as exc:  # noqa: BLE001
            check(f"{w.process} okundu", False, f"{type(exc).__name__}: {str(exc)[:60]}")

    if not uwp_seen:
        print("     (UWP penceresi acik degil -- ApplicationFrameHost inisi test edilemedi)")


def test_uwp() -> None:
    """Ayarlar'i acip UWP inisini dogrula."""
    print("\n2) UWP (Ayarlar) -- ApplicationFrameHost inisi")

    def settings_windows() -> dict[int, str]:
        # Baslik "Ayarlar"/"Settings"; sinif ApplicationFrameWindow. Sadece sinifa
        # bakmak kullanicinin acik Hesap Makinesi'ni de yakalardi.
        return {w.hwnd: w.title for w in worker.call(windows.list_windows)
                if w.cls == "ApplicationFrameWindow" and w.title in ("Ayarlar", "Settings")}

    before = settings_windows()
    subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:"], shell=False)
    hwnd = 0
    for _ in range(25):
        time.sleep(0.4)
        new = [h for h in settings_windows() if h not in before]
        if new:
            hwnd = new[0]
            break
    ours = bool(hwnd)
    if not hwnd and before:
        hwnd = next(iter(before))          # zaten aciktti; okunur ama kapatilmaz
        print("     Ayarlar zaten acikti -- o pencere okunacak, kapatilmayacak")
    if not hwnd:
        check("Ayarlar acildi", False, "pencere bulunamadi")
        return
    time.sleep(1.5)

    try:
        tree = worker.call(uia.snapshot_window, hwnd)
        count = tree.count()
        check("Ayarlar okundu", count > 20, f"{count} oge")

        text = server.pc_text(hwnd=hwnd)   # arac kendi worker.call'ini yapar
        check("Ayarlar metni alindi", len(text) > 40, f"{len(text)} karakter")

        info = windows.describe(hwnd)
        print(f"     sinif={info.cls} surec={info.process}")
    finally:
        # Eskiden: taskkill /F window_pid. ApplicationFrameWindow'un sureci
        # ApplicationFrameHost -- oldurmek kullanicinin BUTUN UWP pencerelerini
        # (Hesap Makinesi, Fotograflar...) kapatirdi. Sadece kendi penceremiz, WM_CLOSE.
        if ours:
            # UWP cercevesi kapaninca yok edilmek yerine gizlenebiliyor.
            closed = worker.call(windows.close_window, hwnd) or hwnd not in settings_windows()
            check("Ayarlar WM_CLOSE ile kapandi", closed)


def test_browser_coldstart() -> None:
    """Tarayici penceresi acilir acilmaz sayfa icerigi geliyor mu?

    Gercek bir hatanin testi: erisilebilirlik tembel aciliyor, pencere hazir
    gorunurken agac bos geliyordu ve "sayfada bir sey yok" diye yanlis cevap
    uretiliyordu. Eski esik (toplam < 25 oge) hic tetiklenmiyordu cunku
    tarayici cercevesi tek basina 57-74 oge.

    Yerel dosya kullaniliyor: internet gerekmesin, icerik degismesin.
    """
    print("\n5) Tarayici soguk acilis (erisilebilirlik uyandirma)")
    if not _tarayici.BRAVE.exists():
        print("     Brave kurulu degil, atlaniyor")
        return

    page = Path(__file__).parent / "fixtures" / "sayfa.html"
    # Gecici profilli ayri Brave: eskiden kullanicinin acik Brave'inde pencere
    # aciliyordu (gecmisine, oturumuna karisarak). Ayri profil ayrica gercekten soguk.
    with _tarayici.GeciciBrave(page.as_uri(), "soguk acilis") as brave:
        check("test penceresi acildi", bool(brave.hwnd),
              f"hwnd={brave.hwnd}" if brave.hwnd else "pencere bulunamadi")
        if brave.hwnd:
            _coldstart_checks(brave.hwnd)
    if brave.hwnd:
        check("test penceresi WM_CLOSE ile kapandi", brave.closed)
    check("gecici tarayici profili silindi", brave.profile_removed)


def _coldstart_checks(hwnd: int) -> None:
    # ASIL TEST: pencere gorunur gorunmez oku, bekleme koymadan.
    t0 = time.perf_counter()
    out = server.pc_read(hwnd=hwnd, filter="all", max_chars=40000)
    elapsed = time.perf_counter() - t0

    tree = worker.call(uia.snapshot_window, hwnd)
    content = uia.content_element_count(tree)
    print(f"     okuma {elapsed:.1f}s surdu, icerik alaninda {content} oge")

    check("sayfa icerigi geldi", content >= 20, f"{content} oge (>=20 bekleniyor)")
    for parca in ("Kaydet", "birinci baglanti", "Form ogeleri"):
        check(f"{parca!r} agacta var", parca in out)
    check("bos-agac uyarisi verilmedi", "icerik alaninda sadece" not in out)
    check("looks_unpopulated dolu sayfada False", not uia.looks_unpopulated(tree))


def test_freeze() -> None:
    """Acil dondurma girdiyi gercekten kesiyor mu."""
    print("\n3) Acil dondurma")
    from pcgoz import input as pin

    st = safety.hotkey_status()
    # Kayitli MCP sunucusu calisirken kisayolu o tutuyor; bu testin ayri
    # surecinde tumu dolu cikabilir. Mekanizmanin bozuk olmasi ile baskasinin
    # tutuyor olmasi ayri seyler -- ikincisi hata degil.
    taken = not st["registered"] and "1409" in str(st.get("error") or "")
    check("kisayol kayitli (ya da baska bir ornek tutuyor)",
          bool(st["registered"]) or taken,
          st.get("combo") or ("calisan sunucu tutuyor" if taken else str(st.get("error"))))
    if st.get("note"):
        print(f"     not: {st['note']}")

    safety._frozen.set()
    try:
        try:
            server.pc_do(action="left_click", coordinate=[10, 10])
            check("donmusken tiklama reddedildi", False, "TIKLADI")
        except safety.Frozen:
            check("donmusken tiklama reddedildi", True)

        try:
            server.pc_do(action="type", text="olmamali")
            check("donmusken yazma reddedildi", False, "YAZDI")
        except safety.Frozen:
            check("donmusken yazma reddedildi", True)

        # Okuma engellenmemeli -- dondurma girdiyi durdurur, gormeyi degil.
        try:
            server.pc_windows()
            check("donmusken okuma calismaya devam etti", True)
        except Exception as exc:  # noqa: BLE001
            check("donmusken okuma calismaya devam etti", False, str(exc)[:50])
    finally:
        safety._frozen.clear()

    # Yazma ORTASINDA dondurma: uzun bir metin yazarken bayrak kalkarsa
    # kalan karakterler gonderilmemeli.
    print("     yazma ortasinda kesme denemesi (girdi gonderilmiyor, DRY_RUN)")
    original = config.DRY_RUN
    config.DRY_RUN = False
    sent = {"count": 0}
    real_send = pin._send

    def counting_send(*inputs):  # noqa: ANN001, ANN202
        sent["count"] += len(inputs)
        # Gercekten gondermiyoruz -- sadece sayiyoruz.

    pin._send = counting_send
    try:
        def freeze_soon() -> None:
            time.sleep(0.35)
            safety._frozen.set()

        threading.Thread(target=freeze_soon, daemon=True).start()
        try:
            pin.type_text("x" * 400)
            check("yazma ortasinda kesildi", False, "sonuna kadar yazdi")
        except safety.Frozen:
            check("yazma ortasinda kesildi", True,
                  f"{sent['count'] // 2} karakterden sonra durdu (400 yerine)")
    finally:
        pin._send = real_send
        safety._frozen.clear()
        config.DRY_RUN = original


def test_password_guard() -> None:
    """Parola alani blogu -- onaya degil, karara bagli olmayan sert sinir."""
    print("\n4) Parola alani blogu")
    try:
        safety.guard_text_target(is_password=True, process_name="notepad.exe", label="Edit \"parola\"")
        check("parola alanina yazma reddedildi", False, "izin verdi")
    except safety.Refused as exc:
        check("parola alanina yazma reddedildi", True, str(exc)[:56])

    try:
        safety.guard_text_target(is_password=False, process_name="consent.exe", label="UAC")
        check("UAC penceresine yazma reddedildi", False, "izin verdi")
    except safety.Refused as exc:
        check("UAC penceresine yazma reddedildi", True, str(exc)[:56])

    try:
        safety.guard_text_target(is_password=False, process_name="notepad.exe", label="normal")
        check("normal alana yazma serbest", True)
    except safety.Refused:
        check("normal alana yazma serbest", False, "gereksiz yere engelledi")


def test_password_guard_live() -> None:
    """Parola blogu gercek bir parola kutusunda, arac zinciri uzerinden.

    Yukaridaki test sadece karar fonksiyonunu deniyor; bu, pc_do'nun onu
    gercekten cagirdigini. Eskiden 'key' eylemi parola kontrolu yapmiyordu:
    pc_clipboard set + key 'ctrl+v' ile parola girilebiliyordu.

    Girdi gondermez: DRY_RUN acik, engel calismasa bile tiklama ve tus
    sadece loglanir. Pencere en ustte acilir ama odagi calmaz.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from _pencere import ES_PASSWORD, TestWindow

    print("\n6) Parola blogu -- gercek parola kutusu, pc_do uzerinden")
    window = TestWindow("PcGoz parola testi", topmost=True, noactivate=True,
                        edits=(("normal", 0), ("", ES_PASSWORD)))
    original = config.DRY_RUN
    config.DRY_RUN = True
    try:
        time.sleep(0.3)
        tree = worker.call(uia.snapshot_window, window.hwnd)
        edits = [n for n in tree.walk() if n.role == "Edit"]
        password = next((n for n in edits if n.password), None)
        normal = next((n for n in edits if not n.password), None)
        check("UIA parola kutusunu tanidi", password is not None and normal is not None,
              f"{len(edits)} Edit, parola={sum(n.password for n in edits)}")
        if password is None or normal is None:
            return
        pw_ref = worker.call(registry.register, password, window.hwnd)
        normal_ref = worker.call(registry.register, normal, window.hwnd)

        def outcome(**kwargs) -> str:  # noqa: ANN003
            try:
                return server.pc_do(force=True, **kwargs)
            except safety.Refused as exc:
                return f"REDDEDILDI: {exc}"

        for combo in ("ctrl+v", "shift+insert", "a"):
            got = outcome(action="key", key=combo, ref=pw_ref)
            check(f"parola kutusuna key {combo!r} reddedildi", got.startswith("REDDEDILDI"), got[:60])
        got = outcome(action="type", text="gizli", ref=pw_ref)
        check("parola kutusuna type reddedildi", got.startswith("REDDEDILDI"), got[:60])
        got = outcome(action="key", key="tab", ref=pw_ref)
        check("parola kutusunda tab serbest", "DRY RUN" in got, got[:60])
        got = outcome(action="key", key="ctrl+v", ref=normal_ref)
        check("normal kutuda ctrl+v serbest", "DRY RUN" in got, got[:60])
    finally:
        config.DRY_RUN = original
        window.close()


def test_annotation_follows_changes() -> None:
    """Ekran goruntusu kutulari son okumadan sonraki degisikligi izliyor mu?

    Kutular son pc_read'in agacindan ciziliyor. Eskiden arada pencere
    kaydiysa kutular eski konumlara ciziliyordu; model de o kutulara bakip
    tiklama noktasi seciyor. Girdi gondermez: pencere programla tasinir.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from _pencere import WM_CLOSE, TestWindow, user32

    print("\n7) Ekran goruntusu kutulari -- okumadan sonra degisen pencere")
    window = TestWindow("PcGoz kutu testi", topmost=True, noactivate=True,
                        rect=(100, 100, 420, 180), edits=(("bir", 0), ("iki", 0)))
    try:
        time.sleep(0.3)
        server.pc_read(hwnd=window.hwnd)
        edits = [n for n in server._last["tree"].walk() if n.role == "Edit" and n.ref]
        check("iki kutu okundu", len(edits) == 2, f"{len(edits)}")
        if len(edits) != 2:
            return
        before = edits[0].rect

        swp_nosize_nozorder_noactivate = 0x0001 | 0x0004 | 0x0010
        user32.SetWindowPos(window.hwnd, None, 400, 250, 0, 0, swp_nosize_nozorder_noactivate)
        user32.PostMessageW(window.children[1], WM_CLOSE, 0, 0)
        time.sleep(0.3)

        note = server.pc_screenshot(hwnd=window.hwnd)[0]
        after = edits[0].rect
        moved = (after[0] - before[0], after[1] - before[1])
        check("kutu pencereyle birlikte tasindi", moved == (300, 150), f"kayma {moved}")
        check("kapanan kutu bildirildi", "1 oge son okumadan beri kayboldu" in note,
              next((line for line in note.splitlines() if "kayboldu" in line), "(bildirilmedi)"))
    finally:
        window.close()


def test_effects_and_stable_refs() -> None:
    """Eylem sonucu ne degistigini soyluyor mu; ayni oge ayni ref'i aliyor mu?

    Gunlukteki iki israfin testi: her eylemden sonra ayrica okuma yapiliyordu,
    ve Invoke'un hicbir sey yapmadigi fark edilmeyip ayni butona tekrar
    tiklaniyordu. Fare/klavye kullanmaz -- pc_invoke UIA deseniyle calisir,
    hedef de kendi test penceremiz.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from _pencere import WM_COMMAND, TestWindow, kernel32, user32

    print("\n8) Eylem sonucu ozeti ve kalici ref'ler")
    holder: dict[str, object] = {}

    def handler(hwnd, msg, wparam, lparam):  # noqa: ANN001, ANN202
        window = holder.get("w")
        if msg == WM_COMMAND and window and lparam == window.buttons[0]:
            user32.SetWindowTextW(window.children[0], "degisti")
            user32.CreateWindowExW(0, "STATIC", "Kaydedildi", 0x50000000, 250, 10, 120, 24,
                                   hwnd, None, kernel32.GetModuleHandleW(None), None)
            return 0
        return None          # ikinci dugme: hicbir sey yapma

    window = TestWindow("PcGoz etki testi", handler=handler, topmost=True, noactivate=True,
                        rect=(140, 140, 480, 200), edits=(("ilk", 0),),
                        buttons=("Degistir", "Etkisiz"))
    holder["w"] = window
    try:
        time.sleep(0.3)
        server.pc_read(hwnd=window.hwnd, filter="all")
        first = {n.ref for n in server._last["tree"].walk() if n.ref}
        server.pc_read(hwnd=window.hwnd, filter="all")
        second = {n.ref for n in server._last["tree"].walk() if n.ref}
        check("ikinci okuma ayni ref'leri verdi", first == second and bool(first),
              f"{len(first)} ref, ortak {len(first & second)}")

        tree = server._last["tree"]
        buttons = {n.name: n.ref for n in tree.walk()      # baslik cubugu dugmeleri haric
                   if n.role == "Button" and n.ref and n.name in ("Degistir", "Etkisiz")}
        edit = next((n.ref for n in tree.walk() if n.role == "Edit" and n.ref), None)
        check("dugmeler ve kutu okundu", len(buttons) == 2 and edit is not None, str(buttons))
        if len(buttons) != 2 or edit is None:
            return

        out = server.pc_invoke(ref=buttons["Degistir"], action="invoke", force=True)
        print("     " + out.replace("\n", "\n     "))
        check("degisen deger bildirildi", 'deger "ilk" -> "degisti"' in out)
        check("yeni oge ref'iyle bildirildi",
              any("Kaydedildi" in line and "[ref_" in line for line in out.splitlines()))

        out = server.pc_invoke(ref=buttons["Etkisiz"], action="invoke", force=True)
        check("etkisiz Invoke 'degisiklik yok' dedi", "Gorunur degisiklik yok" in out)
        check("ve tiklamayi onerdi", "pc_do left_click" in out)

        out = server.pc_invoke(ref=edit, action="value", value="elle", force=True)
        check("value eyleminin sonucu bildirildi", 'deger "degisti" -> "elle"' in out,
              next((line for line in out.splitlines() if "deger" in line), "(yok)").strip())
    finally:
        window.close()


def test_window_management() -> None:
    """pc_window: tasi / kucult / geri getir / buyut / kapat + kabuk korumasi.

    Girdi gondermez; hedef kendi test penceremiz (odak calmaz). Buyutme ekrani
    yarim saniye kaplar.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from _pencere import TestWindow, user32

    print("\n9) Pencere yonetimi")
    window = TestWindow("PcGoz pencere testi", topmost=True, noactivate=True,
                        rect=(160, 160, 400, 200))
    hwnd = window.hwnd
    try:
        time.sleep(0.3)
        out = server.pc_window(action="move", hwnd=hwnd, rect=[300, 260, 520, 240],
                               force=True, observe=False)
        frame = windows.frame_rect(hwnd)
        check("gorunur cerceve istenen yere geldi", frame == (300, 260, 820, 500),
              f"{frame} -- {out.splitlines()[0][-50:]}")

        for action, probe, expected in (("minimize", user32.IsIconic, True),
                                        ("restore", user32.IsIconic, False),
                                        ("maximize", user32.IsZoomed, True),
                                        ("restore", user32.IsZoomed, False)):
            server.pc_window(action=action, hwnd=hwnd, force=True, observe=False)
            time.sleep(0.4)
            check(f"{action}", bool(probe(hwnd)) == expected)

        out = server.pc_window(action="close", hwnd=hwnd, force=True)
        check("close pencereyi kapatti", not windows.is_window(hwnd))
        check("ozet kapanmayi bildirdi", "kapandi" in out,
              next((line for line in out.splitlines() if "kapandi" in line), "(yok)")[:70])
    finally:
        window.close()

    # DRY_RUN altinda: engel calismasa bile gorev cubuguna gercekten WM_CLOSE
    # gitmesin ("Windows'u kapat" iletisim kutusu acilirdi). Engel DRY_RUN
    # kontrolunden once geldigi icin test yine anlamli.
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    original = config.DRY_RUN
    config.DRY_RUN = True
    try:
        out = server.pc_window(action="close", hwnd=tray, force=True)
        check("gorev cubugu reddedildi", False, f"reddetmedi: {out[:50]}")
    except PermissionError as exc:
        check("gorev cubugu reddedildi", True, str(exc)[:60])
    finally:
        config.DRY_RUN = original


def test_hung_window() -> None:
    """Donmus bir pencere butun araclari kilitliyor mu?

    Eskiden evet: butun araclar tek iplikte sirayla calisiyor ve donmus
    pencereye UIA cagrisi 20.7 sn, PrintWindow 14.7 sn -- pencere kendine
    gelene kadar -- bekledi (gercekten donmus uygulamada sonsuz). Ayri bir
    surecte pencere acip 12 sn donduruyoruz. Girdi gondermez, ~13 sn surer.
    """
    print("\n10) Donmus pencere")
    helper = Path(__file__).parent / "_askida_pencere.py"
    proc = subprocess.Popen([sys.executable, str(helper), "12"], stdout=subprocess.PIPE, text=True)
    hwnd = int(proc.stdout.readline())

    def timed(fn):  # noqa: ANN001, ANN202
        started = time.monotonic()
        try:
            return fn(), time.monotonic() - started
        except Exception as exc:  # noqa: BLE001 -- beklenen hata da sonuc
            return exc, time.monotonic() - started

    try:
        time.sleep(0.4)
        server.pc_read(hwnd=hwnd, filter="all")
        button = next(n.ref for n in server._last["tree"].walk()
                      if n.role == "Button" and n.name == "Dugme")
        ctypes.windll.user32.PostMessageW(hwnd, 0x8000, 0, 0)      # pencereyi dondur
        hung_at = time.monotonic()
        time.sleep(0.3)

        out, took = timed(lambda: server.pc_read(hwnd=hwnd))
        check("pc_read beklemeden 'yanit vermiyor' dedi",
              isinstance(out, uia.NotResponding) and took < 1.5, f"{took:.2f} sn")
        out, took = timed(lambda: server.pc_screenshot(hwnd=hwnd))
        check("pc_screenshot beklemeden dondu ve durumu soyledi",
              isinstance(out, list) and took < 1.5 and "yanit vermiyor" in out[0], f"{took:.2f} sn")
        out, took = timed(lambda: server.pc_invoke(ref=button, action="invoke", force=True))
        check("pc_invoke beklemeden 'yanit vermiyor' dedi",
              isinstance(out, uia.NotResponding) and took < 1.5, f"{took:.2f} sn")
        out, took = timed(server.pc_windows)
        check("diger araclar calismaya devam etti", isinstance(out, str) and took < 1.5,
              f"{took:.2f} sn")

        time.sleep(max(0.0, 5.6 - (time.monotonic() - hung_at)))   # Windows 5 sn bekler
        listing = server.pc_windows().splitlines()
        check("pc_windows 'YANIT VERMIYOR' isaretledi",
              any(f"hwnd={hwnd} " in line and "YANIT VERMIYOR" in line for line in listing))

        time.sleep(max(0.0, 12.6 - (time.monotonic() - hung_at)))
        out, took = timed(lambda: server.pc_read(hwnd=hwnd))
        check("pencere kendine gelince yine okundu", isinstance(out, str), f"{took:.2f} sn")
    finally:
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
        proc.wait(20)


def test_ocr() -> None:
    """pc_ocr gercek bir pencerede yaziyi ve tiklanacak yeri dogru buluyor mu?

    Dogrulama UIA'dan bagimsiz: dugmelerin ekran dikdortgeni Win32'den
    (GetWindowRect) aliniyor ve OCR'in verdigi merkez onun icinde olmali.
    Girdi gondermez.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from ctypes import wintypes

    from _pencere import TestWindow, user32

    print("\n11) OCR")
    window = TestWindow("PcGoz OCR testi", topmost=True, noactivate=True,
                        rect=(220, 220, 420, 150), buttons=("Kaydet", "Şifre Değiştir"))
    try:
        time.sleep(0.4)
        out = server.pc_ocr(hwnd=window.hwnd)
        check("pencere yazisi okundu", "Kaydet" in out, out.splitlines()[0][-40:])

        for button, needle in zip(window.buttons, ("kaydet", "sifre degistir")):
            box = wintypes.RECT()
            user32.GetWindowRect(button, ctypes.byref(box))
            out = server.pc_ocr(hwnd=window.hwnd, find=needle)
            # Satir sonunda "[belirsiz: ...]" eki olabilir; sadece merkezi al.
            centers = re.findall(r"merkez=\((-?\d+),(-?\d+)\)", out)
            inside = [(int(x), int(y)) for x, y in centers
                      if box.left <= int(x) <= box.right and box.top <= int(y) <= box.bottom]
            check(f"{needle!r} bulundu, merkez dugmenin ustunde", len(inside) == 1,
                  f"merkezler {centers}, dugme ({box.left},{box.top})-({box.right},{box.bottom})")
    finally:
        window.close()


def test_turkish_find() -> None:
    """Turkce arayuzde arama: "İptal" dugmesi "iptal" ile bulunuyor mu?

    casefold ile bulunmuyordu: "İptal".casefold() noktali "i̇ptal" veriyor.
    Pencere sorgusu da ayni hataya sahipti. Girdi gondermez.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from _pencere import TestWindow

    print("\n12) Turkce arama")
    window = TestWindow("PcGöz arama testi", topmost=True, noactivate=True,
                        rect=(260, 260, 420, 120), buttons=("İptal", "KAYIT"))
    try:
        time.sleep(0.3)
        for query, wanted in (("iptal", 'Button "İptal"'), ("kayıt", 'Button "KAYIT"')):
            out = server.pc_find(query=query, hwnd=window.hwnd)
            check(f"pc_find {query!r} -> {wanted}", wanted in out, out.splitlines()[0][-30:])
        out = server.pc_windows(query="pcgoz arama")
        check("pencere sorgusu 'pcgoz arama' -> 'PcGöz arama testi'", f"hwnd={window.hwnd}" in out)
    finally:
        window.close()


def main() -> int:
    print("PcGoz kapsam + guvenlik testleri\n" + "=" * 60)
    safety.start_hotkey()
    test_coverage()
    test_uwp()
    test_browser_coldstart()
    test_freeze()
    test_password_guard()
    test_password_guard_live()
    test_annotation_follows_changes()
    test_effects_and_stable_refs()
    test_window_management()
    test_hung_window()
    test_ocr()
    test_turkish_find()
    print("\n" + "=" * 60)
    print(f"{len(passed)} gecti, {len(failed)} kaldi")
    for name in failed:
        print(f"  KALDI: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
