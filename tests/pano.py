"""Pano uzerinden yapistirma testi. Elle calistirilir: uv run python tests/pano.py

Girdi gondermez: Ctrl+V yerine, panoyu istenen gecikmeyle okuyan kendi test
penceresine mesaj gonderir. Kullanicinin panosunu kullanir ama basta saklar,
sonda geri koyar (~3 sn).

Kapattigi riskler:
1. Yapistirma kullanicinin panosundaki resmi/dosyayi siliyordu (sadece metin
   saklaniyordu).
2. Pano sabit 150 ms sonra geri konuyordu; hedef gec okursa kullanicinin
   ESKI panosunu yapistiriyordu.
3. Yapistirilan metin Win+V gecmisinde kaliyordu.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _pencere import WM_APP, TestWindow  # noqa: E402

from pcgoz import clipboard  # noqa: E402
import _gunluk  # noqa: E402,F401 -- gercek denetim gunlugunu kirletme

user32 = clipboard.user32
kernel32 = clipboard.kernel32
CF_TEXT = 1
CF_DIB = 8

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    mark = "OK  " if ok else "HATA"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))


class SlowReader:
    """WM_APP gelince `delay` saniye bekleyip panodan metni okuyan 'uygulama'."""

    def __init__(self) -> None:
        self.delay = 0.0
        self.fmt = clipboard.CF_UNICODETEXT
        self.got: str | None = None
        self.private_marked = False
        self.window = TestWindow("PcGoz pano testi", handler=self._handle, noactivate=True,
                                 rect=(-2000, -2000, 200, 100))

    def _handle(self, hwnd, msg, wparam, lparam):  # noqa: ANN001, ANN202
        if msg != WM_APP:
            return None
        time.sleep(self.delay)
        if not user32.OpenClipboard(hwnd):
            self.got = "(pano acilamadi)"
            return 0
        try:
            exclude = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
            self.private_marked = bool(user32.IsClipboardFormatAvailable(exclude))
            handle = user32.GetClipboardData(self.fmt)
            pointer = kernel32.GlobalLock(handle) if handle else None
            if not pointer:
                self.got = "(veri yok)"
                return 0
            try:
                if self.fmt == CF_TEXT:
                    self.got = ctypes.string_at(pointer).decode("mbcs")
                else:
                    self.got = ctypes.c_wchar_p(pointer).value
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
        return 0

    def trigger(self) -> None:
        self.got = None
        self.window.post(WM_APP)

    def wait(self, timeout: float = 3.0) -> str | None:
        deadline = time.monotonic() + timeout
        while self.got is None and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.got


def _put(items: list[tuple[int, bytes]]) -> None:
    clipboard._open()
    try:
        user32.EmptyClipboard()
        for fmt, data in items:
            clipboard._set(fmt, data)
    finally:
        user32.CloseClipboard()


def _tiny_dib() -> bytes:
    """2x2 piksel, 32 bit, BITMAPINFOHEADER + pikseller."""
    header = (40).to_bytes(4, "little") + (2).to_bytes(4, "little") + (2).to_bytes(4, "little")
    header += (1).to_bytes(2, "little") + (32).to_bytes(2, "little") + bytes(24)
    return header + bytes([0, 0, 255, 0, 0, 255, 0, 0, 255, 0, 0, 0, 12, 34, 56, 0])


def _formats() -> dict[int, bytes]:
    return dict(clipboard.snapshot().items)


def test_formats_survive(reader: SlowReader) -> None:
    print("Kullanicinin panosu korunuyor mu")
    custom = user32.RegisterClipboardFormatW("PcGoz Test Bicimi")
    dib = _tiny_dib()
    _put([(CF_DIB, dib), (custom, b"ozel veri \x00\x01\x02")])
    before = _formats()

    reader.delay = 0.0
    result = clipboard.paste("yapistirilan metin", reader.trigger)
    reader.wait()
    after = _formats()

    check("resim (CF_DIB) geri geldi", after.get(CF_DIB, b"")[:len(dib)] == dib)
    check("ozel bicim birebir geri geldi", after.get(custom) == before.get(custom))
    check("yapistirilan metin panoda kalmadi",
          clipboard.CF_UNICODETEXT not in after or clipboard.get_text() != "yapistirilan metin")
    check("hedef dogru metni aldi", reader.got == "yapistirilan metin", repr(reader.got))
    check("sonuc okumayi bildirdi", result.read, f"{result.read_ms} ms")

    _put([])
    result = clipboard.paste("bos panoya", reader.trigger)
    reader.wait()
    check("bos pano bos kaldi (bos metin konmadi)", _formats() == {}, str(list(_formats())))


def test_slow_reader(reader: SlowReader) -> None:
    print("Gec okuyan hedef")
    for delay in (0.0, 0.4, 1.0):
        clipboard.set_text("KULLANICININ ESKI PANOSU")
        reader.delay = delay
        result = clipboard.paste(f"yeni metin {delay}", reader.trigger)
        got = reader.wait()
        check(f"{int(delay * 1000)} ms sonra okuyan dogru metni aldi",
              got == f"yeni metin {delay}", f"{got!r}, okuma {result.read_ms} ms")
        check(f"{int(delay * 1000)} ms: sonra eski pano geri geldi",
              clipboard.get_text() == "KULLANICININ ESKI PANOSU")

    # Karsilastirma: eski yontem (sabit 150 ms) ayni durumda ne yapardi.
    clipboard.set_text("KULLANICININ ESKI PANOSU")
    reader.delay = 0.4
    previous = clipboard.get_text()
    clipboard.set_text("yeni metin")
    reader.trigger()
    time.sleep(0.15)
    clipboard.set_text(previous)
    got = reader.wait()
    print(f"  (bilgi) eski yontemle 400 ms'de okuyan hedef {got!r} yapistirirdi")


def test_ansi_reader(reader: SlowReader) -> None:
    print("CF_TEXT isteyen hedef (turetilmis bicim)")
    reader.delay = 0.0
    reader.fmt = CF_TEXT
    try:
        result = clipboard.paste("ansi okuyan", reader.trigger)
        got = reader.wait()
        check("CF_TEXT isteyen de metni aldi", got == "ansi okuyan", f"{got!r}, okundu={result.read}")
    finally:
        reader.fmt = clipboard.CF_UNICODETEXT


def test_privacy_and_timeout(reader: SlowReader) -> None:
    print("Gizlilik ve zaman asimi")
    reader.delay = 0.0
    clipboard.paste("gizli olmali", reader.trigger)
    reader.wait()
    check("gecici metin pano gecmisinden haric isaretli", reader.private_marked)

    clipboard.set_text("KULLANICININ ESKI PANOSU")
    started = time.monotonic()
    result = clipboard.paste("kimse okumayacak", lambda: None, timeout=0.5)
    elapsed = time.monotonic() - started
    check("kimse okumazsa read=False", not result.read)
    check("zaman asimi suresine uyuldu", elapsed < 1.0, f"{elapsed:.2f} s")
    check("zaman asiminda da pano geri geldi", clipboard.get_text() == "KULLANICININ ESKI PANOSU")


def test_other_readers(reader: SlowReader) -> None:
    """Hedef disindaki bir surecin okumasi 'yapistirildi' sayilmamali.

    26H2'de CrossDeviceService her pano degisikligini ~200 ms'de okuyor ve
    hicbir 'izleme disi' isaretini dinlemiyor. Hedef sanilinca pano erken geri
    konuyor, gec okuyan hedef KULLANICININ ESKI PANOSUNU aliyordu.
    """
    import os

    print("Baska surecin okumasi hedef sayilmiyor")
    background = [p for p in _running() if p.lower() in clipboard.BACKGROUND_READERS]
    clipboard.set_text("KULLANICININ ESKI PANOSU")
    reader.delay = 0.4
    result = clipboard.paste("hedefe ozel", reader.trigger, readers={os.getpid()})
    got = reader.wait()
    check("hedef surec verilince 400 ms'de okuyan dogru metni aldi",
          got == "hedefe ozel", f"{got!r}, okuma {result.read_ms} ms, digerleri {result.others}")
    if not background:
        print("  (arka plan okuyucusu calismiyor -- dislama canli denenemedi)")
        return
    # Arka plan okuyucusuna metin verilmediyse gecikmeli sunum beklemede kalir
    # ve gec okuyan hedef de YAKALANIR. Eskiden arka plan once okuyunca metin
    # panoya dusuyor, hedefin okumasi hic gorunmuyordu (2 sn bekle + yanlis uyari).
    check("gec okuyan hedef yakalandi (arka plan okuyucusu sunumu tuketmedi)",
          result.read and (result.read_ms or 0) >= 300, f"read={result.read}, {result.read_ms} ms")
    result = clipboard.paste("kimse okumayacak", lambda: None, timeout=0.6, readers={os.getpid()})
    check("arka plan okuyucusu hedef sayilmadi", not result.read and result.others,
          f"read={result.read}, digerleri {result.others}")


def _running() -> list[str]:
    import subprocess

    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, check=False)
    return [line.split('","')[0].strip('"') for line in out.stdout.splitlines() if line]


def main() -> int:
    saved = clipboard.snapshot()
    print(f"(kullanicinin panosu saklandi: {len(saved.items)} bicim, {saved.size} bayt)")
    reader = SlowReader()
    try:
        test_formats_survive(reader)
        test_slow_reader(reader)
        test_ansi_reader(reader)
        test_privacy_and_timeout(reader)
        test_other_readers(reader)
    finally:
        reader.window.close()
        clipboard.restore(saved)
        back = clipboard.snapshot()
        print(f"(kullanicinin panosu geri kondu: {len(back.items)} bicim)")
    print("=" * 58)
    print(f"{len(passed)} gecti, {len(failed)} kaldi")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
