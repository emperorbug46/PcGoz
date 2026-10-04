"""Pano oku/yaz ve pano uzerinden yapistirma.

Uzun metni harf harf yazmak yerine panoya koyup Ctrl+V yapmak hem cok daha
hizli hem de girdi kuyrugunu tikamiyor. Ayrica bir metin kutusunun icerigini
birebir okumanin en guvenilir yolu (UIA ValuePattern bazi uygulamalarda
kirpilmis deger doner).

Yapistirma kullanicinin panosunu odunc aliyor, o yuzden iki sey onemli:

1. Geri verilen pano alinanla ayni olmali. Eskiden sadece metin saklaniyordu:
   panoda resim ya da dosya varsa silinip yerine bos metin konuyordu.
2. Geri verme, hedef uygulama metni OKUDUKTAN sonra olmali. Eskiden sabit
   150 ms bekleniyordu; hedef o surede okumadiysa kullanicinin eski panosu
   yapistiriliyordu. Artik metin gecikmeli sunumla (delayed rendering)
   konuyor: hedef okumaya kalkinca Windows bize WM_RENDERFORMAT gonderiyor,
   yani okumanin ani kesin olarak biliniyor.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, Collection

from .windows import process_name

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

WM_RENDERFORMAT = 0x0305
WM_RENDERALLFORMATS = 0x0306
HWND_MESSAGE = -3
PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF

# restype verilmezse 64 bit modul taniticisi int'e kirpilir (inputguard'da
# SetWindowsHookEx'i WinError 126 ile bozuyordu); pencere sinifi icin de dogrusu bu.
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
user32.EnumClipboardFormats.restype = wintypes.UINT
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
user32.GetOpenClipboardWindow.restype = wintypes.HWND
user32.GetClipboardOwner.restype = wintypes.HWND

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class ClipboardBusy(RuntimeError):
    """Panoyu baska bir uygulama tutuyor."""


def _open(hwnd: int | None = None, attempts: int = 12) -> None:
    """Panoyu ac. Baska bir uygulama tutuyor olabilir, birkac kez dene."""
    for index in range(attempts):
        if user32.OpenClipboard(hwnd):
            return
        time.sleep(0.03 * (index + 1))
    raise ClipboardBusy(
        "Pano acilamadi -- baska bir uygulama tutuyor olabilir. "
        "Birkac saniye sonra tekrar dene."
    )


def _global_copy(data: bytes) -> int:
    """Bayt dizisini panoya verilebilecek yeni bir HGLOBAL'e kopyala."""
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(1, len(data)))
    if not handle:
        raise OSError("GlobalAlloc basarisiz")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise OSError("GlobalLock basarisiz")
    try:
        ctypes.memmove(pointer, data, len(data))
    finally:
        kernel32.GlobalUnlock(handle)
    return handle


def _set(fmt: int, data: bytes) -> None:
    """Pano ACIKKEN bir bicim koy. Basariliysa bellek artik panonun."""
    handle = _global_copy(data)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        raise OSError(f"SetClipboardData({fmt}) basarisiz")


def _text_bytes(text: str) -> bytes:
    return text.encode("utf-16-le") + b"\0\0"


def get_text() -> str:
    _open()
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.c_wchar_p(pointer).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_text(text: str) -> None:
    _open()
    try:
        user32.EmptyClipboard()
        _set(CF_UNICODETEXT, _text_bytes(text))
    finally:
        user32.CloseClipboard()


# --------------------------------------------------------------------------
# Panonun tamamini saklayip geri koyma
# --------------------------------------------------------------------------

def _is_handle_format(fmt: int) -> bool:
    """Verisi HGLOBAL degil, GDI/ozel tanitici olan bicimler -- kopyalanamaz.

    CF_BITMAP (2), CF_METAFILEPICT (3), CF_PALETTE (9), CF_ENHMETAFILE (14),
    sahibin cizdigi bicimler (0x80-0x8E) ve ozel/GDI araliklari (0x200-0x3FF).
    Resimde kayip yok: CF_BITMAP yaninda her zaman CF_DIB de listeleniyor,
    o kopyalaniyor ve Windows CF_BITMAP'i ondan yeniden uretiyor.
    """
    return fmt in (2, 3, 9, 14) or 0x80 <= fmt <= 0x8E or 0x200 <= fmt <= 0x3FF


@dataclass
class Snapshot:
    items: list[tuple[int, bytes]] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(len(data) for _, data in self.items)


def snapshot() -> Snapshot:
    """Panodaki butun kopyalanabilir bicimlerin bayt kopyasi."""
    _open()
    try:
        items: list[tuple[int, bytes]] = []
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if _is_handle_format(fmt):
                continue
            handle = user32.GetClipboardData(fmt)
            if not handle:
                continue
            size = kernel32.GlobalSize(handle)
            pointer = kernel32.GlobalLock(handle) if size else None
            if not pointer:
                continue
            try:
                items.append((fmt, ctypes.string_at(pointer, size)))
            finally:
                kernel32.GlobalUnlock(handle)
        return Snapshot(items)
    finally:
        user32.CloseClipboard()


#: Bu bicimleri goren pano gecmisi (Win+V) ve bulut panosu icerigi almaz.
#: Yapistirma icin konan gecici metin gecmiste iz birakmasin; geri konan
#: kullanici icerigi de gecmiste ikinci kez gorunmesin.
_PRIVACY_FORMATS = (
    ("ExcludeClipboardContentFromMonitorProcessing", b"\0\0\0\0"),
    ("CanIncludeInClipboardHistory", b"\0\0\0\0"),      # DWORD 0
    ("CanUploadToCloudClipboard", b"\0\0\0\0"),         # DWORD 0
)


def _mark_private() -> None:
    for name, data in _PRIVACY_FORMATS:
        _set(user32.RegisterClipboardFormatW(name), data)


def restore(saved: Snapshot, *, owner: int | None = None) -> None:
    """Saklanan panoyu geri koy. Pano bossa bos birakir (bos metin koymaz)."""
    _open(owner)
    try:
        user32.EmptyClipboard()
        for fmt, data in saved.items:
            try:
                _set(fmt, data)
            except OSError:
                continue          # tek bir bicim yuzunden gerisini kaybetme
        if saved.items:
            _mark_private()
    finally:
        user32.CloseClipboard()


# --------------------------------------------------------------------------
# Gecikmeli sunumla yapistirma
# --------------------------------------------------------------------------

class _Owner:
    """Pano sahibi gizli pencere. Hedef metni okumaya kalkinca haber alir.

    Mesaj-yalniz pencere (HWND_MESSAGE): ekranda yok, sadece mesaj aliyor.
    Iplik basina bir tane -- mesajlari ancak onu yaratan iplik isleyebilir.
    """

    def __init__(self) -> None:
        self.text: str | None = None
        self.reads: list[tuple[float, int]] = []      # (zaman, okuyan surec)
        #: Bu surece metin VERILMEZ; gecikmeli sunum beklemede kalir, sonraki
        #: okuyucu (asil hedef) yine WM_RENDERFORMAT tetikler.
        self.refuse: Callable[[int], bool] | None = None
        self._proc = WNDPROC(self._wndproc)           # referans canli kalmali
        instance = kernel32.GetModuleHandleW(None)
        name = f"PcGozPanoSahibi{threading.get_ident()}"
        wc = WNDCLASSW(lpfnWndProc=self._proc, lpszClassName=name, hInstance=instance)
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, name, name, 0, 0, 0, 0, 0,
                                           wintypes.HWND(HWND_MESSAGE), None, instance, None)
        if not self.hwnd:
            raise OSError(f"pano sahibi pencere olusturulamadi (WinError {ctypes.get_last_error()})")

    def _render(self) -> None:
        if self.text is not None:
            _set(CF_UNICODETEXT, _text_bytes(self.text))

    def _wndproc(self, hwnd, msg, wparam, lparam):  # noqa: ANN001, ANN202
        if msg == WM_RENDERFORMAT and wparam == CF_UNICODETEXT:
            # Istek sirasinda pano okuyanda acik; kim oldugunu oradan ogreniyoruz.
            reader = user32.GetOpenClipboardWindow()
            pid = wintypes.DWORD()
            if reader:
                user32.GetWindowThreadProcessId(reader, ctypes.byref(pid))
            self.reads.append((time.monotonic(), int(pid.value)))
            if self.refuse is None or not self.refuse(int(pid.value)):
                self._render()
            return 0
        if msg == WM_RENDERALLFORMATS:
            # Pencere yok ediliyor ama metin hala panoda bekliyor: simdi koy.
            if user32.OpenClipboard(hwnd):
                try:
                    if user32.GetClipboardOwner() == hwnd:
                        self._render()
                finally:
                    user32.CloseClipboard()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_owners = threading.local()


def _owner() -> _Owner:
    owner = getattr(_owners, "value", None)
    if owner is None:
        owner = _owners.value = _Owner()
    return owner


def _pump(seconds: float, until: Callable[[], bool] | None = None) -> bool:
    """Bu ipligin mesajlarini isle (WM_RENDERFORMAT buraya gelir).

    Doner: `until` saglandi mi. Uyumak yerine bu gerekli -- okuyan uygulama
    GetClipboardData icinde bizim cevabimizi bekliyor; mesaj islenmezse o da
    bekler ve sonunda bos veri alir.
    """
    deadline = time.monotonic() + seconds
    msg = wintypes.MSG()
    while True:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if until is not None and until():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        user32.MsgWaitForMultipleObjects(0, None, False, int(min(remaining, 0.01) * 1000) or 1,
                                         QS_ALLINPUT)


#: Metni panoya koyduktan sonra, tetiklemeden once taninan sure. Panoyu
#: izleyen uygulamalar ilk okumalarini bu arada yapsin -- tetiklemeden sonra
#: gelen okuma hedefe sayiliyor.
_SETTLE_S = 0.03

#: Hedef okuduktan sonra ayni yapistirma icin baska bicim isteyebilir
#: (CF_TEXT gibi, bizim metnimizden turetilir). Kisa bir pay.
_AFTER_READ_S = 0.05


@dataclass
class PasteResult:
    read: bool                 # hedef zaman asimindan once metni okudu mu
    read_ms: int | None        # tetiklemeden okumaya kadar gecen sure
    reader_pid: int | None     # okuyan surec (bilinebildiyse)
    restored_formats: int      # geri konan bicim sayisi
    others: list[str] = field(default_factory=list)   # hedef sayilmayan okuyucular


#: Panodaki her degisikligi kendiliginden okuyan arka plan surecleri. Olculdu
#: (Windows 11 26H2, 2026-09-29): CrossDeviceService.exe (cihazlar arasi pano,
#: MicrosoftWindows.CrossDevice paketi) her degisiklikten ~200 ms sonra okuyor;
#: ExcludeClipboardContentFromMonitorProcessing, CanUploadToCloudClipboard ve
#: "Clipboard Viewer Ignore" isaretlerinin hicbirini dinlemiyor. Tetiklemeden
#: sonra geldigi icin hedefin okumasi saniliyordu: pano erken geri konuyor,
#: hedefe KULLANICININ ESKI PANOSU yapistiriliyordu (tests/pano.py 3 hata).
#:
#: Artik bunlara metin HIC verilmiyor (WM_RENDERFORMAT'a veri koymadan
#: donuluyor). Olculdu: gecikmeli sunum beklemede kaliyor, 600 ms sonra okuyan
#: asil hedef yine yakalaniyor ve dogru metni aliyor; gecici metin de cihazlar
#: arasi panoya gitmiyor. svchost (Pano Kullanici Hizmeti, gecmis/bulut) gizlilik
#: isaretlerini dinliyor -- isaretsiz denemede okudu -- ama hicbir zaman bir
#: yapistirma hedefi olamaz, o yuzden o da listede.
BACKGROUND_READERS = frozenset({"crossdeviceservice.exe", "svchost.exe"})


def _background(pid: int) -> bool:
    return bool(pid) and process_name(pid).lower() in BACKGROUND_READERS


def paste(text: str, trigger: Callable[[], None], *, timeout: float = 2.0,
          readers: Collection[int] | None = None) -> PasteResult:
    """Metni panoya koy, `trigger` ile yapistirt, hedef okuyunca eski panoyu geri ver.

    `trigger` normalde Ctrl+V tusudur. Hedef `timeout` icinde okumazsa pano
    yine geri verilir ve sonuc read=False doner -- o durumda yapistirma
    olmamis ya da gec kalip eski icerigi almis olabilir.

    `readers`: hedefin surec kimlikleri (windows.input_pids). Verilirse
    sadece onlardan gelen okuma sayilir -- baska bir surecin okumasi panoyu
    erken geri koydurup hedefe eski icerigi yapistirtmasin. Tanimadigimiz bir
    hedefte en kotu sonuc yanlis bir "okumadi" uyarisi; yanlis metin degil.
    Verilmezse bilinen arka plan okuyuculari (BACKGROUND_READERS) sayilmaz.
    """
    wanted = set(readers) if readers else None

    def counts(pid: int) -> bool:
        if not pid:
            return True               # okuyan bilinmiyor: eskisi gibi hedef say
        if wanted is not None:
            return pid in wanted
        return not _background(pid)

    saved = snapshot()
    owner = _owner()
    owner.text = text
    owner.reads.clear()
    owner.refuse = _background

    _open(owner.hwnd)
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(CF_UNICODETEXT, None)      # gecikmeli sunum
        _mark_private()
    finally:
        user32.CloseClipboard()

    try:
        _pump(_SETTLE_S)
        started = time.monotonic()
        trigger()
        read = _pump(timeout, until=lambda: any(t >= started and counts(pid)
                                                for t, pid in owner.reads))
        if read:
            _pump(_AFTER_READ_S)
        first = next(((t, pid) for t, pid in owner.reads if t >= started and counts(pid)), None)
        others = sorted({process_name(pid) or str(pid) for t, pid in owner.reads
                         if t >= started and not counts(pid)})
    finally:
        owner.text = None
        owner.refuse = None
        restore(saved, owner=owner.hwnd)

    return PasteResult(
        read=read,
        read_ms=round((first[0] - started) * 1000) if first else None,
        reader_pid=(first[1] or None) if first else None,
        restored_formats=len(saved.items),
        others=others,
    )
