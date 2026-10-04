"""Guvenlik katmani: acil dondurma, denetim gunlugu, kimlik bilgisi blogu.

Kullanici secimi "serbest + log": onay sorusu yok. Geriye iki sey kaliyor --
her eylemin diske yazilmasi ve kullanicinin her an her seyi durdurabilmesi.
"""

from __future__ import annotations

import contextvars
import ctypes
import itertools
import json
import os
import threading
import time
from ctypes import wintypes
from typing import Any

from . import config

user32 = ctypes.WinDLL("user32", use_last_error=True)


class Frozen(RuntimeError):
    """Kullanici acil dondurmayi tetikledi."""


class Refused(RuntimeError):
    """Arac bu eylemi yapmayi reddediyor (kimlik bilgisi alani)."""


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


#: Ortak dondurma nesnesinin adi. Testler kendi adlarini kullanir (tests/_gunluk.py).
FREEZE_NAME = "Local\\PcGozAcilDurdurma"

#: Etkilesimli kullanicilar + SYSTEM tam yetki, butunluk etiketi Orta (NW): yonetici
#: hakkiyla ve normal calisan PcGoz surecleri ayni nesneyi acip degistirebilsin.
_FREEZE_SDDL = "D:(A;;GA;;;IU)(A;;GA;;;SY)S:(ML;;NW;;;ME)"

kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]


def create_shared(kind: str, name: str) -> int:
    """Oturumdaki butun PcGoz surecleri icin ortak adli nesne ("event" ya da "mutex").

    Ayni guvenlik tanimi (_FREEZE_SDDL): yonetici ve normal haklarla calisan
    surecler ayni nesneyi acar. Acilamazsa 0.
    """
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            _FREEZE_SDDL, 1, ctypes.byref(descriptor), None):
        descriptor = ctypes.c_void_p()
    attributes = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False)
    pointer = ctypes.byref(attributes) if descriptor else None
    try:
        if kind == "mutex":
            return kernel32.CreateMutexW(pointer, False, name) or 0
        return kernel32.CreateEventW(pointer, True, False, name) or 0
    finally:
        if descriptor:
            kernel32.LocalFree(descriptor)


class Freeze:
    """threading.Event arayuzu, ama oturumdaki BUTUN PcGoz surecleri icin ortak.

    Ayni anda birden fazla Claude oturumu kendi PcGoz sunucusunu calistirabilir.
    Kisayolu yalnizca biri kaydedebiliyor (digerleri yedek kisayollara duser);
    dondurma surec icinde kalsaydi Ctrl+Alt+Shift+Esc sadece o sunucuyu
    durdururdu, oteki oturum fareyi surmeye devam ederdi. Adli, elle sifirlanan
    bir olay nesnesi hepsinde ortak: biri dondurunca hepsi donar. Butun surecler
    kapaninca nesne de kaybolur -- yeniden baslatma eskisi gibi cozer.
    """

    def __init__(self, name: str = FREEZE_NAME) -> None:
        self.name = name
        self._local = threading.Event()        # ortak nesne acilamazsa yedek
        self._handle = self._open(name)

    @staticmethod
    def _open(name: str) -> int:
        return create_shared("event", name)

    @property
    def shared(self) -> bool:
        return bool(self._handle)

    def is_set(self) -> bool:
        if self._handle:
            return kernel32.WaitForSingleObject(self._handle, 0) == 0     # WAIT_OBJECT_0
        return self._local.is_set()

    def set(self) -> None:
        if self._handle:
            kernel32.SetEvent(self._handle)
        else:
            self._local.set()

    def clear(self) -> None:
        if self._handle:
            kernel32.ResetEvent(self._handle)
        else:
            self._local.clear()


_frozen = Freeze()
_log_lock = threading.Lock()

#: inputguard kilidi tutarken set: kullanicinin fiziksel girdisi yutuluyor,
#: yani araya giremez -- "kullanici mesgul" kontrolu gereksiz.
INPUT_HELD = threading.Event()
_hotkey_state: dict[str, Any] = {"registered": False, "error": None, "combo": None, "note": None}


# --------------------------------------------------------------------------
# Dondurma
# --------------------------------------------------------------------------

def is_frozen() -> bool:
    return _frozen.is_set()


def check_not_frozen() -> None:
    """Girdi ureten her aracin ilk satiri."""
    if _frozen.is_set():
        combo = _hotkey_state.get("combo") or "acil durdurma kisayolu"
        raise Frozen(
            "DONDURULDU -- kullanici acil durdurmayi tetikledi; o anda calisan butun "
            f"PcGoz oturumlari durdu. Devam etmek icin kullanicinin {combo} kisayoluna "
            "tekrar basmasi gerekiyor. Kendi basina cozemezsin, kullaniciya sor."
        )


class SecureDesktop(RuntimeError):
    """Girdi guvenli masaustunde (UAC / Ctrl+Alt+Del / kilit ekrani)."""


def check_input_desktop() -> None:
    """Girdi ureten araclarin ikinci satiri.

    Guvenli masaustu acikken SendInput sessizce bosa gidiyor, ekran goruntusu
    kararmis ya da eski geliyor; araclar sebebini soylemeden basarisiz oluyordu.
    Bunu asmak mumkun degil (tasarim geregi) -- ama soylemek mumkun.
    """
    from . import windows

    name = windows.input_desktop_name()
    if name is None or name.lower() != "default":
        where = f"'{name}'" if name else "acilamadi"
        raise SecureDesktop(
            f"GUVENLI MASAUSTU acik (girdi masaustu {where}): UAC onayi, Ctrl+Alt+Del ekrani "
            "ya da kilit ekrani. Oraya hicbir surec erisemez -- girdi gondermedim. "
            "Kullanicinin kendisi yanitlamali; kapaninca tekrar dene."
        )


class UserBusy(RuntimeError):
    """Kullanici su anda klavye/fareyi kendisi kullaniyor."""


class OtherSession(RuntimeError):
    """Baska bir PcGoz oturumu (baska bir Claude oturumunun sunucusu) eylemde."""


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


#: Kendi gonderdigimiz son girdinin tick degeri.
_last_injection_tick = 0

#: Bu kadar ms icinde gelen girdi bizim sayilir (tick cozunurlugu + gonderim payi).
_INJECTION_TOLERANCE_MS = 250


def note_injection() -> None:
    """Girdi gonderdigimiz anda cagrilir; insan girdisinden ayirt edebilmek icin."""
    global _last_injection_tick
    _last_injection_tick = int(ctypes.windll.kernel32.GetTickCount())


def human_idle_ms() -> int | None:
    """Insanin son girdisinden bu yana gecen ms. Bilinemiyorsa None.

    GetLastInputInfo bizim gonderdigimiz girdiyi de sayiyor, o yuzden kendi
    son gonderimimizin zamaniyla karsilastiriliyor: sistemdeki son girdi
    bizimkinden yeniyse, arada bir insan klavyeye dokunmus demektir.
    """
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO), dwTime=0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    now = int(ctypes.windll.kernel32.GetTickCount())

    # Kendi gonderdigimiz girdiyi insan sanmamak icin tolerans sart:
    # GetTickCount cozunurlugu ~15 ms, ve isaretimizi SendInput'tan hemen
    # once koyuyoruz. Tolerans olmadan her eylem kendinden bir onceki
    # eylemi "insan girdisi" sayar ve arka arkaya is yapmak imkansiz olur.
    delta = ctypes.c_int32(int(info.dwTime) - _last_injection_tick).value
    if delta <= _INJECTION_TOLERANCE_MS:
        return None          # en son girdi bizimdi; insan hakkinda bilgi yok
    return max(0, now - int(info.dwTime))


def check_user_not_busy(force: bool = False) -> None:
    """Kullanici aktif olarak yazarken/tiklarken araya girme.

    Bunun eksikligi somut zarar veriyordu: kullanici mesajini yazarken
    calistirilan bir test odagi calip ctrl+a/delete gonderebiliyor, yani
    yazdigi metni silebiliyordu. Girdi kuyrugu paylasilan bir kaynak --
    ayni anda iki taraf birden kullanamaz.

    0.6.0'dan beri VARSAYILAN OLARAK KAPALI (config.REFUSE_WHEN_BUSY). Kullanici
    istedi: mesgulken reddetme, girdimi kes. Araya karisma sorununu artik
    girdi kilidi cozuyor (inputguard: basmalar yutulur, basili tuslar beklenir).
    Bu kural ayrica farkinda olmadan oturumlari siraya sokuyordu; o is artik
    ortak girdi kilidinde (inputguard, Local\\PcGozGirdiKilidi).
    """
    if not config.REFUSE_WHEN_BUSY or force or INPUT_HELD.is_set():
        return
    idle = human_idle_ms()
    if idle is not None and idle < config.USER_BUSY_MS:
        raise UserBusy(
            f"Kullanici su anda klavye/fareyi kullaniyor ({idle} ms once girdi vardi). "
            "Girdi gondermedim -- araya girsem yazdigi seye karisirdim. "
            "Birkac saniye bekleyip tekrar dene; gercekten simdi gerekiyorsa force=True ver."
        )


def freeze(reason: str) -> None:
    """Dondur (ac/kapa degil). Girdi kilidi kisayolu kendisi yakaladiginda."""
    if not _frozen.is_set():
        _frozen.set()
        audit("hotkey", detail="DONDURULDU", via=reason)


def _toggle_frozen() -> None:
    if _frozen.is_set():
        _frozen.clear()
        audit("hotkey", detail="cozuldu")
    else:
        _frozen.set()
        audit("hotkey", detail="DONDURULDU")


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 0xB07

#: Denenecek kisayollar, sirayla. Ilki baskasi tarafindan tutuluyorsa
#: (WinError 1409) digerine gecilir -- acil durdurmasiz calismak, fareyi
#: suren bir arac icin kabul edilebilir bir durum degil.
HOTKEY_CANDIDATES: tuple[tuple[str, int, int], ...] = (
    ("Ctrl+Alt+Shift+Esc", MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x1B),
    ("Ctrl+Alt+Shift+Pause", MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x13),
    ("Ctrl+Alt+Shift+F12", MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x7B),
    ("Ctrl+Shift+Alt+Backspace", MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x08),
)


WM_TIMER = 0x0113

#: Yedek kisayoldaysak ilk tercihi bu aralikla yeniden dene (ms). Birden fazla
#: sunucu calisirken Esc'i tutan kapaninca digerleri Pause/F12'de kalmasin --
#: kullanicinin bildigi kisayol hep Ctrl+Alt+Shift+Esc olsun.
HOTKEY_RETRY_MS = 5000


def _hotkey_loop() -> None:
    """Kendi mesaj dongusune sahip ayri iplik. RegisterHotKey cagiran iplige postalar."""
    errors = []
    candidates, retry_ms = HOTKEY_CANDIDATES, HOTKEY_RETRY_MS
    for index, (label, mods, vk) in enumerate(candidates):
        if user32.RegisterHotKey(None, HOTKEY_ID + index, mods | MOD_NOREPEAT, vk):
            _hotkey_state["registered"] = True
            _hotkey_state["combo"] = label
            if index:
                _hotkey_state["note"] = (
                    f"ilk tercih ({candidates[0][0]}) baskasi tarafindan tutuluyordu"
                )
            break
        errors.append(f"{label}: WinError {ctypes.get_last_error()}")
    else:
        _hotkey_state["error"] = "hicbir kisayol kaydedilemedi -- " + "; ".join(errors)
        return

    timer = user32.SetTimer(None, 0, retry_ms, None) if index else 0
    msg = wintypes.MSG()
    while True:
        ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if ret in (0, -1):
            break
        if msg.message == WM_HOTKEY and HOTKEY_ID <= msg.wParam < HOTKEY_ID + len(candidates):
            _toggle_frozen()
        elif msg.message == WM_TIMER and timer and msg.wParam == timer:
            label, mods, vk = candidates[0]
            if user32.RegisterHotKey(None, HOTKEY_ID, mods | MOD_NOREPEAT, vk):
                user32.UnregisterHotKey(None, HOTKEY_ID + index)     # yedegi baskasina birak
                user32.KillTimer(None, timer)
                timer = 0
                _hotkey_state["combo"] = label
                _hotkey_state["note"] = None
                audit("hotkey", detail=f"{label} geri alindi (onceki sahibi kapandi)")


def start_hotkey() -> None:
    if not config.HOTKEY_ENABLED:
        _hotkey_state["error"] = "PCGOZ_HOTKEY ile kapatilmis"
        return
    threading.Thread(target=_hotkey_loop, name="pcgoz-hotkey", daemon=True).start()
    # Kayit birkac ms suruyor; selftest'in dogru rapor vermesi icin kisa bekle.
    for _ in range(50):
        if _hotkey_state["registered"] or _hotkey_state["error"]:
            break
        time.sleep(0.01)


def hotkey_status() -> dict[str, Any]:
    return dict(_hotkey_state)


def hotkey_warning() -> str:
    """Acil durdurma calismyorsa dondurulecek uyari, calisiyorsa bos dizge.

    Girdi ureten her aracin sonucuna ekleniyor. Fareyi suren bir aracin
    durdurulamiyor olmasi sessizce gecilebilecek bir ayrinti degil -- her
    seferinde gorunmesi gerekiyor.
    """
    if _hotkey_state["registered"]:
        return ""
    reason = str(_hotkey_state.get("error") or "kaydedilemedi")
    return (
        f"\n\n[!] ACIL DURDURMA CALISMIYOR: {reason}. "
        "Kullanici girdiyi kisayolla durduramaz. Riskli bir sey yapmadan once haber ver."
    )


# --------------------------------------------------------------------------
# Denetim gunlugu
# --------------------------------------------------------------------------

def _rotate_if_needed() -> None:
    try:
        if config.LOG_PATH.exists() and config.LOG_PATH.stat().st_size > config.LOG_MAX_BYTES:
            backup = config.LOG_PATH.with_suffix(".1.jsonl")
            backup.unlink(missing_ok=True)
            config.LOG_PATH.rename(backup)
    except OSError:
        pass


#: Su anki arac cagrisinin kimligi. Eylemin ONCEDEN yazilan kaydi ile
#: sonradan yazilan sonuc kaydi bununla eslesiyor. contextvar: worker.call
#: baglami UIA ipligine tasiyor, eszamanli cagrilar birbirine karismiyor.
CALL_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar("pcgoz_call", default=None)
_call_ids = itertools.count(1)


def next_call_id() -> int:
    return next(_call_ids)


def audit(tool: str, **fields: Any) -> None:
    """Her eylemi JSONL olarak diske yaz. Loglama hicbir zaman aracin isini bozmaz."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": tool,
        "pid": os.getpid(),
        "call": CALL_ID.get(),
        **{k: v for k, v in fields.items() if v is not None},
    }
    if record["call"] is None:
        del record["call"]
    if config.DRY_RUN:
        record["dry_run"] = True
    try:
        with _log_lock:
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed()
            with config.LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


#: Eski yerden gelen JSONL gunlukleri: hedefte varsa sonuna eklenir, yoksa tasinir.
_APPENDABLE = frozenset({"actions.jsonl", "actions.1.jsonl"})


def migrate_legacy_logs() -> list[str]:
    """Eski yerlerdeki gunlukleri (bkz. config.legacy_log_dirs) yeni yere al.

    Doner: yapilan her tasima icin bir satir. Gunluk yeri degistirilmisse
    (PCGOZ_LOG_DIR, testler) hicbir sey yapmaz -- yoksa bir test kullanicinin
    gercek gunlugunu kendi gecici klasorune tasiyip silerdi.
    """
    import shutil

    if config.LOG_DIR != config.DEFAULT_LOG_DIR:
        return []
    done: list[str] = []
    for old in config.legacy_log_dirs():
        try:
            files = sorted(p for p in old.iterdir() if p.is_file())
        except OSError:
            continue
        for src in files:
            dst = config.LOG_DIR / src.name
            try:
                config.LOG_DIR.mkdir(parents=True, exist_ok=True)
                if dst.exists() and src.name not in _APPENDABLE:
                    continue
                with _log_lock:
                    if dst.exists():
                        # Once yeniden adlandir: eski surumle calisan bir sunucu o
                        # sirada yaziyorsa yeni bir dosya acar, satir kaybolmaz.
                        moving = src.with_name(src.name + ".tasiniyor")
                        src.rename(moving)
                        with moving.open("rb") as a, dst.open("ab") as b:
                            shutil.copyfileobj(a, b)
                        moving.unlink()
                    else:
                        shutil.move(str(src), str(dst))
                done.append(f"{src} -> {dst}")
            except OSError:
                continue
        try:
            old.rmdir()                     # sadece bossa
        except OSError:
            pass
    return done


def audit_outcome(tool: str, started: float, error: BaseException | None, **fields: Any) -> None:
    """Cagrinin sonucu ve suresi. Eskiden sadece "ne yapildi" yaziliyordu:
    hangi eylemin reddedildigi (kullanici mesgul, bayat ref, donduruldu) ya da
    ne kadar surdugu gorunmuyordu, yani hicbir iyilestirme olculemiyordu.
    Okuma araclari da yaziliyor -- argumanlari degil, sadece sonuc ve sure."""
    ms = round((time.perf_counter() - started) * 1000)
    if error is None:
        audit(tool, event="sonuc", ok=True, ms=ms, **fields)
    else:
        audit(tool, event="sonuc", ok=False, error=type(error).__name__,
              message=str(error)[:200], ms=ms, **fields)


# --------------------------------------------------------------------------
# Tek sert blok: kimlik bilgisi alanlari
# --------------------------------------------------------------------------

def guard_text_target(*, is_password: bool, process_name: str | None, label: str) -> None:
    """Parola alanina / kimlik bilgisi arayuzune yazmayi reddet.

    Bu bir onay kapisi degil, kapali bir yol. Kullanici "onay sorma" dedi ama
    kimlik bilgisi girmek modelin yapmasi yasak olan bir sey -- karari model
    muhakemesine birakmak yerine araca gomuyoruz.
    """
    if is_password:
        raise Refused(
            f"{label} bir parola alani -- buraya yazmam yasak. "
            "Parolayi kullanicinin kendisi girmeli."
        )
    proc = (process_name or "").lower()
    if proc in config.CREDENTIAL_PROCESSES:
        raise Refused(
            f"{label} bir Windows kimlik bilgisi arayuzu ({process_name}) -- "
            "buraya girdi gondermem yasak. Kullanicinin kendisi yapmali."
        )


#: Parola alanina gonderilebilecek tuslar: gezinme, onay/iptal ve silme.
#: tab, enter, esc, backspace, delete, pgup/pgdn, end/home, oklar.
_SAFE_KEY_VKS = frozenset({0x09, 0x0D, 0x1B, 0x08, 0x2E,
                           0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28})
_VK_SHIFT, _VK_CTRL, _VK_A = 0x10, 0x11, 0x41


def _is_safe_key(mods: list[int], vk: int) -> bool:
    held = set(mods)
    if vk in _SAFE_KEY_VKS and held <= {_VK_SHIFT, _VK_CTRL}:
        return True                        # shift+tab, shift+home, ctrl+backspace ...
    return vk == _VK_A and held == {_VK_CTRL}      # ctrl+a


def guard_key_target(mods: list[int], vk: int, *, is_password: bool,
                     process_name: str | None, label: str) -> None:
    """Parola alanina tus gondermeyi izin listesiyle sinirla.

    Yazma engeli tek basina delikti: pc_clipboard set + key 'ctrl+v' ya da
    key 'a', key 'shift+1' ... ile parola tus tus girilebiliyordu. Yasak
    listesi (ctrl+v, shift+insert, win+v ...) hep bir yolu kacirir; o yuzden
    tersine gidiliyor -- sadece karakter uretmeyen ve yapistirmayan tuslar.
    """
    proc = (process_name or "").lower()
    if not (is_password or proc in config.CREDENTIAL_PROCESSES):
        return
    if _is_safe_key(mods, vk):
        return
    where = "bir parola alani" if is_password else f"bir Windows kimlik bilgisi arayuzu ({process_name})"
    raise Refused(
        f"{label} {where} -- buraya sadece gezinme/silme tuslari gonderebilirim "
        "(tab, enter, esc, oklar, home/end, backspace, delete, ctrl+a). "
        "Karakter ya da yapistirma yasak; parolayi kullanicinin kendisi girmeli."
    )
