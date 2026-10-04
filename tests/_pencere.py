"""Testler icin duz Win32 penceresi: kendi ipliginde, kendi mesaj dongusuyle.

Gercek bir uygulamanin yerine gecer -- ne aldigini kaydeder, istenirse icine
denetim (Edit, parola kutusu) koyar. Ayri iplik sart: pencerenin mesajlari
(tus, WM_RENDERFORMAT ...) ancak o ipligin mesaj dongusu donerken islenir.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE       # 64 bit tanitici kirpilmasin

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_APP = 0x8000
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_BORDER = 0x00800000
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
ES_MULTILINE = 0x0004
ES_PASSWORD = 0x0020
ES_AUTOVSCROLL = 0x0040
ES_AUTOHSCROLL = 0x0080
ES_READONLY = 0x0800
#: Kaydirmasiz cok satirli Edit gorunen alanina sigmayan metni TUMDEN reddeder.
ES_SCROLLING_MULTILINE = ES_MULTILINE | ES_AUTOVSCROLL | ES_AUTOHSCROLL

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
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


#: handler(hwnd, msg, wparam, lparam) -> sonuc ya da None (None = varsayilan isleme)
Handler = Callable[[int, int, int, int], "int | None"]

_class_counter = 0


class TestWindow:
    def __init__(self, title: str, *, handler: Handler | None = None,
                 topmost: bool = False, noactivate: bool = False,
                 rect: tuple[int, int, int, int] = (200, 200, 420, 180),
                 edits: tuple[tuple[str, int], ...] = (),
                 buttons: tuple[str, ...] = ()) -> None:
        """edits: (metin, ek stil) -- pencerenin icine alt alta Edit kutulari.
        buttons: Edit'lerin altina yan yana dugmeler; tiklaninca handler'a
        WM_COMMAND gelir (lparam = dugmenin hwnd'si, self.buttons'ta).

        Ek stil orn. ES_PASSWORD ya da ES_MULTILINE.
        """
        global _class_counter
        _class_counter += 1
        self._class = f"PcGozTest{_class_counter}"
        self._title = title
        self._handler = handler
        self._ex = (WS_EX_TOPMOST if topmost else 0) | (WS_EX_NOACTIVATE if noactivate else 0)
        self._rect = rect
        self._edits = edits
        self._buttons = buttons
        self.hwnd = 0
        self.children: list[int] = []
        self.buttons: list[int] = []
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)      # referans canli kalmali
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or not self.hwnd:
            raise RuntimeError("test penceresi olusturulamadi")

    def _wndproc(self, hwnd, msg, wparam, lparam):  # noqa: ANN001, ANN202
        if self._handler is not None:
            result = self._handler(hwnd, msg, wparam, lparam)
            if result is not None:
                return result
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        instance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._proc, lpszClassName=self._class, hInstance=instance)
        user32.RegisterClassW(ctypes.byref(wc))
        x, y, w, h = self._rect
        self.hwnd = user32.CreateWindowExW(
            self._ex, self._class, self._title, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
            x, y, w, h, None, None, instance, None) or 0
        for index, (text, extra) in enumerate(self._edits):
            style = WS_CHILD | WS_VISIBLE | WS_BORDER | extra
            child = user32.CreateWindowExW(0, "EDIT", text, style, 10, 10 + 36 * index,
                                           w - 40, 26, self.hwnd, None, instance, None)
            self.children.append(child or 0)
        top = 10 + 36 * len(self._edits)
        for index, text in enumerate(self._buttons):
            button = user32.CreateWindowExW(0, "BUTTON", text, WS_CHILD | WS_VISIBLE,
                                            10 + 120 * index, top, 110, 28, self.hwnd,
                                            wintypes.HMENU(1000 + index), instance, None)
            self.buttons.append(button or 0)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def post(self, msg: int, wparam: int = 0, lparam: int = 0) -> None:
        user32.PostMessageW(self.hwnd, msg, wparam, lparam)

    def close(self) -> None:
        user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        self._thread.join(2)
