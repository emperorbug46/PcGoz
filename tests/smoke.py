"""Uctan uca duman testi. Elle calistirilir: uv run python tests/smoke.py

Her adim belirli bir riski kapatiyor; hangi riski kapattigi adimin yaninda yazili.
Not Defteri aciyor, iceriginde oynuyor ve sonunda kaydetmeden kapatiyor.

Kullanici verisine dokunmamak icin uc sart (2026-09-23'te eklendi):
- Not Defteri one alinamazsa hicbir sey gonderilmez. pc_focus basarisiz
  olursa yazilan metin ve ctrl+a/delete o an ondeki pencereye -- or. Claude'un
  mesaj kutusuna -- gider.
- Acilan pencerede birden fazla sekme ya da bos olmayan belge varsa test
  durur: Windows 11 Not Defteri onceki oturumun sekmelerini geri yukleyebilir,
  ctrl+a/delete onlari silerdi.
- Kapatirken surec OLDURULMEZ: Not Defteri tek surecte birden cok pencere
  tutuyor, taskkill kullanicinin diger pencerelerini de kapatirdi. Test kendi
  yazdigini silip pencereyi WM_CLOSE ile kapatir.
"""

from __future__ import annotations

import ctypes

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from pcgoz import config, inputguard, patterns, server, uia, windows, worker  # noqa: E402
from pcgoz.refs import StaleRef  # noqa: E402
import _gunluk  # noqa: E402,F401 -- gercek denetim gunlugunu kirletme

OUT = Path(__file__).parent / "_ciktilar"
TURKISH = "Merhaba ğüşıöç İĞÜŞÖÇ — pcgöz testi 123"

passed: list[str] = []
failed: list[str] = []

#: Testin actigi pencereler -- test nasil biterse bitsin cleanup() bunlari toplar.
_opened: dict[str, int] = {"test": 0, "restored": 0, "held": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    mark = "OK  " if ok else "HATA"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))


def wait_until_user_idle(seconds: float = 2.0, timeout: float = 120.0) -> None:
    """Kullanici klavyeden elini cekene kadar bekle.

    Bu test gercek fare ve klavyeyi suruyor, ustelik ctrl+a/delete gonderiyor.
    Kullanici bir seyler yazarken baslarsa yazdiginin ustune gider. Once
    bekliyoruz -- girdi kuyrugu paylasilan bir kaynak.
    """
    from pcgoz import safety

    deadline = time.monotonic() + timeout
    warned = False
    while time.monotonic() < deadline:
        idle = safety.human_idle_ms()
        if idle is None or idle >= seconds * 1000:
            if warned:
                print("     ...devam ediliyor")
            return
        if not warned:
            print(f"     kullanici klavyeyi kullaniyor -- {seconds:.0f} sn bosalmayi bekliyorum")
            warned = True
        time.sleep(0.3)
    print("     UYARI: bekleme suresi doldu, yine de basliyorum")


def _clear_editor(hwnd: int) -> None:
    """Pencerenin duzenleyicisini klavye kullanmadan bosalt (WM_SETTEXT).

    Test yazdiktan sonra yarida kalirsa (or. kullanici klavyeye dokundu ve
    UserBusy ile durdu) metin Not Defteri oturumunda kalir ve bir sonraki
    acilista kullanicinin karsisina cikardi.
    """
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    editors: list[int] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(child, _lparam):  # noqa: ANN001, ANN202
        if "Edit" in windows.window_class(child):          # RichEditD2DPT, Edit
            editors.append(child)
        return True

    user32.EnumChildWindows(ctypes.c_void_p(hwnd), proc(collect), 0)
    empty = ctypes.create_unicode_buffer("")
    result = ctypes.c_size_t()
    for editor in editors:
        user32.SendMessageTimeoutW(ctypes.c_void_p(editor), 0x000C, 0, empty,   # WM_SETTEXT
                                   0x0002, 1000, ctypes.byref(result))


def cleanup() -> None:
    """Test nasil biterse bitsin: kendi penceresini BOSALTIP kapat, geri yuklenen
    pencereyi icerigiyle birak. Klavye/fare kullanmaz -- kullanici o an klavyede
    olsa da guvenle calisir."""
    if _opened["held"]:
        inputguard.guard.release()
        _opened["held"] = 0
        print(f"     kilit kalkti ({inputguard.guard.swallowed} fiziksel olay yutuldu)")
    test, restored = _opened["test"], _opened["restored"]
    if test and windows.is_window(test):
        _clear_editor(test)
        time.sleep(0.2)
        closed = windows.close_window(test)
        print(f"     temizlik: test penceresi bosaltildi ve {'kapatildi' if closed else 'KAPANMADI'}")
    if restored and windows.is_window(restored):
        closed = windows.close_window(restored)
        print(f"     temizlik: geri yuklenen pencere icerigiyle {'kapatildi' if closed else 'KAPANMADI'}")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    print("PcGoz duman testi\n" + "=" * 60)
    print("Bu test fareyi ve klavyeyi ~20 saniye kullanacak.")
    wait_until_user_idle()
    # Test boyunca kullanicinin FIZIKSEL girdisi tutulur (acil durdurma yine
    # calisir): adim aralarinda fareye dokunmak testi yarida kesmesin.
    if config.BLOCK_USER_INPUT:
        inputguard.guard.acquire(config.BLOCK_BATCH_MAX_S)
        _opened["held"] = 1
        print("     fare/klavye test boyunca kilitli -- Ctrl+Alt+Shift+Esc ile durdurulur")

    print("\n1) Not Defteri aciliyor")
    def notepads() -> list:  # noqa: ANN202
        return windows.find_window("Not Defteri") or windows.find_window("Notepad")

    # Sadece BU testin actigi pencere: kullanicinin acik Not Defteri'ne dokunma.
    # (Windows 11'de pid baslaticiya ait, pencerenin sahibi degil -- pid'le secilemez.)
    existing = {w.hwnd for w in notepads()}
    subprocess.Popen(["notepad.exe"])
    time.sleep(1.8)

    hwnd = 0
    for _ in range(20):
        matches = [w for w in notepads() if w.hwnd not in existing]
        if matches:
            hwnd = matches[0].hwnd
            break
        time.sleep(0.3)
    check("pc_windows Not Defteri'ni buldu", bool(hwnd), f"hwnd={hwnd}")
    if not hwnd:
        return 1
    _opened["test"] = hwnd

    def focus(target: int) -> bool:
        server.pc_focus(hwnd=target)
        time.sleep(0.4)
        return windows.foreground_window() == target

    def fresh(target: int) -> tuple[bool, str]:
        tree = worker.call(uia.snapshot_window, target)
        tabs = [n for n in tree.walk() if n.role == "TabItem"]
        document = next((n for n in tree.walk()
                         if n.role in ("Document", "Edit") and "Text" in n.patterns), None)
        # read_text degil: belge bosken ogenin ADINA dusuyor ("Metin duzenleyici",
        # 17 karakter) ve bos pencereyi dolu sandiriyordu (2026-09-23).
        content = (worker.call(patterns._text_pattern_text, document) or "") if document else ""
        return (len(tabs) <= 1 and not content.strip(),
                f"{len(tabs)} sekme, belge {len(content.strip())} karakter")

    if not focus(hwnd):
        check("Not Defteri one alindi", False, "hicbir sey gonderilmedi")
        worker.call(windows.close_window, hwnd)
        return 1

    # Windows 11 Not Defteri onceki oturumun sekmelerini geri yukleyebilir.
    # Oyleyse ona sadece ctrl+shift+n (yeni pencere) gonder ve testi o bos
    # pencerede yap.
    restored = 0
    ok, detail = fresh(hwnd)
    if not ok:
        print(f"     onceki oturum geri yuklendi ({detail}) -- ona dokunmadan yeni pencere aciliyor")
        restored = hwnd
        _opened.update(restored=restored, test=0)
        existing.add(restored)
        server.pc_do(action="key", key="ctrl+shift+n", observe=False)
        hwnd = 0
        for _ in range(20):
            time.sleep(0.3)
            new = [w for w in notepads() if w.hwnd not in existing]
            if new:
                hwnd = new[0].hwnd
                break
        _opened["test"] = hwnd
        if not hwnd or not focus(hwnd):
            check("bos test penceresi acildi", False, "acilmadi ya da one alinamadi")
            worker.call(windows.close_window, restored)
            return 1
        ok, detail = fresh(hwnd)
    check("test penceresi bos ve tek sekmeli (kullanici verisi yok)", ok, detail)
    if not ok:
        print("     bos pencere bulunamadi -- hicbir seye dokunmadan duruyorum")
        for target in (hwnd, restored):
            if target:
                worker.call(windows.close_window, target)
        return 1

    # --- 2) Agac okuma -----------------------------------------------------
    print("\n2) Erisilebilirlik agaci")
    tree_text = server.pc_read(hwnd=hwnd)
    (OUT / "notepad_agac.txt").write_text(tree_text, encoding="utf-8")
    check("agac okundu", len(tree_text) > 50, f"{len(tree_text)} karakter")
    check("duzenleme alani gorundu",
          any(word in tree_text for word in ("Edit", "Document")),
          "Edit/Document dugumu var")

    # --- 3) Unicode girdi yolu --------------------------------------------
    print("\n3) Turkce karakter yazma  (KEYEVENTF_UNICODE dogru mu)")
    server.pc_do(action="type", text=TURKISH)
    time.sleep(0.6)

    # UIA uzerinden oku
    via_uia = server.pc_text(hwnd=hwnd)
    check("pc_text belge icerigini okudu (baslik degil)", TURKISH in via_uia,
          f"okunan: {via_uia[:80]!r}" if TURKISH not in via_uia else f"{len(via_uia)} karakter")

    # Isletim sistemi uzerinden birebir dogrula: Ctrl+A, Ctrl+C, panoyu oku.
    # Konsol kod sayfasi Turkce karakterleri bozdugu icin karsilastirma burada
    # yapiliyor, ekrana basilan seye bakarak degil.
    server.pc_do(action="key", key="ctrl+a")
    time.sleep(0.2)
    server.pc_do(action="key", key="ctrl+c")
    time.sleep(0.4)
    via_clip = server.pc_clipboard(action="get")
    exact = via_clip.strip() == TURKISH
    check("pano round-trip birebir esit", exact)
    (OUT / "yazilan_metin.txt").write_text(
        f"beklenen: {TURKISH!r}\npanodan  : {via_clip.strip()!r}\nuia      : {via_uia[:200]!r}\n",
        encoding="utf-8",
    )
    if not exact:
        print("       karsilastirma tests/_ciktilar/yazilan_metin.txt icinde")

    # --- 4) Tus gonderimi --------------------------------------------------
    print("\n4) Tus gonderimi")
    server.pc_do(action="key", key="ctrl+a")
    time.sleep(0.2)
    server.pc_do(action="key", key="delete")
    time.sleep(0.3)
    after = server.pc_text(hwnd=hwnd)
    check("ctrl+a / delete calisti", TURKISH not in after)

    # --- 5) Pano yolu ------------------------------------------------------
    print("\n5) Pano ile yapistirma")
    long_text = "satir bir\nsatir iki: ığüşöç\nsatir uc"
    server.pc_do(action="type", text=long_text, paste=True)
    time.sleep(0.6)
    pasted = server.pc_text(hwnd=hwnd)
    check("pano ile yapistirma calisti", "ığüşöç" in pasted,
          f"okunan: {pasted[:60]!r}")
    check("cok satirli yapistirma bozulmadi", "satir uc" in pasted)

    # --- 6) Ekran goruntusu + etiketleme (DPI kaniti) ----------------------
    print("\n6) Ekran goruntusu ve ref kutulari")
    shot = server.pc_screenshot(hwnd=hwnd, annotate=True)
    note, image = shot[0], shot[1]
    (OUT / "notepad.png").write_bytes(image.data)
    check("pencere yakalandi", len(image.data) > 5000, f"{len(image.data)} bayt PNG")
    check("kutular cizildi", "numaralandi" in note, note.splitlines()[-2] if note else "")
    print("       " + "\n       ".join(note.splitlines()))

    # Tam ekran da al: kirpma/olcek dogru mu gozle bakilacak.
    full = server.pc_screenshot(annotate=True)
    (OUT / "tam_ekran.png").write_bytes(full[1].data)

    # --- 7) Bayat ref (GUVENLIK) ------------------------------------------
    print("\n7) Bayat ref testi  (pencere kapaninca eski koordinata tiklamamali)")
    found = server.pc_find(query="Edit", hwnd=hwnd)
    ref = None
    for token in found.replace("[", " ").replace("]", " ").split():
        if token.startswith("ref_"):
            ref = token
            break
    check("test icin ref alindi", bool(ref), str(ref))

    # Sureci OLDURME: Windows 11 Not Defteri tek surecte birden cok pencere
    # tutuyor, taskkill kullanicinin diger pencerelerini de kapatirdi. Kendi
    # yazdigimizi sil (bos belge "kaydet?" diye sormaz), sonra WM_CLOSE.
    server.pc_do(action="key", key="ctrl+a", observe=False)
    server.pc_do(action="key", key="delete", observe=False)
    time.sleep(0.3)
    closed = worker.call(windows.close_window, hwnd)
    check("test icin pencere gercekten kapandi (WM_CLOSE)", closed and not windows.is_window(hwnd))
    if restored:
        # Geri yuklenen pencere: Not Defteri icerigi oturumda sakliyor, WM_CLOSE
        # soru sormadan kapatiyor ve icerik bir sonraki acilista yine geliyor.
        check("geri yuklenen pencere icerigiyle birakildi",
              worker.call(windows.close_window, restored))

    if ref:
        try:
            server.pc_do(action="left_click", ref=ref)
            check("kapali pencerenin ref'ine tiklama reddedildi", False,
                  "TIKLADI -- guvenlik hatasi!")
        except StaleRef as exc:
            check("kapali pencerenin ref'ine tiklama reddedildi", True,
                  str(exc).split("--")[0].strip()[:70])
        except Exception as exc:  # noqa: BLE001
            check("kapali pencerenin ref'ine tiklama reddedildi", True,
                  f"{type(exc).__name__}: {str(exc)[:60]}")

    # --- 8) Hata mesajlari anlasilir mi -----------------------------------
    print("\n8) Hata yollari")
    try:
        server.pc_do(action="key", key="ctrl+kirikTus")
        check("anlamsiz tus adi reddedildi", False, "kabul etti")
    except ValueError as exc:
        check("anlamsiz tus adi reddedildi", True, str(exc)[:60])

    try:
        server.pc_read(hwnd=999999)
        check("olmayan hwnd reddedildi", False, "kabul etti")
    except LookupError as exc:
        check("olmayan hwnd reddedildi", True, str(exc)[:60])

    print("\n" + "=" * 60)
    print(f"{len(passed)} gecti, {len(failed)} kaldi")
    if failed:
        for name in failed:
            print(f"  KALDI: {name}")
    print(f"Ciktilar: {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    from pcgoz import safety

    try:
        code = main()
    except safety.Frozen:
        print("\nDURDU -- acil durdurma kisayoluna basildi.")
        code = 1
    except safety.UserBusy as exc:
        print(f"\nDURDU -- kullanici klavye/fareyi kullanmaya basladi: {str(exc)[:70]}")
        code = 1
    finally:
        cleanup()
    sys.exit(code)
