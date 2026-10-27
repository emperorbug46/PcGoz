"""Birim testleri. Elle calistirilir: uv run python tests/birim.py

Girdi gondermez, pencere acmaz, fareye/klavyeye dokunmaz. Diger testlerin
aksine kullanici bilgisayar basindayken de guvenle calisir.
"""

from __future__ import annotations

import ctypes

# Sunucu gibi: DPI-farkinda olmayan surece Windows sanallastirilmis olcu verir.
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

from ctypes import wintypes  # noqa: E402
from typing import Any  # noqa: E402

from pcgoz import input as pin  # noqa: E402
from pcgoz import safety  # noqa: E402
import _gunluk  # noqa: E402,F401 -- gercek denetim gunlugunu kirletme

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.ToUnicodeEx.argtypes = (
    wintypes.UINT, wintypes.UINT, ctypes.c_char_p, wintypes.LPWSTR,
    ctypes.c_int, wintypes.UINT, ctypes.c_void_p,
)
user32.GetKeyboardLayout.restype = ctypes.c_void_p

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    mark = "OK  " if ok else "HATA"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))


def _produces(mods: list[int], vk: int, layout: int) -> str:
    """Bu tus + degistiriciler bu duzende hangi karakteri uretir?"""
    state = (ctypes.c_ubyte * 256)()
    for mod in mods:
        state[mod] = 0x80
    buf = ctypes.create_unicode_buffer(8)
    scan = user32.MapVirtualKeyExW(vk, 0, ctypes.c_void_p(layout))
    # wFlags=4: klavye durumunu degistirme (olu tuslar sonraki cagriyi bozmasin)
    n = user32.ToUnicodeEx(vk, scan, ctypes.cast(state, ctypes.c_char_p), buf, 8, 4, layout)
    return buf.value[:n] if n > 0 else ""


def test_parse_combo() -> None:
    print("parse_combo")
    layout = user32.GetKeyboardLayout(0)
    print(f"  (duzen: {layout:#x})")
    # Her karakter icin: uretilen tus + degistiriciler ayni duzende geri
    # cevrildiginde ayni karakteri vermeli. Duzene ozel VK degeri sabitlenmiyor.
    for char in "a1?!:;_\"'@#{}[]$ş":
        try:
            mods, vk = pin.parse_combo(char, layout=layout)
        except pin.KeyParseError as exc:
            check(f"{char!r} ayristi", False, str(exc)[:60])
            continue
        got = _produces(mods, vk, layout)
        names = "+".join({0x10: "shift", 0x11: "ctrl", 0x12: "alt"}[m] for m in mods)
        check(f"{char!r} dogru tusa cevrildi", got == char,
              f"{names + '+' if names else ''}vk=0x{vk:02X} -> {got!r}")

    mods, vk = pin.parse_combo("ctrl+s", layout=layout)
    check("ctrl+s", (mods, vk) == ([0x11], ord("S")))
    mods, vk = pin.parse_combo("alt+F4", layout=layout)
    check("alt+F4", (mods, vk) == ([0x12], 0x73))
    mods, vk = pin.parse_combo("shift+?", layout=layout)
    check("shift+? shift'i iki kez eklemedi", mods.count(0x10) == 1, str(mods))
    for bad in ("", "hyper+x", "nosuchkey"):
        try:
            pin.parse_combo(bad, layout=layout)
            check(f"{bad!r} reddedildi", False, "kabul etti")
        except pin.KeyParseError:
            check(f"{bad!r} reddedildi", True)


def test_key_guard() -> None:
    print("parola alanina tus engeli")
    layout = user32.GetKeyboardLayout(0)

    def verdict(combo: str, *, password: bool = True, process: str = "chrome.exe") -> bool:
        mods, vk = pin.parse_combo(combo, layout=layout)
        try:
            safety.guard_key_target(mods, vk, is_password=password,
                                    process_name=process, label="Edit \"Parola\"")
            return True
        except safety.Refused:
            return False

    # Engelin eskiden delindigi yollar: yapistirma ve tus tus karakter.
    for combo in ("ctrl+v", "shift+insert", "ctrl+shift+v", "win+v", "a", "shift+a",
                  "1", "?", "@", "space", "ctrl+alt+q"):
        check(f"parola alaninda {combo!r} reddedildi", not verdict(combo))
    for combo in ("tab", "shift+tab", "enter", "esc", "backspace", "delete",
                  "home", "shift+end", "left", "ctrl+backspace", "ctrl+a"):
        check(f"parola alaninda {combo!r} serbest", verdict(combo))
    check("normal alanda ctrl+v serbest", verdict("ctrl+v", password=False))
    check("kimlik bilgisi arayuzunde 'a' reddedildi",
          not verdict("a", password=False, process="CredentialUIBroker.exe"))
    check("kimlik bilgisi arayuzunde esc serbest",
          verdict("esc", password=False, process="CredentialUIBroker.exe"))


def test_screenshot_scale() -> None:
    print("ekran goruntusu olcegi (model goruntu siniri)")
    from PIL import Image

    from pcgoz import capture, config

    edge, pixels = config.SCREENSHOT_MAX_EDGE, config.SCREENSHOT_MAX_PIXELS

    def sent(w: int, h: int, scale: float | None = None) -> tuple[tuple[int, int], float]:
        _, size, factor = capture.to_png(Image.new("RGB", (w, h)), scale=scale)
        return size, factor

    def within(size: tuple[int, int]) -> bool:
        return max(size) <= edge and size[0] * size[1] <= pixels

    size, factor = sent(1920, 1080)
    check("1920x1080 kucultulmeden gitti (koordinat 1:1)", size == (1920, 1080) and factor == 1.0,
          f"{size}, olcek {factor}")
    for w, h, what in ((3840, 1080, "iki ekran yan yana"), (3840, 2160, "4K"),
                       (2560, 1600, "uzun kenar icinde, alan disinda")):
        size, factor = sent(w, h)
        check(f"{what} {w}x{h} sinira indi", within(size) and factor < 1, f"{size}, olcek {factor:.3f}")
        check(f"{what}: bildirilen olcek gercek boyutla tutarli",
              abs(w * factor - size[0]) < 1 and abs(h * factor - size[1]) < 1.5)
    size, factor = sent(1920, 1080, scale=2.0)
    check("scale=2 sinirda kesildi", within(size) and 1 < factor < 2, f"{size}, olcek {factor:.3f}")
    size, factor = sent(400, 300, scale=2.0)
    check("kucuk bolgede scale=2 aynen uygulandi", size == (800, 600), f"{size}")


def _node(role: str, name: str, rid: tuple[int, ...] | None, *, value: str = "",
          toggle: int | None = None, top: int = 0, focusable: bool = True):  # noqa: ANN202
    from pcgoz.uia import Node
    return Node(el=None, role=role, name=name, value=value, aid="", cls="",
                rect=(0, top, 100, top + 20), enabled=True, offscreen=False,
                focusable=focusable, password=False, pid=1, hwnd=0,
                patterns=frozenset({"Value"}) if value else frozenset(),
                toggle=toggle, selected=False, expand=None, rid=rid)


def test_stable_refs() -> None:
    print("kalici ref'ler")
    from pcgoz.refs import Registry

    reg = Registry()
    first = reg.register(_node("Button", "Kaydet", (42, 7)), hwnd=100)
    again = reg.register(_node("Button", "Kaydet", (42, 7)), hwnd=100)
    check("ayni oge ayni ref'i aldi", first == again, f"{first} / {again}")
    other = reg.register(_node("Button", "Iptal", (42, 8)), hwnd=100)
    check("baska oge yeni ref aldi", other != first, other)
    reused = reg.register(_node("Edit", "Arama", (42, 7)), hwnd=100)
    check("kimlik baska role gecmisse yeni ref", reused not in (first, other), reused)
    elsewhere = reg.register(_node("Button", "Kaydet", (42, 7)), hwnd=200)
    check("baska penceredeki ayni kimlik yeni ref", elsewhere not in (first, other, reused), elsewhere)
    no_id = {reg.register(_node("Text", "x", None), hwnd=100) for _ in range(2)}
    check("kimliksiz oge her seferinde yeni ref", len(no_id) == 2, str(sorted(no_id)))


def test_effect_summary() -> None:
    print("'ne degisti' ozeti")
    from pcgoz import effects

    def scene(nodes, *, wins=None, fg=1, focus=None):  # noqa: ANN001, ANN202
        return effects.Scene(target=1, fg=fg, windows=wins if wins is not None else {1: 'app "Belge"'},
                             focus=focus, nodes=nodes if nodes is None else {n.rid: n for n in nodes})

    edit = _node("Edit", "Ad", (1,), value="ilk")
    save = _node("Button", "Kaydet", (2,))
    before = scene([edit, save])

    text, changed = effects.describe(before, scene([edit, save]), 0.3, True)
    check("degisiklik yoksa soyledi", not changed and text.startswith("Gorunur degisiklik yok"), text)

    after = scene([_node("Edit", "Ad", (1,), value="yeni\r\nsatir"), save,
                   _node("Text", "Kaydedildi", (3,), top=40)])
    text, changed = effects.describe(before, after, 0.4, True, register=lambda n: "ref_9")
    check("sayilar dogru", "1 yeni, 0 kayboldu, 1 degisti" in text, text.splitlines()[1] if changed else text)
    check("yeni oge ref'iyle", '+ Text "Kaydedildi" [ref_9]' in text)
    check("deger degisimi tek satirda", 'deger "ilk" -> "yeni\\nsatir"' in text)
    check("ozet satir sayisi bozulmadi", len(text.splitlines()) == 4, str(len(text.splitlines())))

    text, _ = effects.describe(before, scene([edit, save], wins={1: 'app "Belge"', 5: 'app "Farkli Kaydet"'},
                                             fg=5), 0.5, True)
    check("acilan pencere ve on plan bildirildi",
          'acildi: hwnd=5 app "Farkli Kaydet"' in text and "on plan:" in text)
    text, _ = effects.describe(before, scene(None, wins={}), 0.2, True)
    check("hedefin kapanmasi bildirildi", "kapandi ya da artik okunamiyor" in text
          and 'kapandi: hwnd=1 app "Belge"' in text)
    text, _ = effects.describe(before, scene([edit, save]), 1.2, False)
    check("durulmayan ekran degisiklik sayilmadi",
          text.startswith("Gorunur degisiklik yok"), text)

    text, _ = effects.describe(before, scene(None, wins={}, fg=0), 0.3, True)
    check("on plan bosalinca hwnd=0 yazilmadi",
          "(on planda pencere yok)" in text and "hwnd=0" not in text, text.splitlines()[1])

    # Gorev Yoneticisi sekme degisimi: ayni buton yeni kimlikle yeniden cizildi.
    run_old, run_new = _node("Button", "Calistir", (7,)), _node("Button", "Calistir", (8,))
    text, _ = effects.describe(scene([edit, run_old]), scene([edit, run_new, save]), 0.3, True,
                               register=lambda n: "ref_4")
    check("yeniden cizilen oge 'kayboldu' listesinde yok",
          '- Button "Calistir"' not in text and "(yeniden olusturuldu)" in text
          and '+ Button "Kaydet" [ref_4]' in text and "(yeniden olusturuldu)" not in
          [line for line in text.splitlines() if "Kaydet" in line][0], text)

    from pcgoz import server
    merged = server._merge_text(["[1/2] a", "[2/2] b", b"resim", "not"])
    check("toplu is adimlari ayri satirlarda birlesti",
          merged == ["[1/2] a\n\n[2/2] b", b"resim", "not"], repr(merged))


def test_screenshot_space() -> None:
    print("space='screenshot' koordinat cevirisi")
    from pcgoz import server

    server._last_shot.clear()
    try:
        server._point_for(None, [10, 10], "screenshot")
        check("ekran goruntusu yokken reddetti", False, "kabul etti")
    except ValueError:
        check("ekran goruntusu yokken reddetti", True)

    # Pencere yakalamasi: sol-ust (200, 100), yarim olcek, 800x450 gonderilmis.
    server._last_shot.update(origin=(200, 100), factor=0.5, size=(800, 450), label="test")
    x, y, where = server._point_for(None, [100, 50], "screenshot")
    check("resim (100,50) -> ekran (400,200)", (x, y) == (400, 200), where)
    x, y, _ = server._point_for(None, [100, 50], "screen")
    check("space='screen' dokunulmadi", (x, y) == (100, 50))
    try:
        server._point_for(None, [800, 10], "screenshot")
        check("resim disi reddedildi", False, "kabul etti")
    except ValueError as exc:
        check("resim disi reddedildi", True, str(exc)[:50])
    server._last_shot.clear()


def test_worker_timeout() -> None:
    print("is parcacigi guvenlik agi")
    import threading
    import time

    from pcgoz import worker

    original = worker.CALL_TIMEOUT_S
    worker.CALL_TIMEOUT_S = 0.4
    ran_late: list[str] = []
    try:
        # 1) Takilan is: cagiran zaman asiminda doner, sonuc "belirsiz" denir.
        blocker: dict[str, BaseException] = {}

        def stuck_caller() -> None:
            try:
                worker.call(time.sleep, 1.2)
            except TimeoutError as exc:
                blocker["e"] = exc

        caller = threading.Thread(target=stuck_caller)
        caller.start()
        time.sleep(0.1)

        # 2) Arkasinda kuyruga giren is: zaman asimina ugrar ve SONRADAN CALISMAZ.
        started = time.monotonic()
        try:
            worker.call(ran_late.append, "kuyruktaki is")
            check("kuyruktaki is zaman asimina ugradi", False, "bekleyip calisti")
        except TimeoutError as exc:
            check("kuyruktaki is zaman asimina ugradi", time.monotonic() - started < 1.0,
                  f"{time.monotonic() - started:.2f} sn")
            check("'calistirilmadi' dedi", "calistirilmadi" in str(exc))

        caller.join()      # cagiran zaman asiminda doner; is hala suruyor
        check("takilan is 'belirsiz' dedi", "belirsiz" in str(blocker.get("e", "")))
        time.sleep(1.0)    # takilan is bitsin, kuyruktaki iptal edilmis is gecilsin
        check("iptal edilen is sonradan calismadi", not ran_late, str(ran_late))
        check("is parcacigi sonra yine calisiyor", worker.call(lambda: 42) == 42)
    finally:
        worker.CALL_TIMEOUT_S = original


def test_wait_for_outside_worker() -> None:
    print("pc_wait_for: yoklama dongusu UIA ipliginin disinda")
    import threading
    import time
    from types import SimpleNamespace

    from pcgoz import config, server, uia, worker

    saved = (worker.CALL_TIMEOUT_S, config.WAIT_FOR_MAX_S, server._resolve_hwnd, uia.snapshot_window)
    worker.CALL_TIMEOUT_S = 0.4          # eskiden 0.4 sn'yi gecen bekleme "takili" diye duserdi
    server._resolve_hwnd = lambda hwnd, query, **_: 1
    uia.snapshot_window = lambda hwnd, **_: SimpleNamespace(walk=lambda: [])  # yoklama dogrudan okur
    try:
        out: dict[str, object] = {}

        def waiting() -> None:
            try:
                out["r"] = server.pc_wait_for(query="olmayan-oge", hwnd=1, timeout=1.5)
            except Exception as exc:  # noqa: BLE001
                out["r"] = exc

        caller = threading.Thread(target=waiting)
        caller.start()
        time.sleep(0.6)
        started = time.monotonic()
        quick = worker.call(lambda: 42)
        check("bekleme surerken baska cagri hemen calisti",
              quick == 42 and time.monotonic() - started < 0.3, f"{time.monotonic() - started:.2f} sn")
        caller.join()
        text = str(out.get("r"))
        check("ic sinirdan uzun bekleme ZAMAN ASIMI ile bitti (TimeoutError degil)",
              isinstance(out.get("r"), str) and text.startswith("ZAMAN ASIMI"), text[:120])
        polls = int(text.split("(")[1].split()[0]) if "yoklama" in text else 0
        check("birden cok yoklama yapildi", polls >= 3, str(polls))

        check("kaybolma: bos agacta hemen 'kayboldu'",
              "kayboldu (1 yoklama" in server.pc_wait_for(query="x", hwnd=1, until="disappear"))

        config.WAIT_FOR_MAX_S = 0.6
        text = server.pc_wait_for(query="x", hwnd=1, timeout=5)
        check("ust sinir asilinca soyleniyor", "ust sinir 0.6 sn" in text and "istenen 5 sn" in text, text)
        config.WAIT_FOR_MAX_S = saved[1]

        result = server.pc_batch(actions=[{"name": "pc_wait_for",
                                           "input": {"query": "x", "hwnd": 1, "timeout": 1.2}}])
        body = " ".join(part for part in result if isinstance(part, str))
        check("pc_batch icindeki uzun bekleme de dusmedi", "ZAMAN ASIMI" in body and "HATA" not in body,
              body[:160])

        # Kilit tutan toplu is: bekleme kalan kilit suresine kirpilir (uyari ve
        # oturumlar arasi mutex 10 dk asili kalmasin).
        started = time.monotonic()
        result = server._run_steps([{"name": "pc_wait_for", "input": {"query": "x", "hwnd": 1,
                                                                      "timeout": 30}}],
                                   [], -1, time.monotonic() + 1.0)
        body = " ".join(part for part in result if isinstance(part, str))
        check("kilitli toplu iste bekleme kirpildi ve soylendi",
              "kirpildi" in body and time.monotonic() - started < 2.5,
              f"{time.monotonic() - started:.1f} sn {body[-120:]}")
        clipped, why = server._clip_wait({"timeout": 3}, time.monotonic() + 20)
        check("kalan sureye sigan bekleme kirpilmadi", clipped == {"timeout": 3} and not why)
        # pc_do wait de kirpilir: her biri en cok 30 sn, birkac adimda kilit sinirini asardi.
        worker.CALL_TIMEOUT_S = saved[0]     # 0.6 sn'lik gercek bekleme "takili" sayilmasin
        started = time.monotonic()
        result = server._run_steps([{"name": "pc_do", "input": {"action": "wait", "duration": 5}}],
                                   [], -1, time.monotonic() + 0.6)
        body = " ".join(part for part in result if isinstance(part, str))
        check("kilitli toplu iste pc_do wait de kirpildi",
              "kirpildi" in body and time.monotonic() - started < 2.0,
              f"{time.monotonic() - started:.1f} sn {body[-120:]}")
        clipped, why = server._clip_wait({"action": "wait"}, time.monotonic() + 20, "duration", 1.0)
        check("sure verilmeyen pc_do wait varsayilanla (1 sn) olculur", clipped == {"action": "wait"} and not why)
    finally:
        worker.CALL_TIMEOUT_S, config.WAIT_FOR_MAX_S, server._resolve_hwnd, uia.snapshot_window = saved


def test_com_retry() -> None:
    print("E_FAIL'da tek tekrar")
    import comtypes

    from pcgoz import server, uia

    saved = (uia.snapshot_window, dict(server._last))
    tries: list[int] = []

    def flaky(fails: int, hresult: int = uia.E_FAIL) -> object:
        def snap(hwnd: int, **_: object) -> str:
            tries.append(hwnd)
            if len(tries) <= fails:
                raise comtypes.COMError(hresult, "Belirtilmemis hata", None)
            return "agac"
        return snap

    try:
        uia.snapshot_window = flaky(1)
        check("bir E_FAIL sonrasi ikinci deneme basarili",
              server._snapshot(7) == "agac" and len(tries) == 2, str(tries))
        tries.clear()
        uia.snapshot_window = flaky(2)
        try:
            server._snapshot(7)
            check("iki E_FAIL: hata gecti", False, str(tries))
        except comtypes.COMError:
            check("iki E_FAIL: hata gecti (yalniz bir tekrar)", len(tries) == 2, str(tries))
        tries.clear()
        uia.snapshot_window = flaky(1, hresult=uia.UIA_E_TIMEOUT)
        try:
            server._snapshot(7)
            check("baska COMError tekrarlanmadi", False, str(tries))
        except comtypes.COMError:
            check("baska COMError tekrarlanmadi", len(tries) == 1, str(tries))
    finally:
        uia.snapshot_window = saved[0]
        server._last.clear()
        server._last.update(saved[1])


def test_ocr_search() -> None:
    print("OCR arama (aksan/buyuk-kucuk harf duyarsiz, en kisa aralik)")
    from pcgoz import ocr

    check("fold Turkce I/i ve aksanlari esitliyor",
          ocr.fold("ŞİFRE Değiştir ISI") == ocr.fold("sifre degistir ısı") == "sifre degistir isi",
          ocr.fold("ŞİFRE Değiştir ISI"))

    def line(*words: str) -> "ocr.Line":
        return ocr.Line(" ".join(words), [ocr.Word(w, (i * 100, 0, i * 100 + 80, 20))
                                          for i, w in enumerate(words)])

    lines = [line("Dosya", "Duzen", "Kaydet"), line("Şifre", "Değiştir", "İptal")]
    hits = ocr.find(lines, "kaydet")
    check("tek kelime: sadece o kelimenin kutusu", hits == [("Kaydet", (200, 0, 280, 20))], str(hits))
    hits = ocr.find(lines, "sifre degistir")
    check("iki kelime: iki kelimeyi kapsayan kutu",
          hits == [("Şifre Değiştir", (0, 0, 180, 20))], str(hits))
    check("olmayan metin bos", ocr.find(lines, "yazdir") == [])

    print("OCR buyutme olcegi")
    check("tam ekran 1920x1080 2x okunur", ocr.auto_scale(1920, 1080) == 2)
    check("kucuk bolge 2x", ocr.auto_scale(400, 120) == 2)
    check("4K ekran piksel butcesini asar, 1x", ocr.auto_scale(3840, 2160) == 1)
    check("motorun boyut sinirini asan olcek secilmez", ocr.auto_scale(6000, 300, limit=10000) == 1)
    check("buyutme LANCZOS degil (kutulu yaziyi siliyordu)", ocr.RESAMPLE != ocr.Image.LANCZOS)



def test_audit_stats() -> None:
    print("denetim gunlugu ozeti (--stats)")
    import contextlib
    import io
    import time

    from pcgoz import __main__ as cli
    from pcgoz import config, safety

    check("gunluk gecici klasorde (gercek gunluk kirlenmiyor)", "pcgoz-test-" in str(config.LOG_PATH),
          str(config.LOG_PATH))
    started = time.perf_counter()
    for _ in range(3):
        safety.audit_outcome("pc_do", started, None)
    safety.audit_outcome("pc_do", started, safety.UserBusy("mesgul"))
    safety.audit_outcome("pc_read", started, LookupError("yok"))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.stats()
    text = out.getvalue()
    row = next((line for line in text.splitlines() if line.startswith("pc_do")), "")
    check("arac basina cagri ve hata sayildi", row.split()[1:3] == ["4", "1"], row)
    check("hata turu listelendi", "UserBusy x1" in row, row)
    check("genel hata orani", "5 cagri, 2 hata (%40)" in text,
          next((line for line in text.splitlines() if "cagri," in line), ""))


def test_turkish_search() -> None:
    print("Turkce arama (pc_find / pc_wait_for / pencere sorgusu)")
    from pcgoz.textutil import fold

    # Hepsi eskiden (casefold ile) False donuyordu -- olculdu.
    for text, needle in (("İptal", "iptal"), ("İPTAL", "iptal"), ("KAYIT", "kayıt"),
                         ("Şifre", "sifre"), ("PcGöz arama testi", "pcgoz arama")):
        check(f"{needle!r} -> {text!r} bulundu", fold(needle) in fold(text))
    check("ilgisiz metin eslesmedi", fold("iptal") not in fold("Kaydet"))


def test_render() -> None:
    print("agacin metne cevrilmesi")
    from pcgoz import render

    def tree(role, name, *children, value=""):  # noqa: ANN001, ANN202
        # Kapsayicilar odaklanamaz; gercek agaclarda da oyle.
        node = _node(role, name, None, value=value,
                     focusable=role not in ("Window", "Pane", "List"))
        node.children = list(children)
        return node

    root = tree("Window", "Belge",
                tree("Pane", "", tree("Pane", "", tree("Button", "Kaydet"))),
                tree("List", "Satirlar", *[tree("ListItem", "Satir") for _ in range(12)]),
                tree("Edit", "Not", value="bir\r\niki"))
    text = render.render(root, 1, mode="interactive").text
    lines = text.splitlines()
    button = next((line for line in lines if '"Kaydet"' in line), "")
    check("tek cocuklu sarmalayicilar atlandi", button.startswith('  Button "Kaydet"'), repr(button[:30]))
    check("ayni kardesler katlandi", sum('ListItem "Satir"' in line for line in lines) == 8
          and "... ayni turden 4 oge daha" in text)
    value = next((line for line in lines if line.lstrip().startswith("Edit")), "")
    check("cok satirli deger tek satirda", 'deger="bir\\niki"' in value, value.strip()[:60])
    small = render.render(root, 1, mode="interactive", max_chars=120)
    check("butce asilinca kesildigi soylendi", small.truncated and "[kesildi" in small.text)

    # Adsiz tablo satirlari: kendi adlari yok, icerikleri farkli -- katlanmamali.
    table = tree("Window", "Sayfa", tree("Table", "", *[
        tree("DataItem", "", tree("Button", f"Dugme {i}")) for i in range(12)]))
    text = render.render(table, 1, mode="interactive").text
    check("icerigi farkli adsiz satirlar katlanmadi",
          '"Dugme 11"' in text and "ayni turden" not in text, text.splitlines()[-1][:60])
    same = tree("Window", "Sayfa", tree("Table", "", *[
        tree("DataItem", "", tree("Button", "Sil")) for _ in range(12)]))
    check("icerigi ayni adsiz satirlar yine katlandi",
          "... ayni turden 4 oge daha" in render.render(same, 1, mode="interactive").text)

    # Gercek gorev turunda bulunanlar (2026-09-24)
    combo = tree("ComboBox", "Olcek", tree("ListItem", "125% (Onerilen)"))
    combo.children[0].selected = True
    check("kapali acilir kutunun secimi satirinda",
          'secim="125% (Onerilen)"' in render.render_matches([combo], 1), render.render_matches([combo], 1))
    from pcgoz.textutil import clean
    check("gorunmez yon isaretleri atildi",
          clean("‎24.‎09.‎2026 ‏‎14:42") == "24.09.2026 14:42")
    check("Qt nesne yolu ad sayilmadi",
          clean("QApplication.MainWindow.QWebEngineView.QObject.viewer.pages.QObject") == ""
          and clean("QApplication") == "QApplication" and clean("Kaydet.pdf") == "Kaydet.pdf")


def test_multi_monitor_math() -> None:
    print("coklu monitor: fare koordinati donusumu")
    from pcgoz import input as pin
    from pcgoz import windows

    # Birincilin SOLUNDA ikinci ekran, dikey kayik: sanal masaustu negatiften baslar.
    # SendInput (VIRTUALDESK) 0..65535'i sanal masaustune yayar; Windows geri
    # cevirirken piksel = sol + normal * genislik / 65536 (asagi yuvarlama).
    original = pin.virtual_screen
    layouts = ((-1920, -200, 3840, 1280), (0, 0, 1920, 1080), (-2560, 0, 6400, 1440))
    try:
        for vx, vy, vw, vh in layouts:
            pin.virtual_screen = lambda vx=vx, vy=vy, vw=vw, vh=vh: (vx, vy, vw, vh)
            bad = [x for x in range(vx, vx + vw)
                   if vx + pin._normalize(x, vy)[0] * vw // 65536 != x]
            bad += [y for y in range(vy, vy + vh)
                    if vy + pin._normalize(vx, y)[1] * vh // 65536 != y]
            check(f"sanal ekran ({vx},{vy} {vw}x{vh}): her piksel geri doner", not bad,
                  f"{len(bad)} sapma" + (f", ilk {bad[:3]}" if bad else ""))
    finally:
        pin.virtual_screen = original

    screens = windows.monitors()
    vx, vy, vw, vh = original()
    union = (min(s.rect[0] for s in screens), min(s.rect[1] for s in screens),
             max(s.rect[2] for s in screens), max(s.rect[3] for s in screens))
    check("ekran listesi sanal masaustunu kapliyor", union == (vx, vy, vx + vw, vy + vh),
          f"{len(screens)} ekran, {[(s.rect, s.scale, s.primary) for s in screens]}")
    check("tam olarak bir birincil ekran", sum(s.primary for s in screens) == 1)


def test_input_guard_logic() -> None:
    print("girdi kilidi: kanca kararlari (sahte olaylarla, kanca kurulmadan)")
    import time

    from pcgoz import inputguard as ig
    from pcgoz import safety

    guard = ig._Guard()
    guard.depth, guard.deadline = 1, time.monotonic() + 5      # kilit aktifmis gibi

    def key(vk: int, *, up: bool = False, injected: bool = False) -> int:
        info = ig.KBDLLHOOKSTRUCT(vkCode=vk, flags=ig.LLKHF_INJECTED if injected else 0)
        return guard._keyboard(0, ig.WM_KEYUP if up else ig.WM_KEYDOWN, ctypes.addressof(info))

    def mouse(message: int, *, injected: bool = False) -> int:
        info = ig.MSLLHOOKSTRUCT(flags=ig.LLMHF_INJECTED if injected else 0)
        return guard._mouse(0, message, ctypes.addressof(info))

    check("fiziksel tus basma yutuldu", key(0x41) == 1)
    check("PcGoz'un gonderdigi tus gecti", key(0x41, injected=True) != 1)
    check("fiziksel tus BIRAKMA gecti (takili tus olmasin)", key(0x10, up=True) != 1)
    check("fiziksel fare hareketi yutuldu", mouse(0x0200) == 1)
    check("fiziksel fare dugmesi basma yutuldu", mouse(0x0201) == 1)
    check("fiziksel fare dugmesi BIRAKMA gecti", mouse(0x0202) != 1)
    check("PcGoz'un fare hareketi gecti", mouse(0x0200, injected=True) != 1)

    safety._frozen.clear()
    try:
        for vk in (0xA2, 0xA4, 0xA0):             # sol ctrl, sol alt, sol shift basili
            key(vk)
        key(0x1B)                                 # Esc
        check("kilitliyken Ctrl+Alt+Shift+Esc dondurdu", safety.is_frozen())
        check("ve kilidi birakti", not guard.active())
        check("kilit bittikten sonra fiziksel tus gecti", key(0x41) != 1)
    finally:
        safety._frozen.clear()

    guard.depth, guard.deadline = 1, time.monotonic() - 1        # sure dolmus
    check("sure dolunca kendiliginden birakti", key(0x41) != 1 and mouse(0x0200) != 1)


def test_shared_freeze() -> None:
    print("acil dondurma: surecler arasi ortak")
    import os
    import subprocess
    import sys

    check("testler canli sunucunun nesnesine dokunmuyor",
          safety._frozen.name != safety.FREEZE_NAME, safety._frozen.name)

    name = f"Local\\PcGozBirim{os.getpid()}"
    mine = safety.Freeze(name)
    check("adli olay nesnesi acildi", mine.shared)

    def other(action: str) -> str:
        code = ("import sys; from pcgoz import safety; f = safety.Freeze(sys.argv[2]); "
                "f.clear() if sys.argv[1] == 'coz' else None; print(f.is_set())")
        out = subprocess.run([sys.executable, "-c", code, action, name],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or out.stderr.strip()[-200:]

    mine.set()
    check("bu surec dondurunca obur surec donmus goruyor", other("oku") == "True")
    check("obur surec cozunce burada da cozuldu", other("coz") == "False" and not mine.is_set())


def test_hotkey_reclaim() -> None:
    print("acil durdurma kisayolu: ilk tercih bosalinca geri alinir")
    import time

    # Gercek kisayollara dokunmamak icin kimsenin kullanmadigi iki birlesim.
    mods = safety.MOD_CONTROL | safety.MOD_ALT | safety.MOD_SHIFT
    first, second = ("Ctrl+Alt+Shift+F21", mods, 0x84), ("Ctrl+Alt+Shift+F22", mods, 0x85)
    saved = safety.HOTKEY_CANDIDATES, safety.HOTKEY_RETRY_MS, dict(safety._hotkey_state)
    safety.HOTKEY_CANDIDATES, safety.HOTKEY_RETRY_MS = (first, second), 200
    safety._hotkey_state.update(registered=False, error=None, combo=None, note=None)
    try:
        # "Baska bir sunucu" gibi ilk tercihi bu iplik tutuyor.
        held = user32.RegisterHotKey(None, 0x77, mods | safety.MOD_NOREPEAT, 0x84)
        check("deneme kisayolu tutuldu", bool(held))
        safety.start_hotkey()
        check("ilk tercih doluyken yedege dustu",
              safety.hotkey_status()["combo"] == second[0], str(safety.hotkey_status()))
        user32.UnregisterHotKey(None, 0x77)
        for _ in range(40):
            if safety.hotkey_status()["combo"] == first[0]:
                break
            time.sleep(0.05)
        status = safety.hotkey_status()
        check("sahibi birakinca ilk tercih geri alindi",
              status["combo"] == first[0] and not status["note"], str(status))
        check("yedek baskasina birakildi",
              bool(user32.RegisterHotKey(None, 0x78, mods | safety.MOD_NOREPEAT, 0x85)))
        user32.UnregisterHotKey(None, 0x78)
    finally:
        safety.HOTKEY_CANDIDATES, safety.HOTKEY_RETRY_MS = saved[0], saved[1]
        safety._hotkey_state.clear()
        safety._hotkey_state.update(saved[2])


def test_log_migration() -> None:
    print("denetim gunlugu: eski yerlerden (AppData + MSIX sanal kopyasi) tasima")
    import json
    import os
    import shutil
    import tempfile
    from pathlib import Path

    from pcgoz import config

    saved = (config.LOG_DIR, config.LOG_PATH, config.DEFAULT_LOG_DIR, os.environ.get("LOCALAPPDATA"))
    root = Path(tempfile.mkdtemp(prefix="pcgoz-test-"))
    try:
        # Gercek yerlere hic dokunulmasin: LOCALAPPDATA da varsayilan yer de sahte.
        local = root / "Local"
        real = local / "PcGoz"
        virtual = local / "Packages" / "Claude_abc" / "LocalCache" / "Local" / "PcGoz"
        for folder in (real, virtual):
            folder.mkdir(parents=True)
        (real / "actions.jsonl").write_text('{"ts": "2026-01-01T00:00:00", "tool": "gercek"}\n')
        (virtual / "actions.jsonl").write_text('{"ts": "2026-01-02T00:00:00", "tool": "sanal"}\n')
        (virtual / "yedek.jsonl.bak").write_text("yedek")
        os.environ["LOCALAPPDATA"] = str(local)
        new = root / "yeni"
        new.mkdir()
        config.LOG_DIR = config.DEFAULT_LOG_DIR = new
        config.LOG_PATH = new / "actions.jsonl"
        config.LOG_PATH.write_text('{"ts": "2026-01-03T00:00:00", "tool": "yeni"}\n')

        moved = safety.migrate_legacy_logs()
        tools = sorted(json.loads(line)["tool"] for line in config.LOG_PATH.read_text().splitlines())
        check("iki eski yer de bulundu, uc dosya tasindi", len(moved) == 3, "; ".join(moved))
        check("kayitlar tek gunlukte birlesti, yenisi korundu",
              tools == ["gercek", "sanal", "yeni"], str(tools))
        check("yedek dosya da tasindi", (new / "yedek.jsonl.bak").read_text() == "yedek")
        check("bosalan eski klasorler kalkti", not real.exists() and not virtual.exists())

        # Gunluk yeri degistirilmisse (PCGOZ_LOG_DIR, testler) eskiye dokunulmaz.
        real.mkdir()
        (real / "actions.jsonl").write_text("{}\n")
        config.LOG_DIR = new / "ozel"
        check("ozel gunluk yerinde eski gunluge dokunulmadi",
              safety.migrate_legacy_logs() == [] and (real / "actions.jsonl").exists())
    finally:
        config.LOG_DIR, config.LOG_PATH, config.DEFAULT_LOG_DIR = saved[:3]
        if saved[3] is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = saved[3]
        shutil.rmtree(root, ignore_errors=True)


def test_busy_and_held_logic() -> None:
    print("0.6.0: mesgulken reddetme yok; basili tus birakma plani")
    from pcgoz import config, inputguard

    real = safety.human_idle_ms
    try:
        safety.human_idle_ms = lambda: 10          # kullanici 10 ms once dokundu
        safety.check_user_not_busy()
        check("varsayilanda kullanici mesgulken reddetmiyor", True)
        config.REFUSE_WHEN_BUSY = True
        try:
            safety.check_user_not_busy()
            check("PCGOZ_REFUSE_WHEN_BUSY=1 ile eski davranis", False, "reddetmedi")
        except safety.UserBusy:
            check("PCGOZ_REFUSE_WHEN_BUSY=1 ile eski davranis", True)
    finally:
        config.REFUSE_WHEN_BUSY = False
        safety.human_idle_ms = real

    plan = inputguard.release_plan([0xA4, 0x01])          # sol Alt + sol fare dugmesi
    check("Alt birakilmadan once maske tusu bas-birak",
          plan[:2] == [("tus-bas", 0xE8), ("tus-birak", 0xE8)] and ("tus-birak", 0xA4) in plan
          and plan[-1] == ("fare", 0x01), str(plan))
    check("Win icin de maske", inputguard.release_plan([0x5B])[0] == ("tus-bas", 0xE8))
    check("sadece Ctrl/Shift: maske yok (yan etkisi yok)",
          inputguard.release_plan([0xA2, 0xA0]) == [("tus-birak", 0xA2), ("tus-birak", 0xA0)])
    check("CapsLock/NumLock/ScrollLock taranmiyor (ac/kapa tusu, birakma tersine cevirir)",
          not {0x14, 0x90, 0x91} & set(inputguard.MOMENTARY_VKS))


def test_secure_desktop() -> None:
    print("guvenli masaustu (UAC / Ctrl+Alt+Del): girdi sebebiyle reddedilir")
    from pcgoz import windows

    name = windows.input_desktop_name()
    check("simdi girdi masaustu 'Default'", (name or "").lower() == "default", repr(name))
    real = windows.input_desktop_name
    try:
        for fake in ("Winlogon", None):
            windows.input_desktop_name = lambda fake=fake: fake
            try:
                safety.check_input_desktop()
                check(f"guvenli masaustunde ({fake!r}) girdi reddedildi", False, "reddetmedi")
            except safety.SecureDesktop as exc:
                check(f"guvenli masaustunde ({fake!r}) girdi reddedildi",
                      "UAC" in str(exc) and "Kullanicinin kendisi" in str(exc))
        windows.input_desktop_name = lambda: "Default"
        safety.check_input_desktop()
        check("normal masaustunde gecti", True)
    finally:
        windows.input_desktop_name = real


def test_stale_content() -> None:
    print("bayat icerik: ortulu Chromium, ekran, kilit")
    import ctypes as ct
    import json
    from types import SimpleNamespace

    from pcgoz import config, occlusion as o, server, windows

    screen = (0, 0, 1920, 1080)
    check("tek pencere tamamen ortuyor", o._occluded((100, 100, 500, 400), [(50, 50, 600, 450)], screen))
    check("iki pencere birlikte ortuyor",
          o._occluded((100, 100, 500, 400), [(0, 0, 300, 1080), (300, 0, 1920, 1080)], screen))
    check("1 px serit acik: ortulu degil",
          not o._occluded((100, 100, 500, 400), [(0, 0, 499, 1080), (500, 0, 1920, 1080)], screen))
    check("ekran disina tasan kisim gorunur sayilmaz",
          o._occluded((-300, 100, 200, 400), [(0, 0, 1920, 1080)], screen))
    check("ustunde ortucu yok: gorunur", not o._occluded((100, 100, 500, 400), [], screen))
    check("buyutulmus pencere calisma alanina sigar (AdjustToFit)",
          o._fit((-8, -8, 1928, 1040), (0, 0, 1920, 1032)) == (0, 0, 1920, 1032)
          and o._fit((10, 10, 50, 50), (0, 0, 100, 100)) == (10, 10, 50, 50))

    saved_display = dict(o._display)
    try:
        setting = o.POWERBROADCAST_SETTING(o.GUID_CONSOLE_DISPLAY_STATE, 4, (ct.c_ubyte * 4)(0, 0, 0, 0))
        o._on_power(None, o.PBT_POWERSETTINGCHANGE, ct.addressof(setting))
        off = o._display["state"]
        setting.Data[0] = 2
        o._on_power(None, o.PBT_POWERSETTINGCHANGE, ct.addressof(setting))
        dim = o._display["state"]
        other = o.POWERBROADCAST_SETTING(o._GUID(), 4, (ct.c_ubyte * 4)(0, 0, 0, 0))
        o._on_power(None, o.PBT_POWERSETTINGCHANGE, ct.addressof(other))
        check("ekran bildirimi okunur (0 kapali, 2 karartilmis), baska GUID yok sayilir",
              off == 0 and dim == 2 and o._display["state"] == 2, f"{off} {dim} {o._display['state']}")
    finally:
        o._display.clear()
        o._display.update(saved_display)
    real = o.display_state()
    check("ekran durumu kaydi: Windows o anki degeri hemen gonderiyor", real in (0, 1, 2), f"durum={real}")
    if (windows.input_desktop_name() or "").lower() == "default":
        check("WTS oturum bayraklari dogru okunuyor (masaustu acik -> kilitli degil)", not o.session_locked())
    o.wake()                    # gosterilmeyen kukla pencere: hatasiz acilip kapanmali
    check("wake hatasiz", True)

    saved = (o.is_chromium, o.session_locked, o.display_state, o.chromium_hidden, o.stale_reason,
             o.wake, windows.input_desktop_name, windows.is_window, windows.focus)
    env: dict[str, Any] = {}
    o.is_chromium = lambda h: env["chromium"]
    o.session_locked = lambda: env["locked"]
    o.display_state = lambda: env["display"]
    o.chromium_hidden = lambda h: env["hidden"]
    windows.input_desktop_name = lambda: env["desk"]
    windows.is_window = lambda h: True

    def reason(*, image: bool = False, **kw: Any) -> str | None:
        env.update(chromium=True, locked=False, display=1, hidden=None, desk="Default")
        env.update(kw)
        found = o.stale_reason(42, image=image)
        return found[0] if found else None

    try:
        check("ortulu / simge durumunda Chromium -> uyari",
              reason(hidden="ortulu") == "ortulu" and reason(hidden="simge") == "simge")
        check("gorunur Chromium -> uyari yok", reason() is None)
        check("ekran kapali: Chromium metni bayat, digerinin metni degil",
              reason(display=0) == "ekran_kapali" and reason(display=0, chromium=False) is None
              and reason(display=0, chromium=False, image=True) == "ekran_kapali")
        check("kilitli oturum -> Chromium bayat", reason(locked=True) == "kilitli")
        check("guvenli masaustu: goruntude uyari, UAC'de Chromium metni bayat sayilmaz",
              reason(desk="Winlogon", image=True, chromium=False) == "guvenli_masaustu"
              and reason(desk="Winlogon") is None)
        env.update(hidden="simge")
        found = o.stale_reason(42, image=False)
        check("uyari nedeni ve ne yapacagini soyler", found is not None and "simge durumunda" in found[1]
              and "once pc_focus" in found[1], str(found))
        check("bayat Chromium'da invoke 'tikla' onerisi bastirilir", server._content_stale(42))

        calls: list[tuple[Any, bool]] = []
        o.stale_reason = lambda hwnd, image: calls.append((hwnd, image)) or ("ortulu", "[!] ORTULU")
        noted = lambda text: lambda: (server._note_target(42), text)[1]  # noqa: E731
        out = server._audited("pc_text", noted("metin"), {})
        rec = json.loads(config.LOG_PATH.read_text(encoding="utf-8").splitlines()[-1])
        check("_audited uyariyi ekler, gunluge bayat yazar",
              out == "metin\n[!] ORTULU" and rec.get("bayat") == "ortulu" and calls == [(42, False)],
              f"{out!r} {rec.get('bayat')} {calls}")
        out = server._audited("pc_screenshot", lambda: ["not", b"png"], {})
        check("goruntu araci image=True, not ilk parcaya", out == ["not\n[!] ORTULU", b"png"]
              and calls[-1] == (None, True), str(calls[-1]))
        out = server._audited("pc_invoke", noted("basildi"), {})
        check("eylemde 'tekrarlamadan once' notu", "tekrarlamadan once pc_focus" in out, out)
        check("pc_focus'a uyari eklenmez", server._audited("pc_focus", lambda: "on planda", {}) == "on planda")
        saved_raw = server._RAW["pc_text"]
        server._RAW["pc_text"] = lambda **kw: (server._note_target(42), "metin")[1]
        try:
            out = server._run_steps([{"name": "pc_text", "input": {}}], ["pc_text"], -1)
        finally:
            server._RAW["pc_text"] = saved_raw
        check("pc_batch adiminda da uyari",
              any("metin\n[!] ORTULU" in part for part in out if isinstance(part, str)), str(out))

        def boom() -> str:
            server._note_target(42)
            raise TimeoutError("metin gorunmedi")
        try:
            server._audited("pc_wait_for", boom, {})
            err = None
        except TimeoutError as exc:
            err = str(exc)
        check("hata metnine de eklenir", err == "metin gorunmedi\n[!] ORTULU", str(err))
        o.stale_reason = lambda hwnd, image: (_ for _ in ()).throw(OSError("kontrol bozuk"))
        check("kontrol hatasi araci bozmaz", server._audited("pc_text", lambda: "metin", {}) == "metin")

        # Eylem: once taze, eylem hedefi kendisi kucultunce "simge" -- uyari pc_focus'la geri aldirirdi.
        seq: list[int] = []
        o.stale_reason = lambda hwnd, image: (seq.append(hwnd), None if len(seq) == 1
                                              else ("simge", "[!] SIMGE"))[1]
        out = server._audited("pc_do", lambda: server._watch(42, lambda: "kucultuldu", False)[0], {})
        rec = json.loads(config.LOG_PATH.read_text(encoding="utf-8").splitlines()[-1])
        check("eylem hedefi kendisi gizlerse uyari yok (once tazeyse sonrasina bakilmaz), ara anahtar gunluge girmez",
              out == "kucultuldu" and seq == [42] and "_bayat_once" not in rec and "bayat" not in rec,
              f"{out!r} {seq} {rec}")
        o.stale_reason = lambda hwnd, image: ("ortulu", "[!] ORTULU")
        out = server._audited("pc_do", lambda: server._watch(42, lambda: "tiklandi", False)[0], {})
        check("eylemden once de bayatsa uyari", out.startswith("tiklandi\n[!] ORTULU")
              and "tekrarlamadan once pc_focus" in out, out)
        o.stale_reason = lambda hwnd, image: ("ust_hep_ustte", "[!] UST")
        out = server._audited("pc_invoke", lambda: server._watch(42, lambda: "basildi", False)[0], {})
        check("hep ustte ortude pc_focus onerilmez", out.endswith("tekrarlama.") and "pc_focus ile" not in out, out)
        env.update(hidden="ust_hep_ustte")
        o.stale_reason = saved[4]
        found = o.stale_reason(42, image=False)
        check("hep ustte ortu mesaji ne yapilacagini soyler",
              found is not None and found[0] == "ust_hep_ustte" and "pc_focus bunu acmaz" in found[1], str(found))

        saved_reg, saved_collect = server.registry, server.patterns.collect_text
        server.registry = SimpleNamespace(  # type: ignore[assignment]
            resolve=lambda ref, **k: SimpleNamespace(label="govde"), owning_hwnd=lambda ref: 42)
        server.patterns.collect_text = lambda node, limit=0: "eski metin"  # type: ignore[assignment]
        calls.clear()
        o.stale_reason = lambda hwnd, image: calls.append((hwnd, image)) or ("ortulu", "[!] ORTULU")
        try:
            out = server._audited("pc_text", lambda: server.pc_text(ref_id="ref_40"), {})
        finally:
            server.registry, server.patterns.collect_text = saved_reg, saved_collect
        check("pc_text ref_id ile de hedefi kaydeder", calls == [(42, False)] and out.endswith("[!] ORTULU"),
              f"{calls} {out!r}")

        woke: list[int] = []
        windows.focus = lambda h: None
        o.wake = lambda: woke.append(1)
        o.is_chromium = lambda h: True
        server._focus_and_wake(77)
        o.is_chromium = lambda h: False
        server._focus_and_wake(78)
        check("one alinan Chromium durtulur, digeri durtulmez", woke == [1], str(woke))
    finally:
        (o.is_chromium, o.session_locked, o.display_state, o.chromium_hidden, o.stale_reason,
         o.wake, windows.input_desktop_name, windows.is_window, windows.focus) = saved


def main() -> int:
    test_parse_combo()
    test_busy_and_held_logic()
    test_secure_desktop()
    test_log_migration()
    test_shared_freeze()
    test_hotkey_reclaim()
    test_input_guard_logic()
    test_turkish_search()
    test_render()
    test_multi_monitor_math()
    test_audit_stats()
    test_ocr_search()
    test_worker_timeout()
    test_wait_for_outside_worker()
    test_com_retry()
    test_screenshot_space()
    test_key_guard()
    test_screenshot_scale()
    test_stable_refs()
    test_effect_summary()
    test_stale_content()        # gunluge yazar: test_audit_stats'tan sonra
    print("=" * 58)
    print(f"{len(passed)} gecti, {len(failed)} kaldi")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
