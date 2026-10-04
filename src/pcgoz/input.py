"""SendInput ile fare ve klavye.

Iki karar bu modulun tamamini belirliyor:

1. Fare mutlak koordinatla surulur (MOUSEEVENTF_ABSOLUTE | VIRTUALDESK).
   Koordinat sanal masaustune gore 0-65535'e normalize edilir; boylece coklu
   monitor ve negatif baslangicli yerlesimler kendiliginden dogru calisir.

2. Metin KEYEVENTF_UNICODE ile yazilir (wVk=0, wScan=kod noktasi).
   Bu yol aktif klavye duzeninden bagimsizdir -- Turkce Q, Turkce F, US
   ne olursa olsun "gunaydin, sicak sut" dogru cikar. VkKeyScan tabanli
   yaklasim duzen degisince sessizce bozulur, o yuzden kullanilmiyor.
"""

from __future__ import annotations

import ctypes
import random
import time
from ctypes import wintypes

from . import config, inputguard, safety

user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

WHEEL_DELTA = 120

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
# SHORT donuyor; restype verilmezse int sayilir ve ust bitler cop olabilir.
user32.VkKeyScanExW.argtypes = (wintypes.WCHAR, ctypes.c_void_p)
user32.VkKeyScanExW.restype = ctypes.c_short
user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.c_void_p)
user32.GetWindowThreadProcessId.restype = wintypes.DWORD


# --------------------------------------------------------------------------
# Tus adlari
# --------------------------------------------------------------------------

VK: dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "clear": 0x0C, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pgup": 0x21, "pageup": 0x21, "pgdn": 0x22, "pagedown": 0x22,
    "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "select": 0x29, "print": 0x2A, "execute": 0x2B, "printscreen": 0x2C,
    "insert": 0x2D, "delete": 0x2E, "del": 0x2E, "help": 0x2F,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D, "menukey": 0x5D,
    "numlock": 0x90, "scrolllock": 0x91,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3,
    "lalt": 0xA4, "ralt": 0xA5,
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF,
    "medianext": 0xB0, "mediaprev": 0xB1, "mediastop": 0xB2, "mediaplay": 0xB3,
}
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})          # f1..f24
VK.update({str(d): 0x30 + d for d in range(10)})               # 0..9
VK.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"})
VK.update({f"num{d}": 0x60 + d for d in range(10)})            # numpad
VK.update({
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D, "decimal": 0x6E, "divide": 0x6F,
    "plus": 0xBB, "comma": 0xBC, "minus": 0xBD, "period": 0xBE,
})

MODIFIERS = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12,
             "win": 0x5B, "cmd": 0x5B, "meta": 0x5B}

#: Bu tuslar KEYEVENTF_EXTENDEDKEY olmadan numpad esdegerleri gibi davranir.
#: Unutulunca "yon tuslari calismiyor" seklinde ortaya cikar.
EXTENDED_VKS = frozenset({
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,   # pgup/pgdn/end/home/oklar
    0x2D, 0x2E, 0x2C,                                   # insert/delete/printscreen
    0x5B, 0x5C, 0x5D,                                   # win tuslari / menu
    0x6F, 0x90, 0xA3, 0xA5,                             # divide/numlock/rctrl/ralt
    0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3,          # medya tuslari
})

#: Home/End TEK BASINA gonderilirken bayraksiz gider. Bu makinede AutoHotkey
#: genisletilmis Home/End'i tutuyor: uygulamaya hicbir sey ulasmiyordu.
#: Degistiriciyle birlikte ise genisletilmis kalmak zorunda: bayraksiz hali
#: numpad 7/1 sayilir ve NumLock acikken Windows Shift'i sahte bir birakmayla
#: dusurur -- shift+home secim yapmaz. Kanca degistiricili hallere karismiyor.
#: Uc durum da tests/tus_probu.py ile olculdu.
BARE_PLAIN_VKS = frozenset({0x23, 0x24})


class KeyParseError(ValueError):
    """Anlasilmayan tus ifadesi."""


#: VkKeyScanEx'in ust baytindaki bitler -> basili tutulmasi gereken tus.
_SHIFT_STATE_VKS = ((1, 0x10), (2, 0x11), (4, 0x12))      # shift, ctrl, alt


def foreground_layout() -> int:
    """On plandaki pencerenin klavye duzeni (HKL).

    Tus o pencereye gidiyor ve onun duzeniyle yorumlaniyor; sunucu surecinin
    kendi duzeni farkli olabilir (Windows duzeni pencere basina tutabiliyor).
    """
    thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    return user32.GetKeyboardLayout(thread) or 0


def parse_combo(combo: str, *, layout: int | None = None) -> tuple[list[int], int]:
    """"ctrl+shift+p" -> ([VK_CONTROL, VK_SHIFT], VK_P)

    Tek karakterli tuslar hedef pencerenin duzeninde aranir ve o karakter icin
    gereken degistiriciler eklenir: Turkce Q'da "?" Shift ister, "@" AltGr
    (ctrl+alt) ister. Eskiden bu bilgi atiliyordu ve "?" yerine ayni tusun
    Shift'siz karakteri gidiyordu.
    """
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise KeyParseError("bos tus ifadesi")
    *mods, key = parts
    mod_vks = []
    for mod in mods:
        if mod not in MODIFIERS:
            raise KeyParseError(f"bilinmeyen degistirici: {mod!r} ({combo!r} icinde)")
        mod_vks.append(MODIFIERS[mod])
    if key in VK:
        return mod_vks, VK[key]
    if len(key) == 1:
        hkl = layout if layout is not None else foreground_layout()
        code = user32.VkKeyScanExW(key, hkl)
        if code != -1 and (code & 0xFF) != 0xFF:
            state = (code >> 8) & 0xFF
            extra = [vk for bit, vk in _SHIFT_STATE_VKS if state & bit and vk not in mod_vks]
            return mod_vks + extra, code & 0xFF
    raise KeyParseError(
        f"bilinmeyen tus: {key!r}. Ornekler: enter, esc, tab, f5, up, "
        "ctrl+s, alt+F4, ctrl+shift+p, win+d"
    )


# --------------------------------------------------------------------------
# Alt seviye gonderim
# --------------------------------------------------------------------------

def _send(*inputs: INPUT) -> None:
    if not inputs:
        return
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    safety.note_injection()      # insan girdisinden ayirt edebilmek icin
    if sent != len(inputs):
        err = ctypes.get_last_error()
        if err == 5:
            raise PermissionError(
                "SendInput reddedildi (ERROR_ACCESS_DENIED). On plandaki pencere "
                "muhtemelen yonetici hakkiyla calisiyor; bu oturum yukseltilmemis "
                "oldugu icin ona girdi gonderemem. Kullanicinin kendisi yapmali."
            )
        raise OSError(f"SendInput {len(inputs)} olaydan {sent} tanesini gonderdi (WinError {err})")


def virtual_screen() -> tuple[int, int, int, int]:
    """(x, y, genislik, yukseklik) -- fiziksel piksel, DPI farkindaligi acikken."""
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def _normalize(x: int, y: int) -> tuple[int, int]:
    """Fiziksel pikseli SendInput'un 0..65535 mutlak koordinatina cevir.

    Windows geri cevirirken asagi yuvarliyor: piksel = sol + n * genislik / 65536.
    Tam o piksele dusmek icin tavan gerekiyor. Eskiden round(x * 65535 / (w-1))
    kullaniliyordu ve imlec konumlarin ~%10'unda 1 px sapiyordu (olculdu:
    SendInput + GetCursorPos, x'te 7/68, y'de 3/39 nokta; bu formulle 0).
    """
    vx, vy, vw, vh = virtual_screen()
    nx = ((x - vx) * 65536 + vw - 1) // max(1, vw)
    ny = ((y - vy) * 65536 + vh - 1) // max(1, vh)
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def cursor_pos() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return (point.x, point.y)


def _mouse(flags: int, x: int | None = None, y: int | None = None, data: int = 0) -> INPUT:
    mi = MOUSEINPUT(dx=0, dy=0, mouseData=data & 0xFFFFFFFF, dwFlags=flags, time=0, dwExtraInfo=0)
    if x is not None and y is not None:
        mi.dx, mi.dy = _normalize(x, y)
    return INPUT(type=INPUT_MOUSE, mi=mi)


def _key(vk: int, up: bool, *, extended: bool | None = None) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if extended if extended is not None else vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    scan = user32.MapVirtualKeyW(vk, 0)
    return INPUT(type=INPUT_KEYBOARD,
                 ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0))


def _unicode_key(code: int, up: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD,
                 ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=flags, time=0, dwExtraInfo=0))


# --------------------------------------------------------------------------
# Genel API
# --------------------------------------------------------------------------

def move(x: int, y: int, *, steps: int | None = None, duration_ms: int | None = None) -> None:
    """Fareyi ara adimlarla tasi.

    Isinlama yerine adim adim gitmek onemli: hover durumlari ve ipuclari ancak
    hareket olaylariyla tetikleniyor, surukle-birak da ara hareket gormeden
    calismiyor.
    """
    if config.DRY_RUN:
        return
    with inputguard.hold():
        steps = steps if steps is not None else config.MOUSE_STEPS
        duration = duration_ms if duration_ms is not None else config.MOUSE_MOVE_MS
        x0, y0 = cursor_pos()
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        for i in range(1, max(1, steps) + 1):
            t = i / max(1, steps)
            eased = t * t * (3 - 2 * t)          # yumusak baslangic/bitis
            _send(_mouse(flags, round(x0 + (x - x0) * eased), round(y0 + (y - y0) * eased)))
            if i < steps:
                time.sleep(duration / 1000 / max(1, steps))


_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def click(x: int, y: int, *, button: str = "left", count: int = 1) -> None:
    safety.check_not_frozen()
    if button not in _BUTTONS:
        raise ValueError(f"bilinmeyen dugme: {button!r} (left/right/middle)")
    with inputguard.hold():
        move(x, y)
        if config.DRY_RUN:
            return
        down, up = _BUTTONS[button]
        for i in range(count):
            if i:
                time.sleep(0.06)          # cift tiklama esiginin altinda kal
            _send(_mouse(down), _mouse(up))


def drag(x1: int, y1: int, x2: int, y2: int, *, button: str = "left") -> None:
    safety.check_not_frozen()
    down, up = _BUTTONS[button]
    with inputguard.hold():
        move(x1, y1)
        if config.DRY_RUN:
            return
        _send(_mouse(down))
        time.sleep(0.08)
        move(x2, y2, steps=max(12, config.MOUSE_STEPS), duration_ms=220)
        time.sleep(0.08)
        _send(_mouse(up))


def scroll(x: int, y: int, direction: str, amount: int = 3) -> None:
    safety.check_not_frozen()
    with inputguard.hold():
        move(x, y)
        if config.DRY_RUN:
            return
        horizontal = direction in ("left", "right")
        sign = -1 if direction in ("down", "right") else 1
        flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
        if horizontal:
            sign = -sign          # HWHEEL'de pozitif deger saga kaydirir
        for _ in range(max(1, amount)):
            _send(_mouse(flag, data=sign * WHEEL_DELTA))
            time.sleep(0.03)


def press(combo: str, *, repeat: int = 1) -> None:
    """"ctrl+s", "alt+F4", "enter", "down" ..."""
    safety.check_not_frozen()
    mods, vk = parse_combo(combo)
    if config.DRY_RUN:
        return
    with inputguard.hold():
        extended = False if (vk in BARE_PLAIN_VKS and not mods) else None
        for i in range(max(1, repeat)):
            if i:
                safety.check_not_frozen()
                time.sleep(0.03)
            _send(*[_key(m, False) for m in mods], _key(vk, False, extended=extended))
            time.sleep(0.02)
            _send(_key(vk, True, extended=extended), *[_key(m, True) for m in reversed(mods)])


def type_text(text: str, *, delay_ms: tuple[int, int] | None = None) -> None:
    """Metni klavye duzeninden bagimsiz olarak yaz.

    Satir sonu ve sekme gercek tuslara cevrilir; Unicode 0x0A gonderilse
    cogu uygulama yeni satir uretmez.
    """
    safety.check_not_frozen()
    if config.DRY_RUN:
        return
    with inputguard.hold():
        lo, hi = delay_ms or config.TYPE_DELAY_MS
        for index, char in enumerate(text):
            if index % 16 == 0:
                safety.check_not_frozen()      # kisayol yazma ortasinda da kessin
            if char == "\n":
                _send(_key(0x0D, False), _key(0x0D, True))
            elif char == "\t":
                _send(_key(0x09, False), _key(0x09, True))
            elif char == "\r":
                continue
            else:
                code = ord(char)
                if code > 0xFFFF:              # BMP disi -> surrogate cifti
                    code -= 0x10000
                    high = 0xD800 + (code >> 10)
                    low = 0xDC00 + (code & 0x3FF)
                    _send(_unicode_key(high, False), _unicode_key(high, True),
                          _unicode_key(low, False), _unicode_key(low, True))
                else:
                    _send(_unicode_key(code, False), _unicode_key(code, True))
            time.sleep(random.uniform(lo, hi) / 1000)
