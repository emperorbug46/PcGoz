"""Icerik bayat mi: Chromium'un ortulu pencere hesabi, ekran ve oturum durumu.

Olculdu (27.10.2026, gecici profilli Brave, sayfa sayaci rengi birebir kodluyor):
tamamen ortulu Chromium penceresi sayfayi cizmiyor, erisilebilirlik agacini da
guncellemiyor. PrintWindow ortuldugu andaki kareyi veriyor (9 sn eskiye kadar;
siyah degil, capture bunu "kendi" cizimi sayiyordu), UIA metni de ayni anda
donuk (sayac 8 iken baslik 11). Pencere basligi guncel; UIA okumasi uyandirmiyor.

Chromium bu karari kendi hesabiyla veriyor (ui/aura/native_window_occlusion_tracker_win.cc,
ui/gfx/win/hwnd_util.cc): sanal ekrandan, z-sirasinda hedefin ustundeki "ortucu"
pencerelerin dikdortgenleri cikarilir; hedefin dikdortgeni kalanla kesismiyorsa
ortulu. Ekran kapali ya da oturum kilitliyken butun pencereleri ortulu sayilir.
Asagidaki hesap onun aynisi: uyari yalniz Chromium gercekten cizmezken cikar,
yari ortulu pencerede cikmaz.
"""

from __future__ import annotations

import atexit
import ctypes
import threading
import time
import uuid
from collections.abc import Iterable
from ctypes import wintypes

from . import windows

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
powrprof = ctypes.WinDLL("powrprof", use_last_error=True)

Rect = tuple[int, int, int, int]

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_LAYERED = 0x8, 0x20, 0x80, 0x80000
WS_EX_NOACTIVATE = 0x08000000
LWA_COLORKEY, LWA_ALPHA = 0x1, 0x2
NULLREGION, COMPLEXREGION = 1, 3
RGN_AND, RGN_DIFF = 1, 4
GW_HWNDNEXT = 2
MONITOR_DEFAULTTONEAREST = 2
DWMWA_CLOAKED = 14
EVENT_OBJECT_STATECHANGE = 0x800A
OBJID_WINDOW = 0

#: Chromium ve Electron ust duzey penceresi: tarayicilar, VS Code, Discord, Claude masaustu...
CHROMIUM_CLASS = "Chrome_WidgetWin_"

for _name in ("IsWindowVisible", "IsIconic", "IsZoomed"):
    getattr(user32, _name).argtypes = [wintypes.HWND]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetTopWindow.restype = wintypes.HWND
user32.GetTopWindow.argtypes = [wintypes.HWND]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetLayeredWindowAttributes.argtypes = [wintypes.HWND, ctypes.c_void_p,
                                              ctypes.POINTER(ctypes.c_ubyte),
                                              ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRgnBox.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.NotifyWinEvent.argtypes = [wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG]
gdi32.CreateRectRgn.restype = wintypes.HRGN
gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
gdi32.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def is_chromium(hwnd: int) -> bool:
    return windows.window_class(hwnd).startswith(CHROMIUM_CLASS)


def _cloaked(hwnd: int) -> bool:
    value = wintypes.DWORD()
    hr = dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_CLOAKED,
                                      ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0 and value.value != 0


def _window_rect(hwnd: int) -> Rect | None:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _fit(rect: Rect, bounds: Rect) -> Rect:
    """gfx::Rect::AdjustToFit: dikdortgeni kaydirip kucultup sinirin icine sigdir."""
    axes = []
    for lo, hi, blo, bhi in ((rect[0], rect[2], bounds[0], bounds[2]),
                             (rect[1], rect[3], bounds[1], bounds[3])):
        size = min(bhi - blo, hi - lo)
        origin = blo if lo < blo else min(bhi, lo + size) - size
        axes.append((origin, origin + size))
    (left, right), (top, bottom) = axes
    return (left, top, right, bottom)


def _occluder_rect(hwnd: int) -> Rect | None:
    """Chromium'a gore bu pencere alttakini orter mi? Doner: orttugu dikdortgen ya da None.

    gfx::IsWindowVisibleAndFullyOpaque'un aynisi: gorunmeyen, simge durumundaki,
    tiklama geciren, arac penceresi (gorev cubugu haric), yari saydam ya da renk
    anahtarli katmanli, karmasik bolgeli, gizlenmis (cloaked), bos ve ipucu
    pencereleri ortmez. Buyutulmus pencerenin tasan kenarligi calisma alanina kirpilir.
    """
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return None
    ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if ex_style & WS_EX_TRANSPARENT:
        return None
    cls = windows.window_class(hwnd)
    if ex_style & WS_EX_TOOLWINDOW and cls != "Shell_TrayWnd":
        return None
    if ex_style & WS_EX_LAYERED:
        alpha, flags = ctypes.c_ubyte(), wintypes.DWORD()
        # UpdateLayeredWindow ile cizilen pencerede basarisiz: saydam sayilir.
        if not user32.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(alpha), ctypes.byref(flags)):
            return None
        if (flags.value & LWA_ALPHA and alpha.value < 255) or flags.value & LWA_COLORKEY:
            return None
    box = wintypes.RECT()
    if user32.GetWindowRgnBox(hwnd, ctypes.byref(box)) == COMPLEXREGION:
        return None
    if _cloaked(hwnd):
        return None
    rect = _window_rect(hwnd)
    if rect is None:
        return None
    if user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_POPUP and "tooltip" in cls.lower():
        return None
    if user32.IsZoomed(hwnd):
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            work = info.rcWork
            rect = _fit(rect, (work.left, work.top, work.right, work.bottom))
    return rect


def _virtual_screen() -> Rect:
    x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    return (x, y, x + user32.GetSystemMetrics(78), y + user32.GetSystemMetrics(79))


def _occluded(target: Rect, above: Iterable[Rect], screen: Rect) -> bool:
    """Ustteki ortuculer cikarilinca ekrandan hedefe dusen bir parca kaldi mi? Kalmadiysa ortulu.

    Bolge kurulamazsa (GDI kaynagi bitmis) CombineRgn ERROR doner: ortulu
    denmez, uyari cikmaz.
    """
    free = gdi32.CreateRectRgn(*screen)
    try:
        for rect in above:
            cut = gdi32.CreateRectRgn(*rect)
            gdi32.CombineRgn(free, free, cut, RGN_DIFF)
            gdi32.DeleteObject(cut)
        own = gdi32.CreateRectRgn(*target)
        try:
            return gdi32.CombineRgn(own, own, free, RGN_AND) == NULLREGION
        finally:
            gdi32.DeleteObject(own)
    finally:
        gdi32.DeleteObject(free)


def chromium_hidden(hwnd: int) -> str | None:
    """Chromium bu ust duzey pencereyi gizli sayip cizmeyi birakir mi? Doner: neden ya da None.

    "simge": simge durumunda; "gizli": gorunmez ya da baska sanal masaustunde
    (cloaked); "ortulu": z-sirasinda ustundekiler tamamen ortuyor; "ust_hep_ustte":
    hep ustte duran pencereler tek basina ortuyor -- one almak acmaz, pc_focus
    onerisi okuma/one alma dongusune sokardi (inceleme, 27.10.2026).
    """
    if user32.IsIconic(hwnd):
        return "simge"
    if not user32.IsWindowVisible(hwnd) or _cloaked(hwnd):
        return "gizli"
    above: list[Rect] = []
    pinned: list[Rect] = []         # WS_EX_TOPMOST ortuculer
    current = user32.GetTopWindow(None)
    for _ in range(4096):           # liste gezilirken degisebilir: sonsuz donmesin
        if not current or current == hwnd:
            break
        rect = _occluder_rect(current)
        if rect:
            above.append(rect)
            if user32.GetWindowLongW(current, GWL_EXSTYLE) & WS_EX_TOPMOST:
                pinned.append(rect)
        current = user32.GetWindow(current, GW_HWNDNEXT)
    if current != hwnd:
        return None                 # hedef z-sirasinda bulunamadi: bilinmiyor, uyarma
    own = _occluder_rect(hwnd) or _window_rect(hwnd)
    screen = _virtual_screen()
    if own is None or not _occluded(own, above, screen):
        return None
    target_pinned = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST
    if not target_pinned and pinned and _occluded(own, pinned, screen):
        return "ust_hep_ustte"
    return "ortulu"


def wake() -> None:
    """Chromium'a ortulu pencere hesabini simdi yenilet.

    Zaten on plandaki pencere BringWindowToTop ile ortunun ustune ciksa da olay
    uretmiyor (on plan degismedi): Chromium kendini ortulu sanip >=2,5 sn cizmeden
    kaliyordu (27.10.2026). Chromium SHOW/HIDE'i yok sayiyor, STATECHANGE'te butun
    pencerelerini yeniden hesapliyor. Olay hic gosterilmeyen kendi kukla
    penceremizden: hedef uygulamaya ileti gitmez (olculdu: 55 ms'de uyandi).
    """
    hwnd = user32.CreateWindowExW(WS_EX_NOACTIVATE, "STATIC", "pcgoz-uyandir", 0,
                                  -32000, -32000, 1, 1, None, None, None, None)
    if not hwnd:
        return
    try:
        user32.NotifyWinEvent(EVENT_OBJECT_STATECHANGE, hwnd, OBJID_WINDOW, 0)
    finally:
        user32.DestroyWindow(hwnd)


# --------------------------------------------------------------------------
# Oturum kilidi ve ekran durumu
# --------------------------------------------------------------------------

WTS_CURRENT_SESSION = 0xFFFFFFFF
WTS_SESSION_INFO_EX = 25
WTS_SESSIONSTATE_LOCK = 0


class WTSINFOEX_LEVEL1_W(ctypes.Structure):
    # Yalniz SessionFlags'e kadar okunuyor; LogonTime birligin 8 bayt hizasini tutar.
    _fields_ = [("SessionId", wintypes.ULONG), ("SessionState", ctypes.c_int),
                ("SessionFlags", wintypes.LONG), ("WinStationName", wintypes.WCHAR * 33),
                ("UserName", wintypes.WCHAR * 21), ("DomainName", wintypes.WCHAR * 18),
                ("LogonTime", ctypes.c_longlong)]


class WTSINFOEXW(ctypes.Structure):
    _fields_ = [("Level", wintypes.DWORD), ("Data", WTSINFOEX_LEVEL1_W)]


wtsapi32.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
                                                 ctypes.POINTER(ctypes.c_void_p),
                                                 ctypes.POINTER(wintypes.DWORD)]
wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]


def session_locked() -> bool:
    """Oturum kilitli mi? Girdi masaustu adi UAC'yi kilitten ayirmiyor; Chromium yalniz kilitte durur."""
    buf, size = ctypes.c_void_p(), wintypes.DWORD()
    if not wtsapi32.WTSQuerySessionInformationW(None, WTS_CURRENT_SESSION, WTS_SESSION_INFO_EX,
                                                ctypes.byref(buf), ctypes.byref(size)):
        return False
    try:
        if size.value < ctypes.sizeof(WTSINFOEXW):
            return False
        info = ctypes.cast(buf, ctypes.POINTER(WTSINFOEXW)).contents
        return info.Level == 1 and info.Data.SessionFlags == WTS_SESSIONSTATE_LOCK
    finally:
        wtsapi32.WTSFreeMemory(buf)


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


GUID_CONSOLE_DISPLAY_STATE = _GUID.from_buffer_copy(
    uuid.UUID("6fe69556-704a-47a0-8f24-c28d936fda47").bytes_le)
DEVICE_NOTIFY_CALLBACK = 2
PBT_POWERSETTINGCHANGE = 0x8013


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", _GUID), ("DataLength", wintypes.DWORD), ("Data", ctypes.c_ubyte * 4)]


_POWER_CALLBACK = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p)


class DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS(ctypes.Structure):
    _fields_ = [("Callback", _POWER_CALLBACK), ("Context", ctypes.c_void_p)]


powrprof.PowerSettingRegisterNotification.restype = wintypes.DWORD
powrprof.PowerSettingRegisterNotification.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD,
                                                      ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
powrprof.PowerSettingUnregisterNotification.argtypes = [ctypes.c_void_p]

#: Ekranin son bilinen durumu (0 kapali, 1 acik, 2 karartilmis). Geri cagri
#: sistemin bir ipliginde gelir; kayit ve geri cagri nesnesi surec boyunca yasar.
_display: dict[str, object] = {"state": None, "registered": False}
_display_lock = threading.Lock()


def _on_power(_context: int | None, kind: int, setting: int | None) -> int:
    if kind == PBT_POWERSETTINGCHANGE and setting:
        info = ctypes.cast(setting, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
        if bytes(info.PowerSetting) == bytes(GUID_CONSOLE_DISPLAY_STATE) and info.DataLength >= 4:
            _display["state"] = int.from_bytes(bytes(info.Data), "little")
    return 0


_power_callback = _POWER_CALLBACK(_on_power)


def display_state() -> int | None:
    """Ekran durumu: 0 kapali, 1 acik, 2 karartilmis; None: bilinmiyor.

    Ilk cagrida GUID_CONSOLE_DISPLAY_STATE'e geri cagriyla kaydolunur (pencere
    gerekmez); Windows kayitta o anki degeri hemen gonderir, o yuzden en cok
    0,2 sn beklenir. Sonraki cagrilar bekletmez.
    """
    with _display_lock:
        if not _display["registered"]:
            _display["registered"] = True
            params = DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS(_power_callback, None)
            handle = ctypes.c_void_p()
            if powrprof.PowerSettingRegisterNotification(
                    ctypes.byref(GUID_CONSOLE_DISPLAY_STATE), DEVICE_NOTIFY_CALLBACK,
                    ctypes.byref(params), ctypes.byref(handle)) == 0:
                _display["params"], _display["handle"] = params, handle
                # Yorumlayici kapanirken gelen bildirim serbest kalmis geri cagriya dusmesin.
                atexit.register(powrprof.PowerSettingUnregisterNotification, handle)
                deadline = time.monotonic() + 0.2
                while _display["state"] is None and time.monotonic() < deadline:
                    time.sleep(0.01)
    state = _display["state"]
    return state if isinstance(state, int) else None


# --------------------------------------------------------------------------
# Modele giden uyari
# --------------------------------------------------------------------------

_FROZEN = "Chromium bu durumda sayfayi cizmez, metnini de guncellemez -- icerik (goruntu ve metin)"
_HIDDEN_AS = {"simge": "simge durumunda", "gizli": "gizli ya da baska sanal masaustunde",
              "ortulu": "tamamen ortulu",
              "ust_hep_ustte": "hep ustte duran pencere(ler)in altinda tamamen ortulu"}
_HIDDEN_FIX = {"ust_hep_ustte": "pc_focus bunu acmaz: kullanicidan ustteki pencereyi (or. Gorev "
                                "Yoneticisi, resim icinde resim) kaldirmasini iste ya da pc_window ile tasi."}
SECURE_DESKTOP_NOTE = ("[!] GUVENLI MASAUSTU acik (UAC / Ctrl+Alt+Del / kilit ekrani): ekran "
                       "goruntusu onu gostermez, kararmis ya da eski olabilir. Kullanici yanitlamali.")


def stale_reason(hwnd: int | None, *, image: bool) -> tuple[str, str] | None:
    """Hedefin icerigi bayat olabilir mi? Doner: (gunluk anahtari, modele giden satir) ya da None.

    image: goruntu alan arac (ekran goruntusu, OCR). Metin (UIA) araclarinda
    yalniz Chromium pencereleri bayatlar.
    """
    chromium = bool(hwnd) and windows.is_window(hwnd) and is_chromium(hwnd)
    if image and (windows.input_desktop_name() or "").lower() != "default":
        return "guvenli_masaustu", SECURE_DESKTOP_NOTE
    if not (image or chromium):
        return None
    if chromium and session_locked():
        return "kilitli", f"[!] Oturum kilitli: {_FROZEN} kilitlenmeden onceki hal olabilir."
    if display_state() == 0:
        # 25.10.2026: ekran zaman asimiyla kapaliyken gorev cubugu saati de geri kalmisti --
        # Chromium disinda da goruntu bayat. Fareyi oynatmak ekrani aciyor.
        wake_hint = (" Ekrani acmak icin fareyi oynat: pc_do action='hover' coordinate=[x, y] "
                     "(ekranda herhangi bir nokta).")
        if chromium:
            return "ekran_kapali", f"[!] Ekran kapali: {_FROZEN} ekran kapanmadan onceki hal olabilir.{wake_hint}"
        return "ekran_kapali", f"[!] Ekran kapali: goruntu bayat olabilir.{wake_hint}"
    why = chromium_hidden(hwnd) if chromium else None      # type: ignore[arg-type]
    if why:
        fix = _HIDDEN_FIX.get(why, "Guncel hal icin once pc_focus.")
        return why, f"[!] Bu Chromium penceresi {_HIDDEN_AS[why]}: {_FROZEN} bu hale geldigi andan kalma olabilir. {fix}"
    return None
