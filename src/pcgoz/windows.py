"""Ust duzey pencere listesi, odaklama ve surec bilgisi."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

GW_OWNER = 4
SW_RESTORE = 9
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.IsWindowVisible.restype = wintypes.BOOL
user32.WindowFromPoint.argtypes = [wintypes.POINT]   # POINT deger olarak gecer
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    cls: str
    pid: int
    process: str
    rect: tuple[int, int, int, int]
    minimized: bool
    foreground: bool
    elevated: bool

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(ctypes.c_void_p(hwnd)))


def window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, length + 1)
    return buf.value


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    return int(pid.value)


def process_name(pid: int) -> str:
    """pid -> "notepad.exe". Erisilemeyen surecler icin bos dizge."""
    if not pid:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return ""
    finally:
        kernel32.CloseHandle(handle)


def is_elevated_window(hwnd: int) -> bool:
    """Pencere yonetici hakkiyla mi calisiyor?

    Yukseltilmemis bir surecten yukseltilmis bir surecin tanitici bilgisini
    acamayiz -- OpenProcess'in ERROR_ACCESS_DENIED (5) vermesi bunun isareti.
    """
    pid = window_pid(hwnd)
    if not pid:
        return False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return False
    return ctypes.get_last_error() == 5


def frame_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Gorunur cerceve dikdortgeni.

    GetWindowRect gorunmez yeniden boyutlandirma kenarligini de sayiyor
    (bu makinede olculdu: buyutulmus pencere -9,-9 1938x1038 dedi, ekran
    1920x1080). Kirpma bunu kullanirsa kayiyor, o yuzden DWM'e soruyoruz.
    """
    rect = wintypes.RECT()
    hr = dwmapi.DwmGetWindowAttribute(
        ctypes.c_void_p(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect), ctypes.sizeof(rect),
    )
    if hr != 0 or (rect.right - rect.left) <= 0:
        user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def _is_cloaked(hwnd: int) -> bool:
    """UWP uygulamalarinin gizli 'hayalet' pencerelerini eler."""
    cloaked = wintypes.DWORD()
    hr = dwmapi.DwmGetWindowAttribute(
        ctypes.c_void_p(hwnd), DWMWA_CLOAKED,
        ctypes.byref(cloaked), ctypes.sizeof(cloaked),
    )
    return hr == 0 and cloaked.value != 0


def describe(hwnd: int) -> WindowInfo:
    pid = window_pid(hwnd)
    return WindowInfo(
        hwnd=hwnd,
        title=window_text(hwnd),
        cls=window_class(hwnd),
        pid=pid,
        process=process_name(pid),
        rect=frame_rect(hwnd),
        minimized=bool(user32.IsIconic(ctypes.c_void_p(hwnd))),
        foreground=hwnd == user32.GetForegroundWindow(),
        elevated=is_elevated_window(hwnd),
    )


_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

#: Menu / acilir liste pencerelerinin sinif adlari. Klasik Win32 menusu
#: "#32768", Windows 11'in WinUI menuleri PopupWindowSiteBridge kullaniyor.
MENU_CLASSES = frozenset({
    "#32768",
    "Microsoft.UI.Content.PopupWindowSiteBridge",
    "Windows.UI.Core.CoreComponentInputSource",
    "DropDown",
    "ComboLBox",
})

#: Bir acilir pencerenin dikkate alinmasi icin gereken en kucuk alan.
_MIN_POPUP_AREA = 400


#: PcGoz'un kendi uyari penceresinin sinifi (overlay.py). Pencere listesine ve
#: "ne degisti" ozetine girmesin -- bu surecin de oteki PcGoz sureclerinin de.
OVERLAY_CLASS = "PcGozUyari"


def visible_hwnds() -> list[int]:
    found: list[int] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if (user32.IsWindowVisible(ctypes.c_void_p(hwnd)) and not _is_cloaked(hwnd)
                and window_class(hwnd) != OVERLAY_CLASS):
            found.append(hwnd)
        return True

    user32.EnumWindows(_ENUM_PROC(callback), 0)
    return found


def is_popup(hwnd: int) -> bool:
    """Bu bir menu / acilir pencere mi?

    Menuler ayri birer ust duzey pencere ama *sahipli* (GW_OWNER dolu) --
    normal pencere listesinden bu yuzden elenirler. Sag tik menusunu
    bulabilmek icin ayri bir kategori olarak toplamak gerekiyor.
    """
    if window_class(hwnd) in MENU_CLASSES:
        return True
    if not user32.GetWindow(ctypes.c_void_p(hwnd), GW_OWNER):
        return False
    left, top, right, bottom = frame_rect(hwnd)
    return (right - left) * (bottom - top) >= _MIN_POPUP_AREA


def popup_windows() -> list[WindowInfo]:
    """Su anda ekranda olan menuler ve acilir pencereler.

    Gecici olduklari icin ayri sorgulaniyor: acikken ekranin sahibi
    onlardir, girdi de onlara gider.
    """
    infos = [describe(h) for h in visible_hwnds() if is_popup(h)]
    infos = [w for w in infos if w.width * w.height >= _MIN_POPUP_AREA]
    infos.sort(key=lambda w: -(w.width * w.height))
    return infos


def list_windows(*, include_untitled: bool = True) -> list[WindowInfo]:
    """Gorunur, sahipsiz ust duzey pencereler.

    Basliksizlar da dahil: gorev cubugu (Shell_TrayWnd) basliksiz bir pencere
    ve tiklanabilir olmasi gerekiyor. Sahipli pencereler burada degil --
    onlar menu/acilir kategorisinde, popup_windows() ile.
    """
    found = [
        h for h in visible_hwnds()
        if not user32.GetWindow(ctypes.c_void_p(h), GW_OWNER)
        and (include_untitled or window_text(h))
    ]
    infos = [describe(h) for h in found]
    # Kabuk artiklarini ele: sifir alanli pencereler.
    infos = [w for w in infos if w.minimized or (w.width > 0 and w.height > 0)]
    infos.sort(key=lambda w: (not w.foreground, w.minimized, w.title.lower()))
    return infos


def foreground_window() -> int:
    return int(user32.GetForegroundWindow())


WM_CLOSE = 0x0010


def close_window(hwnd: int, *, timeout: float = 5.0) -> bool:
    """Tek bir pencereyi kapat. Doner: kapandi mi.

    Sureci oldurmek YERINE bunu kullan. Chromium'da her pencerenin
    window_pid'i ANA TARAYICI surecini gosterir; o pid'e taskkill atmak
    kullanicinin butun sekmelerini kapatir. Bir kere yapildi, bir daha
    yapilmasin diye bu fonksiyon var.
    """
    import time

    if not is_window(hwnd):
        return True
    user32.PostMessageW(ctypes.c_void_p(hwnd), WM_CLOSE, 0, 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_window(hwnd):
            return True
        time.sleep(0.1)
    return not is_window(hwnd)


SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_ASYNCWINDOWPOS = 0x4000

#: Kabuk pencereleri: bunlara pencere yonetimi uygulanmaz. Gorev cubuguna ya
#: da masaustune WM_CLOSE gondermek "Windows'u kapat" iletisim kutusunu acar.
SHELL_CLASSES = frozenset({"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"})


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_MONITOR_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
MONITORINFOF_PRIMARY = 1
MDT_EFFECTIVE_DPI = 0


@dataclass
class Monitor:
    rect: tuple[int, int, int, int]      # fiziksel piksel, sanal masaustu koordinati
    scale: int                           # yuzde (%125)
    primary: bool


def monitors() -> list[Monitor]:
    """Ekranlar: konum, olcek, birincil mi.

    Coklu monitorde koordinatlar negatif olabilir (birincilin solundaki ekran)
    ve her ekranin olcegi farkli olabilir. Model hangi koordinatin hangi
    ekrana dustugunu ancak bunu gorunce bilir.
    """
    found: list[Monitor] = []
    shcore = ctypes.WinDLL("shcore")

    def callback(handle, _hdc, _rect, _lparam):  # noqa: ANN001, ANN202
        info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
        user32.GetMonitorInfoW(handle, ctypes.byref(info))
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        if shcore.GetDpiForMonitor(handle, MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)):
            dpi_x.value = 96
        r = info.rcMonitor
        found.append(Monitor((r.left, r.top, r.right, r.bottom), round(dpi_x.value * 100 / 96),
                             bool(info.dwFlags & MONITORINFOF_PRIMARY)))
        return True

    user32.EnumDisplayMonitors(None, None, _MONITOR_PROC(callback), 0)
    found.sort(key=lambda m: (not m.primary, m.rect[0], m.rect[1]))
    return found


def is_hung(hwnd: int) -> bool:
    """Windows bu pencereyi 'yanit vermiyor' sayiyor mu (5 sn mesaj islememis)."""
    return bool(user32.IsHungAppWindow(ctypes.c_void_p(hwnd)))


WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002

#: Yoklama suresi. Cevap veren pencere 1 ms'nin altinda doner; bunu asan
#: pencere mesgul ya da donmus demektir.
RESPOND_TIMEOUT_MS = 500


def responds(hwnd: int, timeout_ms: int = RESPOND_TIMEOUT_MS) -> bool:
    """Pencere mesaj isliyor mu? Bos bir mesajla (WM_NULL) yokla.

    Donmus pencereye yapilan her senkron is -- UIA cagrisi, PrintWindow --
    pencere kendine gelene kadar bekliyordu (olculdu: UIA 20.7 sn,
    PrintWindow 14.7 sn; gercekten donmus uygulamada sonsuz). IsHungAppWindow
    ancak 5 sn sonra devreye giriyor, o yuzden dogrudan yokluyoruz.
    """
    result = ctypes.c_size_t()
    return bool(user32.SendMessageTimeoutW(
        ctypes.c_void_p(hwnd), WM_NULL, 0, 0, SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result)))


def is_minimized(hwnd: int) -> bool:
    """Pencere simge durumunda mi? show() beklemedigi icin sonucu boyle yoklanir."""
    return bool(user32.IsIconic(ctypes.c_void_p(hwnd)))


def is_visible(hwnd: int) -> bool:
    """WS_VISIBLE: simge durumundaki pencere de gorunur sayilir, tepsiye saklanan sayilmaz."""
    return bool(user32.IsWindowVisible(ctypes.c_void_p(hwnd)))


def show(hwnd: int, action: str) -> None:
    """minimize / maximize / restore. Async: askidaki pencerede bizi bekletmesin."""
    command = {"minimize": SW_MINIMIZE, "maximize": SW_MAXIMIZE, "restore": SW_RESTORE}[action]
    user32.ShowWindowAsync(ctypes.c_void_p(hwnd), command)


def post_close(hwnd: int) -> None:
    """Pencerenin X dugmesi: WM_CLOSE. Sureci oldurmez, uygulama kendi karar verir."""
    user32.PostMessageW(ctypes.c_void_p(hwnd), WM_CLOSE, 0, 0)


def move_frame(hwnd: int, x: int, y: int, width: int, height: int) -> None:
    """Pencerenin GORUNUR cercevesini (x, y, genislik, yukseklik)'ya getir.

    SetWindowPos gorunmez yeniden boyutlandirma kenarligi dahil pencere
    dikdortgeniyle calisiyor (bu makinede 7-9 px); istenen cerceve tutsun
    diye fark telafi ediliyor. Buyutulmus pencere once geri getiriliyor.
    """
    import time

    if user32.IsIconic(ctypes.c_void_p(hwnd)) or user32.IsZoomed(ctypes.c_void_p(hwnd)):
        show(hwnd, "restore")
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and (
                user32.IsIconic(ctypes.c_void_p(hwnd)) or user32.IsZoomed(ctypes.c_void_p(hwnd))):
            time.sleep(0.03)
    outer = wintypes.RECT()
    user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(outer))
    left, top, right, bottom = frame_rect(hwnd)
    dl, dt = left - outer.left, top - outer.top
    dr, db = outer.right - right, outer.bottom - bottom
    user32.SetWindowPos(ctypes.c_void_p(hwnd), None, x - dl, y - dt,
                        width + dl + dr, height + dt + db,
                        SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
    # Async gonderildi; pencere islemeden okunan konum eski olur. Kisa bekle.
    wanted = (x, y, x + width, y + height)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and frame_rect(hwnd) != wanted:
        time.sleep(0.03)


GA_ROOT = 2
GA_ROOTOWNER = 3


def root_owner(hwnd: int) -> int:
    """Sahiplik zincirinin kokundeki pencere: kalici iletisim kutusu icin sahibi
    olan ana pencere. Sahipsiz pencerede kendisi, yoksa 0."""
    return int(user32.GetAncestor(ctypes.c_void_p(hwnd), GA_ROOTOWNER) or 0)


def top_level_at(x: int, y: int) -> int:
    """Bu ekran noktasinda hangi ust duzey pencere var?

    WindowFromPoint alt kontrolu dondurur; GetAncestor(GA_ROOT) ile onu sahibi
    olan ust duzey pencereye cikariyoruz. "Buraya tiklarsam neye vururum"
    sorusunun cevabi bu.
    """
    point = wintypes.POINT(x=int(x), y=int(y))
    child = user32.WindowFromPoint(point)
    if not child:
        return 0
    root = user32.GetAncestor(ctypes.c_void_p(child), GA_ROOT)
    return int(root or child)


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


_ENUM_CHILD = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def input_pids() -> set[int]:
    """Klavye girdisini (Ctrl+V dahil) alacak sureclerin kimlikleri.

    On plan penceresinin sureci, iplik odagindaki pencerenin sureci ve
    dogrudan alt pencerelerin surecleri -- UWP'de cerceve ApplicationFrameHost'a,
    icerik (CoreWindow) uygulamanin kendi surecine ait.
    """
    fg = foreground_window()
    if not fg:
        return set()
    pids = {window_pid(fg)}
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    thread = user32.GetWindowThreadProcessId(ctypes.c_void_p(fg), None)
    if thread and user32.GetGUIThreadInfo(thread, ctypes.byref(info)):
        for hwnd in (info.hwndFocus, info.hwndActive):
            if hwnd:
                pids.add(window_pid(int(hwnd)))

    def child(hwnd, _):  # noqa: ANN001, ANN202
        pids.add(window_pid(int(hwnd)))
        return len(pids) < 16
    user32.EnumChildWindows(ctypes.c_void_p(fg), _ENUM_CHILD(child), 0)
    pids.discard(0)
    return pids


DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
user32.CloseDesktop.argtypes = [wintypes.HANDLE]


def input_desktop_name() -> str | None:
    """Girdiyi su an alan masaustunun adi: normalde "Default".

    UAC onayi, Ctrl+Alt+Del ekrani ve kilit ekrani "Winlogon" guvenli
    masaustunde cizilir; oraya SYSTEM disinda hicbir surec erisemez, yani
    OpenInputDesktop basarisiz olur -- o zaman None.
    """
    desk = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not desk:
        return None
    try:
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if not user32.GetUserObjectInformationW(desk, UOI_NAME, buf, ctypes.sizeof(buf),
                                                ctypes.byref(needed)):
            return None
        return buf.value
    finally:
        user32.CloseDesktop(desk)


WM_NCHITTEST = 0x0084
HTCAPTION = 2


def hit_test(hwnd: int, x: int, y: int, timeout_ms: int = 200) -> int | None:
    """Pencere bu ekran noktasini ne sayiyor (HT* kodu)? Cevap yoksa None."""
    lparam = ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)
    result = ctypes.c_ssize_t()
    ok = user32.SendMessageTimeoutW(ctypes.c_void_p(hwnd), WM_NCHITTEST, 0, ctypes.c_ssize_t(lparam),
                                    SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result))
    return int(result.value) if ok else None


def caption_point(hwnd: int) -> tuple[int, int] | None:
    """Tiklanmasi guvenli bir baslik cubugu noktasi: pencereyi one alir, baska bir sey yapmaz.

    Iki sart: pencere noktayi HTCAPTION (surukleme alani) sayiyor -- sekme,
    dugme, arama kutusu degil; Brave, Win11 Not Defteri, Terminal baslik
    cubugunu kendileri ciziyor -- ve o noktada gercekten bu pencere var
    (ustu baska pencereyle ortulu degil). Sag taraftan baslanir: sekmeli
    uygulamalarda bos surukleme alani genelde oradadir.
    """
    if not is_window(hwnd):
        return None
    left, top, right, bottom = frame_rect(hwnd)
    if right - left < 120 or bottom - top < 30:
        return None
    xs = list(range(right - 220, left + 60, -40)) or [(left + right) // 2]
    for dy in (8, 16, 24, 32, 40):
        y = top + dy
        for x in xs:
            if hit_test(hwnd, x, y) == HTCAPTION and top_level_at(x, y) == hwnd:
                return (x, y)
    return None


def find_window(query: str) -> list[WindowInfo]:
    """Baslik / surec / sinif icinde buyuk-kucuk harf duyarsiz alt dizge aramasi.

    Acik menuler de aranir -- bir menuyu adiyla hedefleyebilmek gerekiyor.
    """
    from .textutil import fold

    needle = fold(query)
    pool = list_windows()
    seen = {w.hwnd for w in pool}
    pool += [w for w in popup_windows() if w.hwnd not in seen]
    return [
        w for w in pool
        if needle in fold(w.title)
        or needle in fold(w.process)
        or needle in fold(w.cls)
    ]


def focus(hwnd: int) -> None:
    """Pencereyi one al.

    SetForegroundWindow'un kisitlamasi var: cagiran surec on planda degilse
    Windows istegi sessizce reddedip sadece gorev cubugunu yakip sonduruyor.
    AttachThreadInput ile giris kuyruklarini gecici olarak baglamak bunu asar.
    """
    if not is_window(hwnd):
        raise LookupError(f"hwnd {hwnd} artik yok.")
    if user32.IsIconic(ctypes.c_void_p(hwnd)):
        user32.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)

    target_thread = user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
    current_thread = kernel32.GetCurrentThreadId()
    attached = False
    if target_thread and target_thread != current_thread:
        attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
    try:
        user32.BringWindowToTop(ctypes.c_void_p(hwnd))
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, target_thread, False)
