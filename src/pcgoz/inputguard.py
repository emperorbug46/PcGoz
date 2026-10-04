"""Eylem suresince kullanicinin FIZIKSEL fare/klavye girdisini tut.

Girdi kuyrugu paylasilan bir kaynak. PcGoz yazarken kullanici klavyeye
dokunursa harfleri araya karisir; fareyi oynatirsa tiklama/surukleme yanlis
yere gider. Simdiye kadarki tek koruma "kullanici mesgulse baslama" idi
(UserBusy): eylem basladiktan sonraki araya girmeyi engellemiyordu, ve
pc_batch / uzun isleri adim aralarinda kesiyordu.

Yontem: dusuk seviye klavye ve fare kancasi (WH_KEYBOARD_LL, WH_MOUSE_LL).
Sadece fiziksel olaylar yutulur -- PcGoz'un SendInput'u "injected" bayragi
tasidigi icin gecer. BlockInput kullanilmiyor: acil durdurma kisayolunu da
yutuyor (onu sadece Ctrl+Alt+Del cozuyor).

Guvenlik:
- Acil durdurma (Ctrl+Alt+Shift+Esc ve yedekleri) kilit sirasinda da calisir:
  kanca kisayolu kendisi yakalar, dondurur ve kilidi hemen birakir.
- Sadece BASMA ve hareket olaylari yutulur, BIRAKMA olaylari gecer. Kilit
  basladiginda Shift basili tutuluyorsa birakmasi yutulsaydi Shift takili
  kalirdi; surukleme sirasinda fare dugmesi icin de ayni sey.
- Kilit en fazla MAX_S saniye surer; kanca her olayda sureye bakar. Kanca
  ayri iplikte, kendi mesaj dongusuyle: UIA ipligi takilsa bile cevap verir.
- Kanca sadece kilit sirasinda kurulu. Python'da yazilmis kanca her fare
  olayinda GIL bekler; bosta iken kurulu durup sistemi yavaslatmasin.

0.6.0 (kullanicinin istegi): kullanici fareyi/klavyeyi kullanirken eylem artik
REDDEDILMIYOR, kilit hemen aliniyor. Bunun getirdigi uc sey:
- Basili tus/dugme: kilit anida kullanici Ctrl/Shift/Alt/Win ya da fare
  dugmesini tutuyorsa PcGoz'un yazdigi harf kisayola doner, surukledigi sey
  PcGoz'un hedefine tasinir. Once kullanicinin birakmasi beklenir (birakma
  olaylari gecer); dolarsa sentetik birakma (Alt/Win icin maske tusuyla).
- Oturumlar arasi sira: "kullanici mesgul" kurali farkinda olmadan iki Claude
  oturumunu siraya sokuyordu (birinin SendInput'u otekine insan girdisi
  gorunuyordu). Artik ortak adli mutex (Local\\PcGozGirdiKilidi).
- Sol ustte uyari (overlay.py): kullanici farenin neden kipirdamadigini gorur.
"""

from __future__ import annotations

import ctypes
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes
from typing import Iterator

from . import config, overlay, safety

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
MOUSE_RELEASES = frozenset({0x0202, 0x0205, 0x0208, 0x020C})    # L/R/M/X BUTTONUP
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
WM_APP = 0x8000
_INSTALL, _REMOVE = WM_APP + 1, WM_APP + 2

#: Degistiriciler (sol/sag) -> genel ad. Acil durdurma kombinasyonunu izlemek icin.
_MODIFIERS = {0x10: "shift", 0xA0: "shift", 0xA1: "shift", 0x11: "ctrl", 0xA2: "ctrl",
              0xA3: "ctrl", 0x12: "alt", 0xA4: "alt", 0xA5: "alt"}
#: Ctrl+Alt+Shift + bunlardan biri = acil durdurma (safety.HOTKEY_CANDIDATES).
_STOP_KEYS = frozenset(vk for _, _, vk in safety.HOTKEY_CANDIDATES)

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
# restype verilmezse 64 bit modul taniticisi int'e kirpiliyor ve SetWindowsHookEx
# WinError 126 (modul bulunamadi) veriyor -- olculdu.
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

#: Oturumlar arasi girdi sirasi. Testler kendi adlarini verebilir (_Guard).
MUTEX_NAME = "Local\\PcGozGirdiKilidi"
WAIT_OBJECT_0, WAIT_ABANDONED, WAIT_TIMEOUT = 0x0, 0x80, 0x102

#: Kilit anida basili olup olmadigina bakilan ANLIK tuslar: sol/sag Shift,
#: Ctrl, Alt, Win. CapsLock/NumLock/ScrollLock YOK: onlar ac/kapa tusu,
#: birakma gondermek durumlarini tersine cevirir. AltGr = LCtrl + RAlt.
MOMENTARY_VKS = (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)
#: Tek basina bas-birak yan etkisi olanlar: Alt menu cubugunu, Win Baslat'i acar.
_NEEDS_MASK = frozenset({0xA4, 0xA5, 0x5B, 0x5C})
_EXTENDED_MODS = frozenset({0xA3, 0xA5, 0x5B, 0x5C})
#: Atanmamis sanal tus: Alt/Win birakmasindan once bas-birak, menu/Baslat acilmasin.
MASK_VK = 0xE8
#: Fare dugmeleri (VK) -> (birakma bayragi, mouseData). GetAsyncKeyState fiziksel
#: dugmeye bakar; SendInput da donanim gibi islenir -- sol/sag degisimi uygulanir.
_MOUSE_UP = {0x01: (0x0004, 0), 0x02: (0x0010, 0), 0x04: (0x0040, 0),
             0x05: (0x0100, 1), 0x06: (0x0100, 2)}


def held_inputs() -> list[int]:
    """Su anda basili anlik tuslar ve fare dugmeleri (VK kodlari)."""
    return [vk for vk in (*MOMENTARY_VKS, *_MOUSE_UP)
            if user32.GetAsyncKeyState(vk) & 0x8000]


def release_plan(pressed: list[int]) -> list[tuple[str, int]]:
    """Basili kalanlari birakmak icin gonderilecek olaylar: ("tus-bas"|"tus-birak"|"fare", vk).

    Alt ya da Win varsa ONCE maske tusu bas-birak -- yoksa tek basina birakma
    menu cubugunu / Baslat menusunu acar ve odagi calar.
    """
    keys = [vk for vk in pressed if vk in MOMENTARY_VKS]
    plan: list[tuple[str, int]] = []
    if any(vk in _NEEDS_MASK for vk in keys):
        plan += [("tus-bas", MASK_VK), ("tus-birak", MASK_VK)]
    plan += [("tus-birak", vk) for vk in keys]
    plan += [("fare", vk) for vk in pressed if vk in _MOUSE_UP]
    return plan


class _Guard:
    def __init__(self, mutex_name: str = MUTEX_NAME) -> None:
        self.mutex_name = mutex_name
        self._mutex = 0
        self.owned = False                 # ortak kilit bizde mi (kanca ipligi)
        self.busy = False                  # son deneme baska oturum yuzunden doldu mu
        self.released_held: list[int] = []  # son kilitte sentetik birakilanlar (test icin)
        self.depth = 0
        self.deadline = 0.0
        self.swallowed = 0                 # yutulan fiziksel olay (bilgi/test icin)
        self.error: str | None = None
        self._held: set[str] = set()       # fiziksel basili degistiriciler
        self._hooks: list[int] = []
        self._lock = threading.Lock()
        self._ack = threading.Event()
        self._thread_id = 0
        self._ready = threading.Event()
        self._kb = HOOKPROC(self._keyboard)     # referanslar canli kalmali
        self._ms = HOOKPROC(self._mouse)

    # -- kanca ipligi ------------------------------------------------------

    def _ensure_thread(self) -> None:
        if self._thread_id:
            return
        threading.Thread(target=self._loop, name="pcgoz-girdi-kilidi", daemon=True).start()
        self._ready.wait(2)

    def _loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)      # mesaj kuyrugunu olustur
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == _INSTALL:
                self._own_session()
                if not self.busy:
                    self._install()
                self._ack.set()
            elif msg.message == _REMOVE:
                self._remove()
                self._release_session()
                self._ack.set()
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

    def _install(self) -> None:
        if self._hooks:
            return
        module = kernel32.GetModuleHandleW(None)
        for kind, proc in ((WH_KEYBOARD_LL, self._kb), (WH_MOUSE_LL, self._ms)):
            hook = user32.SetWindowsHookExW(kind, proc, module, 0)
            if hook:
                self._hooks.append(hook)
            else:
                self.error = f"SetWindowsHookEx({kind}) basarisiz (WinError {ctypes.get_last_error()})"

    def _remove(self) -> None:
        for hook in self._hooks:
            user32.UnhookWindowsHookEx(hook)
        self._hooks.clear()
        self._held.clear()

    def _own_session(self) -> None:
        """Ortak girdi kilidini al (kanca ipliginde: mutex iplige bagli)."""
        self.busy = False
        if not self._mutex:
            self._mutex = safety.create_shared("mutex", self.mutex_name)
        if not self._mutex:
            self.owned = False              # acilamadi: sirasiz devam (eski davranis)
            return
        result = kernel32.WaitForSingleObject(self._mutex, int(config.SESSION_WAIT_S * 1000))
        # WAIT_ABANDONED: sahibi surec coktu -- kilit artik bizim.
        self.owned = result in (WAIT_OBJECT_0, WAIT_ABANDONED)
        self.busy = result == WAIT_TIMEOUT

    def _release_session(self) -> None:
        if self.owned:
            kernel32.ReleaseMutex(self._mutex)
            self.owned = False

    def _request(self, message: int, timeout: float = 1.0) -> None:
        self._ack.clear()
        user32.PostThreadMessageW(self._thread_id, message, 0, 0)
        self._ack.wait(timeout)

    # -- kancalar (kanca ipliginde calisir; hizli donmeli) --------------------

    def active(self) -> bool:
        return self.depth > 0 and time.monotonic() < self.deadline

    def _keyboard(self, code, wparam, lparam):  # noqa: ANN001, ANN202
        if code == 0 and self.active():
            info = KBDLLHOOKSTRUCT.from_address(lparam)
            if not info.flags & LLKHF_INJECTED:
                down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                name = _MODIFIERS.get(info.vkCode)
                if name:
                    (self._held.add if down else self._held.discard)(name)
                if not down:
                    return user32.CallNextHookEx(None, code, wparam, lparam)   # birakma gecer
                if info.vkCode in _STOP_KEYS and {"ctrl", "alt", "shift"} <= self._held:
                    # Kilit RegisterHotKey'i de yutuyor; acil durdurmayi burada yap.
                    safety.freeze("kilit sirasinda acil durdurma kisayolu")
                    self.deadline = 0.0
                    overlay.show("durdu")
                self.swallowed += 1
                return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _mouse(self, code, wparam, lparam):  # noqa: ANN001, ANN202
        if code == 0 and self.active() and wparam not in MOUSE_RELEASES:
            info = MSLLHOOKSTRUCT.from_address(lparam)
            if not info.flags & LLMHF_INJECTED:
                self.swallowed += 1
                return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    # -- kilit -----------------------------------------------------------------

    def acquire(self, seconds: float) -> None:
        with self._lock:
            self.depth += 1
            if self.depth > 1:
                self.deadline = max(self.deadline, time.monotonic() + seconds)
                return
            self._ensure_thread()
            # Baska bir PcGoz oturumu eylemdeyse burada bekleniyor (en fazla SESSION_WAIT_S).
            self._request(_INSTALL, timeout=config.SESSION_WAIT_S + 2)
            if self.busy:
                self.depth -= 1
                raise safety.OtherSession(
                    "Baska bir PcGoz oturumu (baska bir Claude oturumu) su anda masaustunde "
                    f"calisiyor; {config.SESSION_WAIT_S:.0f} sn bekledim, bitmedi. Hicbir sey "
                    "gondermedim. Birkac saniye sonra tekrar dene.")
            self.deadline = time.monotonic() + seconds
            safety.INPUT_HELD.set()
            overlay.show("kilit")
            self._settle_held()
            self.deadline = max(self.deadline, time.monotonic() + seconds)

    def _settle_held(self) -> None:
        """Kullanici bir degistiriciyi ya da fare dugmesini tutuyorsa birakmasini bekle.

        Kanca birakmalari gecirip yeni basmalari yuttugu icin bekleme yeterli:
        surukledigi sey KENDI biraktigi yere duser, Alt/Win yan etkisi olmaz.
        Dolarsa sentetik birakma (release_plan).
        """
        self.released_held = []
        pressed = held_inputs()
        if not pressed:
            return
        overlay.show("bekle")
        limit = time.monotonic() + config.HELD_WAIT_S
        while pressed and time.monotonic() < limit:
            time.sleep(0.03)
            pressed = held_inputs()
        overlay.show("kilit")
        if not pressed:
            return
        from . import input as _input      # input bu modulu iceri aliyor; dongu olmasin

        events = []
        for kind, vk in release_plan(pressed):
            if kind == "fare":
                flag, data = _MOUSE_UP[vk]
                events.append(_input._mouse(flag, data=data))
            else:
                events.append(_input._key(vk, kind == "tus-birak", extended=vk in _EXTENDED_MODS))
        _input._send(*events)
        if any(vk in _MOUSE_UP for vk in pressed):
            time.sleep(0.04)                # birakma islensin, sonra fare oynasin
        self.released_held = pressed
        safety.audit("girdi_kilidi", detail="basili birakildi",
                     keys=[hex(vk) for vk in pressed])

    def release(self) -> None:
        with self._lock:
            self.depth = max(0, self.depth - 1)
            if self.depth == 0:
                self.deadline = 0.0
                safety.INPUT_HELD.clear()
                self._request(_REMOVE)


guard = _Guard()
overlay.attach(guard.active)


@contextmanager
def hold(seconds: float | None = None) -> Iterator[None]:
    """Bu blok suresince kullanicinin fiziksel girdisini tut. Ic ice kullanilabilir."""
    if not config.BLOCK_USER_INPUT or config.DRY_RUN:
        yield
        return
    guard.acquire(seconds or config.BLOCK_MAX_S)
    try:
        yield
    finally:
        guard.release()
