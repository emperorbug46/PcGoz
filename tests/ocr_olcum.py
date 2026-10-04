"""Windows OCR'in dogrulugu: yazi boyu ve olcek.

Elle calistirilir: uv run python tests/ocr_olcum.py

Girdi gondermez. Iki olcum:

1. Gercek pencere: bilinen metinli Edit ve dugmeler (ClearType, kenarlikli
   kutular) 11/13/16 px; uc buyutme yontemi x 1/2/3x, kac metin bulundu.
   Birkac saniye en ustte bir test penceresi gorunur (odak calmaz).
2. Sentetik sayfa: metinler PIL ile Segoe UI 9-16 px, acik/koyu zemin;
   karakter hata orani (Levenshtein / beklenen uzunluk); tam ekran sureleri.

Amac: pc_ocr'in buyutme olcegi ve yontemi olcume dayansin. ("79/79" ->
"79179" gercek bir hataydi.) Ikinci olcum tek basina yaniltti: LANCZOS ve 3x
sentetik sayfada en iyi, gercek pencerede en kotuydu -- ikisine birden bak.
"""

from __future__ import annotations

import ctypes

# Sunucu gibi: DPI-farkinda olmayan surec ekrani sanal olcuyle (1536x864) gorur.
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from pcgoz import ocr  # noqa: E402

TEXTS = (
    "79/79", "6/6", "24.09.2026", "%125", "v0.4.0", r"C:\Kod\PcGoz",
    "12:45:07", "Dosya Düzen Görünüm", "Kaydet İptal Uygula", "İstanbul ığüşöç ĞÜŞİÖÇ",
)
SIZES = (9, 11, 13, 16)
THEMES = {"acik": ((255, 255, 255), (20, 20, 20)), "koyu": ((32, 32, 32), (224, 224, 224))}
SCALES = (1, 2, 3)
FONT = r"C:\Windows\Fonts\segoeui.ttf"


def distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def sheet(size: int, theme: str) -> tuple[Image.Image, list[tuple[str, int, int]]]:
    """Metinler alt alta. Doner: goruntu ve (metin, ust, alt) bantlari."""
    background, ink = THEMES[theme]
    font = ImageFont.truetype(FONT, size)
    gap = size * 2
    image = Image.new("RGB", (520, gap * (len(TEXTS) + 1)), background)
    draw = ImageDraw.Draw(image)
    bands = []
    for index, text in enumerate(TEXTS):
        top = gap // 2 + index * gap
        draw.text((12, top), text, font=font, fill=ink)
        bands.append((text, top - gap // 3, top + size + gap // 3))
    return image, bands


def read(image: Image.Image, bands, scale: int) -> list[str]:  # noqa: ANN001
    """Her bant icin okunan metin (yoksa bos)."""
    big = image if scale == 1 else image.resize((image.width * scale, image.height * scale),
                                                ocr.RESAMPLE)
    lines, _, _ = ocr.recognize(big, scale=1)      # olcegi biz verdik; recognize tekrar buyutmesin
    found = []
    for _, top, bottom in bands:
        hits = [line for line in lines
                if top <= (line.rect[1] + line.rect[3]) / 2 / scale <= bottom]
        found.append(" ".join(line.text for line in hits))
    return found


METHODS = {"LANCZOS": Image.LANCZOS, "BICUBIC": Image.BICUBIC, "NEAREST": Image.NEAREST}


def window_bench() -> None:
    """Gercek Win32 denetimleri (ClearType, kenarlikli kutular) -- sentetik sayfanin gostermedigi.

    Olculdu: LANCZOS ile buyutulen goruntude dugme yazilari TAMAMEN kayboluyordu
    (kenarligin halka artiklari); duz zeminli sentetik sayfa bunu yakalamiyordu.
    """
    from ctypes import wintypes

    sys.path.insert(0, str(Path(__file__).parent))
    from _pencere import TestWindow, user32

    from pcgoz import capture

    gdi32 = ctypes.WinDLL("gdi32")
    gdi32.CreateFontW.restype = wintypes.HANDLE
    gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    wm_setfont = 0x0030
    edits = ("79/79", "24.09.2026", r"C:\Kod\PcGoz", "v0.4.0", "12:45:07")
    buttons = ("Kaydet", "İptal", "Uygula")
    wanted = [_squash(t) for t in edits + buttons]

    print(f"\nGercek pencere: {len(edits)} Edit + {len(buttons)} dugme, bulunan metin sayisi "
          f"(/{len(wanted)})")
    print(f"{'boy':>4s} " + "  ".join(f"{m:>7s} 1x 2x 3x" for m in METHODS))
    for px in (11, 13, 16):
        window = TestWindow("PcGoz OCR olcumu", topmost=True, noactivate=True,
                            rect=(200, 200, 460, 300), edits=tuple((t, 0) for t in edits),
                            buttons=buttons)
        font = gdi32.CreateFontW(-px, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
        try:
            for child in window.children + window.buttons:
                user32.SendMessageW(child, wm_setfont, font, 1)
            time.sleep(0.4)
            image, _, _ = capture.capture_window(window.hwnd)
        finally:
            window.close()
            gdi32.DeleteObject(font)
        cells = []
        for method in METHODS.values():
            counts = []
            for scale in SCALES:
                big = image if scale == 1 else image.resize(
                    (image.width * scale, image.height * scale), method)
                lines, _, _ = ocr.recognize(big, scale=1)
                text = _squash(" ".join(line.text for line in lines))
                counts.append(sum(w in text for w in wanted))
            cells.append(" ".join(f"{c:2d}" for c in counts))
        print(f"{px:4d} " + "  ".join(f"{'':>7s} {c}" for c in cells))


def _squash(text: str) -> str:
    return ocr.fold(text).replace(" ", "")


def main() -> int:
    window_bench()
    print(f"\nWindows OCR dogrulugu -- {len(TEXTS)} metin, Segoe UI")
    print("=" * 66)
    print(f"{'boy':>4s} {'zemin':6s} " + "  ".join(f"{s}x: hata% tam" for s in SCALES))
    totals = {s: [0, 0, 0] for s in SCALES}        # hata, uzunluk, tam
    errors: dict[int, list[str]] = {s: [] for s in SCALES}
    timing = {s: 0.0 for s in SCALES}
    for size in SIZES:
        for theme in THEMES:
            image, bands = sheet(size, theme)
            cells = []
            for scale in SCALES:
                started = time.perf_counter()
                found = read(image, bands, scale)
                timing[scale] += time.perf_counter() - started
                err = sum(distance(exp, got) for (exp, _, _), got in zip(bands, found))
                length = sum(len(exp) for exp, _, _ in bands)
                exact = sum(exp == got for (exp, _, _), got in zip(bands, found))
                totals[scale][0] += err
                totals[scale][1] += length
                totals[scale][2] += exact
                errors[scale] += [f"{size}px {theme}: {exp!r} -> {got!r}"
                                  for (exp, _, _), got in zip(bands, found) if exp != got]
                cells.append(f"{100 * err / length:6.1f} {exact:2d}/{len(TEXTS)}")
            print(f"{size:4d} {theme:6s} " + "   ".join(cells))
    print("-" * 66)
    runs = len(SIZES) * len(THEMES)
    for scale in SCALES:
        err, length, exact = totals[scale]
        print(f"{scale}x toplam: hata %{100 * err / length:.1f}, tam dogru {exact}/{runs * len(TEXTS)}, "
              f"ortalama {1000 * timing[scale] / runs:.0f} ms/goruntu")
    for scale in SCALES:
        print(f"\n{scale}x hatalari ({len(errors[scale])}):")
        for line in errors[scale][:12]:
            print(f"  {line}")

    # Gercek ekranda sure (dogruluk olculemez: beklenen metin bilinmiyor)
    from pcgoz import capture
    screen, _ = capture.capture_screen()
    print(f"\nTam ekran {screen.width}x{screen.height}:")
    for scale in SCALES:
        big = screen if scale == 1 else screen.resize((screen.width * scale, screen.height * scale),
                                                      Image.LANCZOS)
        started = time.perf_counter()
        lines, _, _ = ocr.recognize(big, scale=1)
        print(f"  {scale}x: {1000 * (time.perf_counter() - started):5.0f} ms, {len(lines)} satir")
    started = time.perf_counter()
    lines, _, scale = ocr.recognize(screen)
    print(f"  pc_ocr (otomatik): {1000 * (time.perf_counter() - started):5.0f} ms, "
          f"{scale:g}x, {len(lines)} satir")
    return 0


if __name__ == "__main__":
    sys.exit(main())
