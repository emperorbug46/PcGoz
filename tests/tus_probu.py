"""Tus probu: input.press'in gonderdigi tuslar uygulamaya ne olarak ulasiyor?

Elle calistirilir: uv run python tests/tus_probu.py

Kendi penceresini acip one alir ve uygulamanin gordugu WM_KEYDOWN / WM_CHAR
mesajlarini kaydeder. Neden gerekli: SendInput'un basarili donmesi tusun
uygulamaya ulastigi anlamina gelmiyor. Bu makinede AutoHotkey Home/End'i
tutuyor -- genisletilmis bayrakla gonderilen Home/End uygulamaya hic
ulasmiyordu. Arada bir kanca olup olmadigini ancak alici taraf soyler.

Sonda gercek ctrl+v ile, gercek bir Edit denetimine pano uzerinden
yapistirmayi da dener (clipboard.paste'in uctan uca yolu).

Girdi gonderir (~2 sn), ama sadece kendi penceresine: her gonderimden hemen
once on plandaki pencerenin kendisi oldugunu kontrol eder, degilse hic
gondermez. Gonderilenler zararsiz gezinme tuslari ve ctrl+v. Kullanicinin
panosu yapistirmadan sonra geri konur.
"""

from __future__ import annotations

import ctypes

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import sys  # noqa: E402
import time  # noqa: E402
from ctypes import wintypes  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))

from _pencere import ES_READONLY, ES_SCROLLING_MULTILINE, WM_APP, TestWindow, user32  # noqa: E402

from pcgoz import clipboard, safety, server, windows  # noqa: E402
from pcgoz import input as pin  # noqa: E402
import _gunluk  # noqa: E402,F401 -- gercek denetim gunlugunu kirletme

WM_KEYDOWN = 0x0100
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_GETTEXT = 0x000D
WM_FOCUS_EDIT = WM_APP + 1
WM_FOCUS_SELF = WM_APP + 2
WM_SYSCOMMAND = 0x0112
SC_KEYMENU = 0xF100
WM_SETTEXT = 0x000C

VK_NAMES = {0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt", 0x21: "PgUp", 0x22: "PgDn",
            0x23: "End", 0x24: "Home", 0x25: "Sol", 0x26: "Yukari", 0x27: "Sag",
            0x28: "Asagi", 0x2E: "Delete"}

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    mark = "OK  " if ok else "HATA"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))


class Probe:
    """Tuslari kaydeden pencere + icinde yapistirma testi icin bir Edit."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.window = TestWindow("PcGoz tus probu", handler=self._handle,
                                 edits=(("", ES_SCROLLING_MULTILINE), ("salt okunur", ES_READONLY)))
        self.hwnd = self.window.hwnd
        self.edit = self.window.children[0]

    def _handle(self, hwnd, msg, wparam, lparam):  # noqa: ANN001, ANN202
        if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
            self.events.append(("down", int(wparam)))
        elif msg == WM_CHAR:
            self.events.append(("char", chr(wparam)))
        elif msg == WM_SYSCOMMAND and (wparam & 0xFFF0) == SC_KEYMENU:
            # Tek basina Alt bas-birak menu moduna sokar; kaydet ama menuye girme.
            self.events.append(("menu", int(wparam)))
            return 0
        elif msg == WM_FOCUS_SELF:
            user32.SetFocus(self.hwnd)          # tuslar yine bu pencerenin handler'ina gelsin
            return 0
        elif msg == WM_FOCUS_EDIT:
            # SetFocus pencerenin kendi ipliginden; wparam = kacinci Edit
            user32.SetFocus(self.window.children[wparam])
            return 0
        return None

    def ours(self) -> bool:
        return windows.foreground_window() == self.hwnd

    def capture(self, send) -> list[tuple[str, object]] | None:  # noqa: ANN001
        """Gonder ve pencerenin gorduklerini dondur. On planda degilsek None."""
        if not self.ours():
            return None
        self.events.clear()
        send()
        time.sleep(0.2)
        return list(self.events)

    def edit_text(self) -> str:
        buf = ctypes.create_unicode_buffer(4096)
        user32.SendMessageW(self.edit, WM_GETTEXT, 4096, buf)
        return buf.value


def _fmt(events: list[tuple[str, object]]) -> str:
    parts = []
    for kind, value in events:
        if kind == "down":
            parts.append(VK_NAMES.get(value, f"vk=0x{value:02X}"))   # type: ignore[arg-type]
        else:
            parts.append(f"karakter {value!r}")
    return ", ".join(parts) or "HICBIR SEY ulasmadi"


def _raw_extended(vk: int):  # noqa: ANN202
    """Genisletilmis bayrakla ham gonderim -- araya giren kanca var mi?"""
    def send() -> None:
        scan = user32.MapVirtualKeyW(vk, 0)
        flags = pin.KEYEVENTF_EXTENDEDKEY
        pin._send(pin.INPUT(type=pin.INPUT_KEYBOARD,
                            ki=pin.KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags)))
        time.sleep(0.02)
        pin._send(pin.INPUT(type=pin.INPUT_KEYBOARD,
                            ki=pin.KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags | pin.KEYEVENTF_KEYUP)))
    return send


def wait_until_user_idle(seconds: float = 2.0, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        idle = safety.human_idle_ms()
        if idle is None or idle >= seconds * 1000:
            return True
        time.sleep(0.3)
    return False


def test_keys(probe: Probe) -> None:
    print("Bilgi: genisletilmis bayrakla ham gonderim")
    for name, vk in (("Home", 0x24), ("End", 0x23)):
        got = probe.capture(_raw_extended(vk))
        print(f"  {name:<6} -> {_fmt(got) if got is not None else 'gonderilmedi (odak kaydi)'}")

    print("input.press")
    cases = [
        ("home", [0x24]), ("end", [0x23]),
        ("ctrl+end", [0x11, 0x23]), ("ctrl+home", [0x11, 0x24]),
        # Shift'li hal ayrica onemli: bayraksiz Home numpad 7 sayilir ve
        # Windows Shift'i sahte birakmayla dusurur (Shift, Home, Shift).
        ("shift+home", [0x10, 0x24]), ("shift+end", [0x10, 0x23]),
        ("pgdn", [0x22]), ("left", [0x25]), ("delete", [0x2E]),
    ]
    for combo, expected in cases:
        got = probe.capture(lambda c=combo: pin.press(c))
        if got is None:
            check(combo, False, "odak probe penceresinden kaydi, gonderilmedi")
            return
        downs = [v for k, v in got if k == "down"]
        chars = [v for k, v in got if k == "char"]
        check(combo, downs == expected and not chars, _fmt(got))


def test_paste(probe: Probe) -> None:
    print("Gercek ctrl+v ile Edit'e yapistirma")
    probe.window.post(WM_FOCUS_EDIT)
    time.sleep(0.2)
    if not probe.ours():
        check("yapistirma", False, "odak probe penceresinden kaydi, gonderilmedi")
        return
    before = clipboard.snapshot()
    text = "Pano ile yapıştırma: ğüşıöç İĞÜŞÖÇ\r\nikinci satır"
    result = clipboard.paste(text, lambda: pin.press("ctrl+v"))
    time.sleep(0.1)
    check("Edit metni birebir aldi (Turkce + cok satir)", probe.edit_text() == text,
          ascii(probe.edit_text()[:60]))
    check("hedef okudu olarak bildirildi", result.read, f"{result.read_ms} ms, pid={result.reader_pid}")
    after = dict(clipboard.snapshot().items)
    check("kullanicinin panosu geri geldi",
          all(after.get(fmt) == data for fmt, data in before.items), f"{len(before.items)} bicim")


def test_type_report(probe: Probe) -> None:
    print("pc_do type: metin nereye gitti?")
    probe.window.post(WM_FOCUS_EDIT, 0)
    time.sleep(0.2)
    if not probe.ours():
        check("type raporu", False, "odak probe penceresinden kaydi, gonderilmedi")
        return
    out = server.pc_do(action="type", text="abc", force=True)
    lines = out.splitlines()
    # CUIAutomation8 cok satirli Edit'i "Document" rolunde veriyor.
    check("hedef adiyla bildirildi",
          any(line.startswith(("Hedef: Edit", "Hedef: Document")) for line in lines),
          next((line for line in lines if line.startswith("Hedef")), "(yok)"))
    value_line = next((line for line in lines if line.startswith("Hedefin degeri")), "")
    check("hedefin yeni degeri geri okundu", value_line.endswith('abc"'), ascii(value_line[-40:]))

    # Gunlukteki durum: metin gitmedi ama arac bunu soylemedi, model uc kez yazdi.
    probe.window.post(WM_FOCUS_EDIT, 1)
    time.sleep(0.2)
    if not probe.ours():
        check("salt okunur kutu", False, "odak probe penceresinden kaydi, gonderilmedi")
        return
    out = server.pc_do(action="type", text="q", force=True)
    check("metin tutmayinca uyardi", "DEGISMEDI" in out,
          next((line for line in out.splitlines() if "UYARI" in line), "(uyari yok)")[:70])


def test_do_paths(probe: Probe) -> None:
    """pc_do'nun key ve tiklama yollari gozlemle birlikte uctan uca calisiyor mu."""
    print("pc_do key / left_click + 'ne degisti' ozeti")
    if not probe.ours():
        check("pc_do yollari", False, "odak probe penceresinden kaydi, gonderilmedi")
        return
    out = server.pc_do(action="key", key="end", force=True)
    check("key sonuc ve ozet dondu", out.startswith("'end' gonderildi") and
          ("Sonuc" in out or "Gorunur degisiklik yok" in out), out.splitlines()[-1][:60])

    server.pc_read(hwnd=probe.hwnd, filter="all")
    edits = [n for n in server._last["tree"].walk()
             if n.role in ("Edit", "Document") and n.ref]      # cok satirli = Document
    if len(edits) != 2 or not probe.ours():
        check("pc_do left_click", False, f"{len(edits)} Edit bulundu ya da odak kaydi")
        return
    # Onceki adim odagi salt okunur kutuda birakti; digerine tiklamak odagi tasir.
    target = next(n for n in edits if "salt okunur" not in n.text)
    out = server.pc_do(action="left_click", ref=target.ref, force=True)
    check("tiklama odak degisimini bildirdi",
          any(line.startswith("odak:") and "salt okunur" in line for line in out.splitlines()),
          next((line for line in out.splitlines() if line.startswith("odak")), "(odak satiri yok)")[:70])


def test_focus_fallback(probe: Probe) -> None:
    """Odak kilidinde pc_focus baslik cubuguna tiklayarak one aliyor mu."""
    print("pc_focus: odak kilidi geri dususu (baslik cubugu)")
    point = windows.caption_point(probe.hwnd)
    check("standart baslik cubugunda guvenli nokta bulundu", point is not None, str(point))
    left, top, right, _ = windows.frame_rect(probe.hwnd)

    # Ustu ortulu baslik cubuguna tiklamak ortuye tiklamak olur: nokta verilmemeli.
    cover = TestWindow("PcGoz ortu", topmost=True, noactivate=True,
                       rect=(left - 20, top - 20, right - left + 40, 90))
    try:
        time.sleep(0.3)
        check("baslik cubugu ortuluyken nokta verilmedi",
              windows.caption_point(probe.hwnd) is None)
    finally:
        cover.close()

    # Baska bir pencere onde ve SetForegroundWindow etkisiz (kilidin benzetimi):
    # pc_focus baslik cubuguna tiklamali.
    other = TestWindow("PcGoz one gecen", rect=(left + 40, top + 220, 320, 150))
    real_focus = windows.focus
    try:
        time.sleep(0.3)
        windows.focus(other.hwnd)
        time.sleep(0.3)
        if windows.foreground_window() != other.hwnd:
            check("on kosul: diger pencere onde", False)
            return
        windows.focus = lambda hwnd: None
        home = pin.cursor_pos()
        result = server.pc_focus(hwnd=probe.hwnd, force=True)
        check("kilitte pc_focus baslik cubuguna tiklayip one aldi",
              probe.ours() and "baslik cubuguna" in result, result[:110])
        check("imlec yerine dondu", pin.cursor_pos() == home, f"{home} -> {pin.cursor_pos()}")
    finally:
        windows.focus = real_focus
        other.close()


_HOLDER = r"""
import os, sys, time
from pcgoz import inputguard
guard = inputguard._Guard(mutex_name=sys.argv[1])
guard.acquire(10)
print("aldi", flush=True)
time.sleep(float(sys.argv[2]))
if sys.argv[3] == "olur":
    os._exit(0)                    # kilidi birakmadan ol: mutex "terk edilmis" kalir
guard.release()
print("birakti", flush=True)
"""


def _holder(name: str, seconds: float, end: str):  # noqa: ANN202
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, name, str(seconds), end],
                            stdout=subprocess.PIPE, text=True)
    line = proc.stdout.readline().strip()
    return proc, line == "aldi"


def test_session_lock() -> None:
    """Iki PcGoz sureci ayni anda girdi suremez (ortak adli mutex).

    Eski 'kullanici mesgul' kurali bunu farkinda olmadan sagliyordu; 0.6.0'da
    kalkinca iki Claude oturumu ayni anda fareyi surebilirdi.
    """
    import os

    from pcgoz import config, inputguard

    print("oturumlar arasi girdi kilidi (ayri surec)")
    name = f"Local\\PcGozTestKilit{os.getpid()}"
    mine = inputguard._Guard(mutex_name=name)

    proc, ok = _holder(name, 1.5, "birak")
    started = time.perf_counter()
    mine.acquire(5)
    waited = time.perf_counter() - started
    mine.release()
    proc.wait(10)
    check("oteki surec birakana kadar beklendi", ok and 1.0 < waited < 4.0, f"{waited:.2f} sn")

    proc, ok = _holder(name, 3.0, "birak")
    original = config.SESSION_WAIT_S
    config.SESSION_WAIT_S = 0.5
    try:
        mine.acquire(5)
        mine.release()
        check("bekleme dolunca acik hata", False, "kilit alindi")
    except Exception as exc:  # noqa: BLE001
        check("bekleme dolunca acik hata", isinstance(exc, inputguard.safety.OtherSession)
              and "Baska bir PcGoz oturumu" in str(exc), f"{type(exc).__name__}")
    finally:
        config.SESSION_WAIT_S = original
        proc.wait(10)

    proc, ok = _holder(name, 0.1, "olur")
    proc.wait(10)
    started = time.perf_counter()
    mine.acquire(5)
    waited = time.perf_counter() - started
    mine.release()
    check("kilidi tutan surec olunce kilit hemen alindi (terk edilmis)", ok and waited < 1.0,
          f"{waited:.2f} sn")


def _async_down(vk: int) -> bool:
    from pcgoz import inputguard        # GetAsyncKeyState imzasi (SHORT) orada tanimli

    return bool(inputguard.user32.GetAsyncKeyState(vk) & 0x8000)


def test_held_and_overlay(probe: Probe) -> None:
    """Kilit anida basili tus/dugme ve sol ust uyari (0.6.0)."""
    from PIL import ImageChops, ImageStat

    from pcgoz import capture, config, inputguard, overlay

    print("0.6.0: basili tus/dugme birakma ve sol ust uyari")
    guard = inputguard.guard
    original = config.HELD_WAIT_S
    config.HELD_WAIT_S = 0.3                 # kullanici birakmiyor: bekleme kisa dolsun
    try:
        # Kontrol deneyi: maskesiz Alt bas-birak menu moduna sokuyor mu? (Sokmuyorsa
        # asagidaki "menuye girilmedi" kontrolu bir sey kanitlamaz.)
        probe.window.post(WM_FOCUS_SELF)
        time.sleep(0.2)
        probe.events.clear()
        pin._send(pin._key(0xA4, False), pin._key(0xA4, True))
        time.sleep(0.3)
        control = any(k == "menu" for k, _ in probe.events)
        check("kontrol: maskesiz Alt bas-birak menu moduna sokar", control, _fmt(probe.events))

        # Alt basili kaldi: kilit maskeyle birakmali, menu moduna girilmemeli.
        probe.events.clear()
        pin._send(pin._key(0xA4, False))
        with inputguard.hold(5):
            released = list(guard.released_held)
        time.sleep(0.3)
        downs = [v for k, v in probe.events if k == "down"]
        check("basili Alt birakildi, once maske tusu gitti",
              0xA4 in released and 0xE8 in downs and not _async_down(0xA4), _fmt(probe.events))
        check("Alt birakilirken menu moduna girilmedi",
              not any(k == "menu" for k, _ in probe.events), _fmt(probe.events))

        # Ctrl basili kaldi: birakilmazsa yazilan 'a' Ctrl+A (tumunu sec) olurdu.
        probe.window.post(WM_FOCUS_EDIT, 0)
        time.sleep(0.2)
        user32.SendMessageW(probe.edit, WM_SETTEXT, 0, "")
        pin._send(pin._key(0xA2, False))
        with inputguard.hold(5):
            released = list(guard.released_held)
            pin.press("a")
        time.sleep(0.2)
        check("basili Ctrl birakildi, 'a' Ctrl+A olmadi",
              0xA2 in released and not _async_down(0xA2) and probe.edit_text() == "a",
              f"birakilan {released}, metin {probe.edit_text()!r}")

        # Sol dugme basili kaldi (surukleme): birakilmali.
        left, top, right, bottom = windows.frame_rect(probe.hwnd)
        pin.move(right - 40, bottom - 20)
        pin._send(pin._mouse(pin.MOUSEEVENTF_LEFTDOWN))
        with inputguard.hold(5):
            released = list(guard.released_held)
            down = _async_down(0x01)
        check("basili sol dugme birakildi", 0x01 in released and not down, f"birakilan {released}")
    finally:
        config.HELD_WAIT_S = original
        for vk in (0xA2, 0xA4):              # ne olursa olsun takili kalmasin
            if _async_down(vk):
                pin._send(pin._key(0xE8, False), pin._key(0xE8, True), pin._key(vk, True))

    # Uyari: kilitliyken gorunur, yakalamaya girmez, tiklamayi gecirir, listede yok.
    with inputguard.hold(5):
        time.sleep(0.3)
        hwnd = overlay.overlay.hwnd
        shown = overlay.overlay.visible()
        excluded = overlay.capture_excluded()
        box = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(box))
        cx, cy = (box.left + box.right) // 2, (box.top + box.bottom) // 2
        under = windows.top_level_at(cx, cy)
        listed = hwnd in windows.visible_hwnds()
        region = (box.left, box.top, box.right - box.left, box.bottom - box.top)
        with_overlay = capture.capture_region(*region)
    time.sleep(0.8)
    hidden = not overlay.overlay.visible()
    without = capture.capture_region(*region)
    diff = max(ImageStat.Stat(ImageChops.difference(with_overlay.convert("RGB"),
                                                    without.convert("RGB"))).mean)
    check("kilitliyken sol ustte uyari gorundu", shown and box.left < 200 and box.top < 200,
          f"({box.left},{box.top} {box.right - box.left}x{box.bottom - box.top})")
    check("uyari ekran yakalamadan dislanmis (WDA_EXCLUDEFROMCAPTURE)", excluded)
    check("ekran goruntusunde uyari yok", diff < 3.0, f"ortalama fark {diff:.2f}")
    check("tiklama uyarinin icinden geciyor (WindowFromPoint alttakini veriyor)",
          under not in (0, hwnd), f"altta hwnd={under}")
    check("uyari pencere listesinde yok", not listed)
    check("kilit bitince uyari gizlendi", hidden)


def test_input_guard(probe: Probe) -> None:
    """Gercek kanca kuruluyken PcGoz'un kendi girdisi geciyor mu, kanca kalkiyor mu.

    Kullanicinin fiziksel girdisinin yutuldugu burada olculemez (SendInput hep
    'injected'); o karar birim testinde sahte olaylarla deneniyor.
    """
    print("girdi kilidi: gercek kanca")
    from pcgoz import inputguard

    probe.window.post(WM_FOCUS_SELF)      # onceki adim odagi bir Edit'te birakti
    time.sleep(0.2)
    started = time.perf_counter()
    with inputguard.hold(5):
        took = (time.perf_counter() - started) * 1000
        installed = len(inputguard.guard._hooks)
        got = probe.capture(lambda: pin.press("end"))
    time.sleep(0.05)
    check("kilitte klavye + fare kancasi kuruldu", installed == 2,
          f"{installed} kanca, kurulum {took:.1f} ms")
    downs = [v for k, v in got if k == "down"] if got is not None else None
    check("kilitliyken PcGoz'un tusu uygulamaya ulasti", downs == [0x23],
          _fmt(got) if got is not None else "odak kaydi, gonderilmedi")
    check("kilit bitince kancalar kaldirildi", not inputguard.guard._hooks)

    # Her girdi eylemi kilidi kurup kaldiriyor: maliyeti olc.
    rounds = 20
    started = time.perf_counter()
    for _ in range(rounds):
        with inputguard.hold(5):
            pass
    per = (time.perf_counter() - started) * 1000 / rounds
    check("kilit kur/kaldir maliyeti dusuk", per < 20, f"{per:.1f} ms / eylem")


def test_cursor_landing() -> None:
    """Imlec istenen piksele TAM dusuyor mu? Imleci oynatir, tiklamaz.

    Eski formul (round(x * 65535 / (w-1))) konumlarin ~%10'unda 1 px sapiyordu;
    kucuk hedeflerde (onay kutusu kenari, ayirici, surukleme tutamaci) iska.
    """
    print("imlec konumu (SendInput mutlak -> GetCursorPos)")
    vx, vy, vw, vh = pin.virtual_screen()
    xs = list(range(0, 24)) + list(range(vw // 2 - 10, vw // 2 + 10)) + list(range(vw - 24, vw))
    ys = list(range(0, 12)) + list(range(vh // 2 - 8, vh // 2 + 8)) + list(range(vh - 12, vh))
    flags = pin.MOUSEEVENTF_MOVE | pin.MOUSEEVENTF_ABSOLUTE | pin.MOUSEEVENTF_VIRTUALDESK

    def land(x: int, y: int) -> tuple[int, int]:
        pin._send(pin._mouse(flags, x, y))
        time.sleep(0.004)
        return pin.cursor_pos()

    home = pin.cursor_pos()
    try:
        bad_x = [x for x in xs if land(vx + x, vy + vh // 2)[0] != vx + x]
        bad_y = [y for y in ys if land(vx + vw // 2, vy + y)[1] != vy + y]
    finally:
        land(*home)
    check("her pikselde imlec tam yerinde", not bad_x and not bad_y,
          f"x {len(bad_x)}/{len(xs)}, y {len(bad_y)}/{len(ys)} sapma")
    check("imlec eski yerine kondu", pin.cursor_pos() == home)


def main() -> int:
    probe = Probe()
    try:
        if not wait_until_user_idle():
            print("Kullanici 60 sn boyunca klavyeyi birakmadi; hicbir sey gonderilmedi.")
            return 1
        windows.focus(probe.hwnd)
        time.sleep(0.3)
        if not probe.ours():
            # Windows'un odak kilidi (or. on planda gorev cubugu varken) arka plandaki
            # surecin pencere one almasini reddediyor. Kendi baslik cubugumuza tikla --
            # ama sadece dogrulanmis bir surukleme noktasina (bkz. windows.caption_point).
            point = windows.caption_point(probe.hwnd)
            if point:
                pin.click(*point)
                time.sleep(0.3)
        if not probe.ours():
            print("Probe penceresi one alinamadi; hicbir sey gonderilmedi.")
            return 1
        test_keys(probe)
        test_paste(probe)
        test_type_report(probe)
        test_do_paths(probe)
        test_input_guard(probe)
        test_focus_fallback(probe)
        test_session_lock()
        test_held_and_overlay(probe)
        test_cursor_landing()
    finally:
        probe.window.close()

    print("=" * 58)
    print(f"{len(passed)} gecti, {len(failed)} kaldi")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
