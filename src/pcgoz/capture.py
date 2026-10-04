"""Ekran yakalama, olcekleme ve ref kutusu cizimi.

Tek pencere yakalamada once PrintWindow(PW_RENDERFULLCONTENT) deneniyor:
bu yol pencerenin ustu ortulu ya da arkada olsa bile dogru goruntu veriyor.
Bazi uygulamalar (donanim hizlandirmali oynaticilar, bazi oyunlar) bu cagriya
siyah dondurur; o durumda ekrandan kirpmaya dusuluyor.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from . import config, windows
from .uia import Node

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0

#: Ekranda nadir gorulen bir renk -- kutular arayuzle karismasin.
BOX_COLOR = (255, 45, 149)
BOX_TEXT = (255, 255, 255)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _bitmap_to_image(hdc_mem: int, bitmap: int, width: int, height: int) -> Image.Image:
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height        # negatif = yukaridan asagi satir duzeni
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = BI_RGB

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(hdc_mem, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS)
    return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)


def capture_window(hwnd: int) -> tuple[Image.Image, tuple[int, int], bool]:
    """Pencereyi yakala. Doner: (goruntu, ekrandaki sol-ust kosesi, yanit veriyor mu).

    PrintWindow pencereye senkron mesaj gonderiyor: donmus pencerede pencere
    kendine gelene kadar bekliyordu (olculdu: 14.7 sn). Yanit vermeyen
    pencere ekrandan kirpiliyor -- ustu ortuluyse ustteki gorunur, ama
    sunucu kilitlenmez. Ucuncu deger False ise boyle oldu.
    """
    if not windows.is_window(hwnd):
        raise LookupError(f"hwnd {hwnd} artik yok.")
    left, top, right, bottom = windows.frame_rect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise ValueError(f"hwnd {hwnd} penceresinin gorunur alani yok (simge durumunda olabilir).")
    if not windows.responds(hwnd):
        return capture_region(left, top, width, height), (left, top), False

    hdc_window = user32.GetWindowDC(ctypes.c_void_p(hwnd))
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    bitmap = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    try:
        gdi32.SelectObject(hdc_mem, bitmap)
        ok = user32.PrintWindow(ctypes.c_void_p(hwnd), hdc_mem, PW_RENDERFULLCONTENT)
        image = _bitmap_to_image(hdc_mem, bitmap, width, height)
        if not ok or _looks_blank(image):
            return capture_region(left, top, width, height), (left, top), True
        return image, (left, top), True
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(ctypes.c_void_p(hwnd), hdc_window)


def _looks_blank(image: Image.Image) -> bool:
    """PrintWindow basarisiz oldugunda genelde duz siyah doner."""
    small = image.resize((32, 32))
    extrema = small.convert("L").getextrema()
    return extrema[1] <= 8


def capture_region(x: int, y: int, width: int, height: int) -> Image.Image:
    """Ekranin bir bolgesini yakala (ustte ne varsa onu gorur)."""
    hdc_screen = user32.GetDC(None)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bitmap = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
    try:
        gdi32.SelectObject(hdc_mem, bitmap)
        gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, x, y, SRCCOPY)
        return _bitmap_to_image(hdc_mem, bitmap, width, height)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def capture_screen() -> tuple[Image.Image, tuple[int, int]]:
    from .input import virtual_screen

    vx, vy, vw, vh = virtual_screen()
    return capture_region(vx, vy, vw, vh), (vx, vy)


# --------------------------------------------------------------------------
# Etiketleme
# --------------------------------------------------------------------------

def _font(size: int = 13) -> ImageFont.ImageFont:
    for name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf"):
        try:
            return ImageFont.truetype(f"C:\\Windows\\Fonts\\{name}", size)
        except OSError:
            continue
    return ImageFont.load_default()


def annotate(
    image: Image.Image,
    origin: tuple[int, int],
    nodes: list[Node],
    *,
    limit: int = 60,
) -> int:
    """Ogelerin uzerine numarali kutu ciz. Doner: cizilen kutu sayisi.

    Numaralar ref_N'in N'i -- agacta gordugum ref ile resimde gordugum kutu
    ayni sey olsun diye. Gorme ile eylem arasindaki bag tam olarak burasi.
    """
    ox, oy = origin
    draw = ImageDraw.Draw(image)
    font = _font()
    area_limit = image.width * image.height * 0.5

    # Kucuk ogeler once: buyuk kapsayicilar kutu israfi.
    candidates = [
        n for n in nodes
        if n.ref and n.area > 0 and n.area < area_limit and n.actionable and not n.offscreen
    ]
    candidates.sort(key=lambda n: n.area)

    drawn = 0
    used: list[tuple[int, int, int, int]] = []
    for node in candidates:
        if drawn >= limit:
            break
        x1, y1 = node.rect[0] - ox, node.rect[1] - oy
        x2, y2 = node.rect[2] - ox, node.rect[3] - oy
        if x2 <= 0 or y2 <= 0 or x1 >= image.width or y1 >= image.height:
            continue
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(image.width - 1, x2), min(image.height - 1, y2)
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue

        label = node.ref.split("_", 1)[1]
        draw.rectangle([x1, y1, x2, y2], outline=BOX_COLOR, width=2)

        tw = draw.textlength(label, font=font)
        lx, ly = x1, max(0, y1 - 15)
        box = (int(lx), int(ly), int(lx + tw + 6), int(ly + 15))
        if any(_overlaps(box, u) for u in used):
            ly = y1 + 1                     # etiket cakisiyorsa ogenin icine al
            box = (int(lx), int(ly), int(lx + tw + 6), int(ly + 15))
        draw.rectangle(box, fill=BOX_COLOR)
        draw.text((lx + 3, ly + 1), label, fill=BOX_TEXT, font=font)
        used.append(box)
        drawn += 1
    return drawn


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


# --------------------------------------------------------------------------
# Cikti
# --------------------------------------------------------------------------

def fit_factor(width: int, height: int) -> float:
    """Resmin modele API'de kucultulmeden ulasmasi icin en buyuk olcek.

    Iki sinir birden: uzun kenar ve toplam piksel. Kucuk resimlerde 1'den
    buyuk cikabilir (yakinlastirmanin ne kadar gidebilecegi).
    """
    by_edge = config.SCREENSHOT_MAX_EDGE / max(1, width, height)
    by_area = (config.SCREENSHOT_MAX_PIXELS / max(1, width * height)) ** 0.5
    return min(by_edge, by_area)


def to_png(image: Image.Image, *, scale: float | None = None) -> tuple[bytes, tuple[int, int], float]:
    """PNG bayti + son boyut + uygulanan olcek.

    Olcek verilmezse resim sadece model sinirini asiyorsa kucultulur (bkz.
    config.SCREENSHOT_MAX_EDGE). Verilen olcek de sinirda kesilir: sinirin
    ustune cikan bir yakinlastirmayi API zaten geri kucultur, ve o zaman
    koordinatlar bizim bildirdigimiz olcekle tutmaz.
    """
    factor = min(scale if scale is not None else 1.0, fit_factor(image.width, image.height))

    if abs(factor - 1.0) > 0.001:
        original = image.width
        # Asagi yuvarla: yukari yuvarlama siniri bir piksel asabilir.
        size = (max(1, int(image.width * factor)), max(1, int(image.height * factor)))
        image = image.resize(size, Image.LANCZOS)
        factor = image.width / original

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), (image.width, image.height), factor
