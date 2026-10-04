"""Testler icin gecici profilli Brave -- kullanicinin tarayicisina dokunmadan.

Kullanicinin Brave'inde pencere acmak gecmisine ve oturumuna karisir. Burada
her acilis ayri, gecici bir profil (--user-data-dir): gercekten soguk acilis,
sonunda yalnizca o profilin surecleri beklenir ve klasor silinir.

    with GeciciBrave(sayfa.as_uri(), "soguk acilis") as brave:
        brave.hwnd       # 0 ise pencere gelmedi
    brave.closed, brave.profile_removed
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from pcgoz import windows, worker

BRAVE = Path(r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe")


def start_unelevated(argv: list[str]) -> Path | None:
    """Yonetici hakkiyla calisiyorsak programi normal haklarla baslat.

    Olculdu (2026-09-24): yukseltilmis surecten baslatilan Brave hemen
    0xC0000005 ile cokuyor. Explorer'in actigi program Explorer'in (normal)
    jetonunu alir; arguman gecirmek icin kucuk bir .cmd uzerinden. Doner:
    silinecek betik (varsa).
    """
    if not ctypes.windll.shell32.IsUserAnAdmin():
        subprocess.Popen(argv)
        return None
    fd, name = tempfile.mkstemp(prefix="pcgoz-ac-", suffix=".cmd")
    with open(fd, "w", encoding="mbcs") as script:
        script.write("@start \"\" " + subprocess.list2cmdline(argv) + "\r\n")
    subprocess.Popen(["explorer.exe", name])
    return Path(name)


def drop_profile(profile: Path) -> bool:
    """Gecici profilin sureclerini bekle (gerekirse durdur), klasoru sil.

    Durdurulan surecler yalnizca komut satirinda BU profil klasoru olanlar --
    kullanicinin kendi Brave'ine dokunulmaz. Doner: klasor silindi mi.
    """
    query = ("Get-CimInstance Win32_Process -Filter \"Name='brave.exe'\" | Where-Object "
             f"{{ $_.CommandLine -like '*{profile.name}*' }} | ForEach-Object ProcessId")
    for attempt in range(12):
        out = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                             capture_output=True, text=True, check=False).stdout.split()
        if not out:
            break
        if attempt == 10:
            for pid in out:
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, check=False)
        time.sleep(0.5)
    shutil.rmtree(profile, ignore_errors=True)
    return not profile.exists()


class GeciciBrave:
    """Gecici profilli Brave penceresi. Cikista pencere WM_CLOSE, profil silinir."""

    def __init__(self, url: str, title_part: str, *, timeout: float = 15.0) -> None:
        self.url = url
        self.title_part = title_part.lower()
        self.timeout = timeout
        self.hwnd = 0
        self.closed = False
        self.profile_removed = False
        self._profile: Path | None = None
        self._script: Path | None = None

    def __enter__(self) -> "GeciciBrave":
        self._profile = Path(tempfile.mkdtemp(prefix="pcgoz-brave-"))
        self._script = start_unelevated([
            str(BRAVE), f"--user-data-dir={self._profile}", "--no-first-run",
            "--no-default-browser-check", "--new-window", self.url])
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline and not self.hwnd:
            time.sleep(0.25)
            for w in worker.call(windows.list_windows):
                if w.process == "brave.exe" and self.title_part in w.title.lower():
                    self.hwnd = w.hwnd
                    break
        return self

    def __exit__(self, *exc: object) -> None:
        # Sureci OLDURME: Chromium'da window_pid ana tarayici surecidir.
        if self.hwnd:
            self.closed = worker.call(windows.close_window, self.hwnd)
        if self._profile is not None:
            self.profile_removed = drop_profile(self._profile)
        if self._script is not None:
            self._script.unlink(missing_ok=True)
