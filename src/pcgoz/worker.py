"""Tum UIA ve girdi islerini yuruten tek iplik.

Neden tek iplik: MCP sunucusu senkron araclari bir iplik havuzunda calistirir,
yani her cagri farkli bir iplige dusebilir. COM nesnelerini iplikler arasinda
gezdirmek yerine hepsini kendi apartmanina sahip tek bir iplige gonderiyoruz.
Yan fayda: ref kayit defteri ve girdi sirasi dogal olarak seri hale geliyor,
kilit gerekmiyor.

Bedeli: tek bir takilan cagri butun araclari kilitler. UIA ve PrintWindow
artik donmus pencerede takilmiyor (zaman asimi + yoklama, bkz. uia.iuia ve
windows.responds); buradaki sure siniri son guvenlik agi.
"""

from __future__ import annotations

import contextvars
import ctypes
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")

COINIT_APARTMENTTHREADED = 0x2

#: Tek bir cagri icin azami bekleme.
CALL_TIMEOUT_S = 90.0


@dataclass
class _Job:
    fn: Callable[[], Any]
    reply: queue.Queue[tuple[bool, Any]] = field(default_factory=lambda: queue.Queue(maxsize=1))
    started: threading.Event = field(default_factory=threading.Event)
    #: Zaman asimina ugrayip cagirana hata donen is ARTIK calistirilmaz.
    #: Yoksa takilma cozulunce sonradan calisir: model "basarisiz" sandigi
    #: bir tiklamanin saniyeler sonra gerceklestigini bilemez.
    cancelled: threading.Event = field(default_factory=threading.Event)


_jobs: queue.Queue[_Job] = queue.Queue()
_thread: threading.Thread | None = None
_lock = threading.Lock()
_busy_since: float | None = None


def _loop() -> None:
    global _busy_since
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    while True:
        job = _jobs.get()
        with _lock:
            if job.cancelled.is_set():
                continue
            job.started.set()
        _busy_since = time.monotonic()
        try:
            job.reply.put((True, job.fn()))
        except BaseException as exc:  # noqa: BLE001 -- hata cagirana aynen tasinir
            exc.__pcgoz_tb__ = traceback.format_exc()  # type: ignore[attr-defined]
            job.reply.put((False, exc))
        finally:
            _busy_since = None


def ensure_started() -> None:
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="pcgoz-uia", daemon=True)
            _thread.start()


def _stuck(seconds: float, *, ran: bool) -> TimeoutError:
    fate = ("Bu istek BASLADI ama bitmedi -- etkisi belirsiz, ekrana bakmadan tekrarlama."
            if ran else "Bu istek calistirilmadi ve sonradan da calistirilmayacak.")
    return TimeoutError(
        f"PcGoz'un UIA/girdi is parcacigi {seconds:.0f} sn'dir tek bir cagride takili "
        f"(muhtemelen donmus bir uygulama). {fate} Birkac saniye sonra tekrar dene; "
        "surerse Claude Code'u yeniden baslat."
    )


def call(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """`fn`'i UIA ipliginde calistir, sonucu (veya istisnayi) burada dondur.

    Zaten UIA ipligindeysek is kuyruga atilmaz, dogrudan burada calisir.
    Bu kontrol olmazsa ic ice cagrilar kilitleniyor: worker kendi kuyruguna
    is birakip cevabini bekler, ama kuyrugu isleyen tek iplik de kendisidir --
    yani sonsuza kadar durur. Bir aracin baska bir araci cagirmasi (pc_text
    icinde pc_read gibi) bunu tetiklemeye yetiyor.
    """
    if threading.current_thread() is _thread:
        return fn(*args, **kwargs)
    ensure_started()
    limit = CALL_TIMEOUT_S
    busy = _busy_since
    if busy is not None and time.monotonic() - busy > limit:
        raise _stuck(time.monotonic() - busy, ran=False)   # kuyruga bile girme

    # Cagiranin baglami (safety.CALL_ID) UIA ipliginde de gecerli olsun.
    context = contextvars.copy_context()
    job = _Job(lambda: context.run(fn, *args, **kwargs))
    _jobs.put(job)
    try:
        ok, value = job.reply.get(timeout=limit)
    except queue.Empty:
        with _lock:
            ran = job.started.is_set()
            job.cancelled.set()
        raise _stuck(limit, ran=ran) from None
    if ok:
        return value  # type: ignore[no-any-return]
    raise value
