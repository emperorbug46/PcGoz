"""Canli testler icin sayac sayfasi: cizilen kare ve UIA metni guncel mi?

fixtures/sayac.html her saniye arka plan rengini degistirir; renk sayaci birebir
kodlar (R: n%16, G: n/16, B=200). Baslik JS durumunu cizimden bagimsiz gosterir
("PcGozD f=<n> v=<visible|hidden>"): cizilen sayac basliktakinin gerisindeyse kare
bayat. Olcum yontemi 27.10.2026'dan.
"""

from __future__ import annotations

import ctypes
import re
from pathlib import Path

from PIL import Image

from pcgoz import capture, windows

SAYFA = Path(__file__).parent / "fixtures" / "sayac.html"
user32 = ctypes.windll.user32


def coz(color: tuple[int, int, int]) -> int | str:
    """Renk -> cizilen sayac; sayfa rengi degilse sinif adi."""
    r, g, b = color
    if abs(b - 200) < 10:
        return r // 16 + 16 * (g // 16)
    if max(color) < 30:
        return "siyah"
    if min(color) > 225:
        return "beyaz"
    return f"diger{color}"


def ortalama(img: Image.Image) -> tuple[int, int, int]:
    px = img.load()
    vals = [px[x, y] for x in range(img.width) for y in range(img.height)]
    return tuple(sum(p[k] for p in vals) // len(vals) for k in range(3))  # type: ignore[return-value]


def _orta(img: Image.Image) -> int | str:
    cx, cy = img.width // 2, img.height // 2 + 40      # arac cubugunun altinda, sayfa ortasi
    return coz(ortalama(img.crop((cx - 4, cy - 4, cx + 5, cy + 5))))


def pw_sayac(hwnd: int) -> int | str:
    """PrintWindow(PW_RENDERFULLCONTENT) karesindeki sayac -- capture'in geri dusussuz hali."""
    left, top, right, bottom = windows.frame_rect(hwnd)
    width, height = right - left, bottom - top
    hdc = user32.GetWindowDC(ctypes.c_void_p(hwnd))
    mem = capture.gdi32.CreateCompatibleDC(hdc)
    bmp = capture.gdi32.CreateCompatibleBitmap(hdc, width, height)
    try:
        capture.gdi32.SelectObject(mem, bmp)
        ok = user32.PrintWindow(ctypes.c_void_p(hwnd), mem, capture.PW_RENDERFULLCONTENT)
        img = capture._bitmap_to_image(mem, bmp, width, height)
    finally:
        capture.gdi32.DeleteObject(bmp)
        capture.gdi32.DeleteDC(mem)
        user32.ReleaseDC(ctypes.c_void_p(hwnd), hdc)
    return _orta(img) if ok else "pw_hata"


def ekran_sayac(hwnd: int) -> int | str:
    """Ekranda (ustte ne varsa) pencerenin ortasindaki sayac."""
    left, top, right, bottom = windows.frame_rect(hwnd)
    return _orta(capture.capture_region(left, top, right - left, bottom - top))


def baslik(hwnd: int) -> tuple[int | None, str]:
    """(sayac, 'v' gorunur / 'h' gizli) -- Chromium'un kendi gorunurluk karari."""
    m = re.search(r"f=(\d+) v=(\w)", windows.window_text(hwnd))
    return (int(m[1]), m[2]) if m else (None, "?")
