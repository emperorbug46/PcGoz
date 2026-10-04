"""Girdi kilitliyken ekranin sol ustunde yari saydam uyari.

Kullanicinin istegi (2026-10-03): PcGoz eylem yaparken fare ve klavye
kilitleniyor; kullanici bunun sebebini ekranda gorsun. Kilit 0.6.0'dan beri
kullanici fareyi kullanirken de aliniyor (eskiden eylem reddediliyordu), yani
"fare neden kipirdamiyor" sorusunun cevabi ekranda olmali.

Pencere:
- tiklama icinden gecer (WS_EX_TRANSPARENT + katmanli), odak calmaz
  (NOACTIVATE), gorev cubugunda gorunmez (TOOLWINDOW), en ustte (TOPMOST);
- yari saydam (%80), koyu zemin, amber nokta, Windows 11 yuvarlak kose;
- PcGoz'un KENDI gozune girmez: WDA_EXCLUDEFROMCAPTURE ile ekran goruntusu ve
  OCR onu gormez, windows.visible_hwnds sinifina gore onu eler (pencere
  listesine ve "ne degisti" ozetine girmez).

Gorunurluk kilide bagli: inputguard "goster" der, 100 ms'lik zamanlayici
kilidin hala etkin olup olmadigina bakar; kilit bittikten LINGER_S sonra
gizlenir -- ardisik eylemlerde yanip sonmesin, suresi dolan takilmis bir is de
uyariyi acik birakmasin.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from typing import Callable

from . import config, windows

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x08, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
LWA_ALPHA = 0x2
WDA_EXCLUDEFROMCAPTURE = 0x11
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SPI_GETWORKAREA = 0x0030
WM_PAINT, WM_TIMER, WM_NCHITTEST, WM_APP = 0x000F, 0x0113, 0x0084, 0x8000
HTTRANSPARENT = -1
_SHOW = WM_APP + 1
TRANSPARENT_BK, NULL_PEN = 1, 8
DT_LEFT, DT_VCENTER, DT_SINGLELINE, DT_NOPREFIX = 0x0, 0x4, 0x20, 0x800
FW_NORMAL, FW_SEMIBOLD = 400, 600
CLEARTYPE_QUALITY = 5

#: Opaklik (0-255): %80 -- arkasi seziliyor ama yazi okunuyor.
ALPHA = 204
#: Kilit bittikten sonra uyarinin acik kaldigi sure.
LINGER_S = 0.4
#: "durduruldu" gibi geri bildirimlerin ekranda kalma suresi.
NOTICE_S = 2.5


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL),
                ("rgbReserved", ctypes.c_byte * 32)]


# 64 bit'te imzasiz cagri lparam'i tasirip pencere dongusunu bozuyor (_pencere.py kalibi).
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.FrameRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(wintypes.RECT), wintypes.UINT]
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wintypes.HGDIOBJ
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.Ellipse.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                        ctypes.POINTER(wintypes.SIZE)]


def _rgb(hex_color: str) -> int:
    """'#RRGGBB' -> COLORREF (0x00BBGGRR)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return r | (g << 8) | (b << 16)


BACKGROUND, BORDER, TITLE, SUBTITLE = "#18181B", "#3F3F46", "#F4F4F5", "#A1A1AA"

def _turkish_ui() -> bool:
    """Uyari dili: PCGOZ_LANG=tr|en zorlar; yoksa Windows arayuz dili (Turkce degilse Ingilizce)."""
    forced = os.environ.get("PCGOZ_LANG", "").strip().lower()
    if forced in ("tr", "en"):
        return forced == "tr"
    try:
        return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x1F   # LANG_TURKISH
    except Exception:
        return True


#: durum -> (1. satir, 2. satir, nokta rengi)
_STATES_TR: dict[str, tuple[str, str, str]] = {
    "kilit": ("PcGöz çalışıyor — fare ve klavye kilitli", "Durdurmak için Ctrl+Alt+Shift+Esc", "#F59E0B"),
    "bekle": ("PcGöz çalışıyor — fare ve klavye kilitli", "Basılı tuşu bırakın", "#F59E0B"),
    "durdu": ("PcGöz durduruldu", "Devam için kısayola tekrar basın", "#EF4444"),
}
_STATES_EN: dict[str, tuple[str, str, str]] = {
    "kilit": ("PcGöz is working — mouse and keyboard locked", "Press Ctrl+Alt+Shift+Esc to stop", "#F59E0B"),
    "bekle": ("PcGöz is working — mouse and keyboard locked", "Release the held key", "#F59E0B"),
    "durdu": ("PcGöz stopped", "Press the shortcut again to resume", "#EF4444"),
}
STATES = _STATES_TR if _turkish_ui() else _STATES_EN
_ORDER = tuple(STATES)


class _Overlay:
    def __init__(self) -> None:
        self.hwnd = 0
        self.state = "kilit"
        self.active: Callable[[], bool] = lambda: False
        self._visible = False
        self._seen = 0.0             # kilidin en son etkin goruldugu an
        self._notice_until = 0.0     # "durdu" gibi gecici bildirimin bitisi
        self._size = (0, 0)
        self._fonts: tuple[int, int] = (0, 0)
        self._scale = 0.0
        self._proc = WNDPROC(self._wndproc)       # referans canli kalmali
        self._started = False
        self._start_lock = threading.Lock()
        self._ready = threading.Event()

    # -- disaridan (her iplikten) -----------------------------------------

    def show(self, state: str = "kilit") -> None:
        if not config.OVERLAY or state not in STATES:
            return
        self._ensure()
        if self.hwnd:
            user32.PostMessageW(self.hwnd, _SHOW, _ORDER.index(state), 0)

    def visible(self) -> bool:
        return bool(self.hwnd) and bool(user32.IsWindowVisible(self.hwnd))

    def _ensure(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._run, name="pcgoz-uyari", daemon=True).start()
        self._ready.wait(2)

    # -- uyari ipligi --------------------------------------------------------

    def _run(self) -> None:
        instance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._proc, lpszClassName=windows.OVERLAY_CLASS,
                       hInstance=instance)
        user32.RegisterClassW(ctypes.byref(wc))
        ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
        self.hwnd = user32.CreateWindowExW(ex, windows.OVERLAY_CLASS, "PcGöz", WS_POPUP,
                                           0, 0, 10, 10, None, None, instance, None) or 0
        if self.hwnd:
            user32.SetLayeredWindowAttributes(self.hwnd, 0, ALPHA, LWA_ALPHA)
            user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)
            corner = ctypes.c_int(DWMWCP_ROUND)
            dwmapi.DwmSetWindowAttribute(wintypes.HWND(self.hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
                                         ctypes.byref(corner), ctypes.sizeof(corner))
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wparam, lparam):  # noqa: ANN001, ANN202
        if msg == _SHOW:
            self._on_show(_ORDER[wparam] if wparam < len(_ORDER) else "kilit")
            return 0
        if msg == WM_TIMER:
            self._on_timer()
            return 0
        if msg == WM_PAINT:
            self._paint()
            return 0
        if msg == WM_NCHITTEST:
            return HTTRANSPARENT          # tiklamalar alttaki pencereye
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_show(self, state: str) -> None:
        now = time.monotonic()
        if state == "durdu":
            self._notice_until = now + NOTICE_S
        elif now < self._notice_until:
            return                         # bildirim ekrandayken ezilmesin
        self._seen = now
        if state != self.state or not self._visible:
            self.state = state
            self._place()
            user32.InvalidateRect(self.hwnd, None, True)
        if not self._visible:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self._visible = True
            user32.SetTimer(self.hwnd, 1, 100, None)

    def _on_timer(self) -> None:
        now = time.monotonic()
        if self.active():
            self._seen = now
        if now - self._seen > LINGER_S and now > self._notice_until:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            user32.KillTimer(self.hwnd, 1)
            self._visible = False

    # -- cizim ----------------------------------------------------------------

    def _metrics(self) -> dict[str, int]:
        s = self._scale
        return {"pad": round(14 * s), "pad_v": round(9 * s), "dot": round(8 * s),
                "gap": round(10 * s), "line1": round(18 * s), "line2": round(16 * s),
                "margin": round(20 * s)}

    def _place(self) -> None:
        primary = next((m for m in windows.monitors() if m.primary), None)
        scale = (primary.scale if primary else 100) / 100
        if scale != self._scale:
            for font in self._fonts:
                if font:
                    gdi32.DeleteObject(font)
            self._scale = scale
            self._fonts = (
                gdi32.CreateFontW(-round(13 * scale), 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, 1, 0, 0,
                                  CLEARTYPE_QUALITY, 0, "Segoe UI"),
                gdi32.CreateFontW(-round(11 * scale), 0, 0, 0, FW_NORMAL, 0, 0, 0, 1, 0, 0,
                                  CLEARTYPE_QUALITY, 0, "Segoe UI"),
            )
        m = self._metrics()
        title, subtitle, _ = STATES[self.state]
        hdc = user32.GetDC(self.hwnd)
        widths = []
        try:
            for font, text in zip(self._fonts, (title, subtitle)):
                old = gdi32.SelectObject(hdc, font)
                size = wintypes.SIZE()
                gdi32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(size))
                widths.append(size.cx)
                gdi32.SelectObject(hdc, old)
        finally:
            user32.ReleaseDC(self.hwnd, hdc)
        width = m["pad"] + m["dot"] + m["gap"] + max(widths) + m["pad"]
        height = m["pad_v"] * 2 + m["line1"] + m["line2"]
        self._size = (width, height)
        work = wintypes.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work), 0)
        user32.SetWindowPos(self.hwnd, wintypes.HWND(HWND_TOPMOST), work.left + m["margin"],
                            work.top + m["margin"], width, height, SWP_NOACTIVATE)

    def _paint(self) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(self.hwnd, ctypes.byref(ps))
        try:
            width, height = self._size
            m = self._metrics()
            title, subtitle, dot_color = STATES[self.state]
            rect = wintypes.RECT(0, 0, width, height)
            for color, draw in ((BACKGROUND, user32.FillRect), (BORDER, user32.FrameRect)):
                brush = gdi32.CreateSolidBrush(_rgb(color))
                draw(hdc, ctypes.byref(rect), brush)
                gdi32.DeleteObject(brush)

            brush = gdi32.CreateSolidBrush(_rgb(dot_color))
            old_brush = gdi32.SelectObject(hdc, brush)
            old_pen = gdi32.SelectObject(hdc, gdi32.GetStockObject(NULL_PEN))
            top = m["pad_v"] + (m["line1"] - m["dot"]) // 2
            gdi32.Ellipse(hdc, m["pad"], top, m["pad"] + m["dot"] + 1, top + m["dot"] + 1)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(brush)

            gdi32.SetBkMode(hdc, TRANSPARENT_BK)
            left = m["pad"] + m["dot"] + m["gap"]
            lines = ((self._fonts[0], TITLE, title, m["pad_v"], m["line1"]),
                     (self._fonts[1], SUBTITLE, subtitle, m["pad_v"] + m["line1"], m["line2"]))
            for font, color, text, y, h in lines:
                old = gdi32.SelectObject(hdc, font)
                gdi32.SetTextColor(hdc, _rgb(color))
                box = wintypes.RECT(left, y, width - m["pad"], y + h)
                user32.DrawTextW(hdc, text, -1, ctypes.byref(box),
                                 DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX)
                gdi32.SelectObject(hdc, old)
        finally:
            user32.EndPaint(self.hwnd, ctypes.byref(ps))


overlay = _Overlay()


def show(state: str = "kilit") -> None:
    """Uyariyi goster ya da durumunu degistir (her iplikten cagrilabilir)."""
    overlay.show(state)


def attach(active: Callable[[], bool]) -> None:
    """Uyari bu fonksiyon True dondukce acik kalir (girdi kilidinin durumu)."""
    overlay.active = active


def capture_excluded() -> bool:
    """Uyari penceresi ekran yakalamadan dislanmis mi (test icin)."""
    if not overlay.hwnd:
        return False
    affinity = wintypes.DWORD()
    user32.GetWindowDisplayAffinity(overlay.hwnd, ctypes.byref(affinity))
    return affinity.value == WDA_EXCLUDEFROMCAPTURE
