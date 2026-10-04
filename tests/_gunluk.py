"""Testleri gercek ortamdan yalitir -- import edilmesi yeter.

1. Denetim gunlugu: testler arac fonksiyonlarini dogrudan cagiriyor; eylem
   kayitlari eskiden kullanicinin %LOCALAPPDATA%\\PcGoz\\actions.jsonl dosyasina
   gidiyordu (2026-09-23: bir gunde 71 test kaydi). O gunluk gercek kullanimi
   olcmek icin (--stats).
2. Acil dondurma: durum butun PcGoz surecleri arasinda ortak (safety.Freeze).
   Testler dondurup cozuyor; ortak nesneyi kullansalar calisan sunucuyu da
   dondururlar, kullanici bilerek dondurduysa da cozerlerdi. Her test sureci
   kendine ozel bir nesneye yazar; okurken ortak nesneye de bakar -- kullanici
   acil durdurmaya basinca calisan test de durur.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

from pcgoz import config, safety

config.LOG_DIR = Path(tempfile.mkdtemp(prefix="pcgoz-test-"))
config.LOG_PATH = config.LOG_DIR / "actions.jsonl"
# Her calistirma bir klasor aciyordu, hic silinmiyordu (2026-09-24: 44 tane).
atexit.register(shutil.rmtree, config.LOG_DIR, ignore_errors=True)

class _TestFreeze(safety.Freeze):
    def __init__(self) -> None:
        super().__init__(f"Local\\PcGozTest{os.getpid()}")
        self.live = safety.Freeze()

    def is_set(self) -> bool:
        return super().is_set() or self.live.is_set()


safety._frozen = _TestFreeze()
