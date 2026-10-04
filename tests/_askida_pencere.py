"""Askiya alinabilen test penceresi -- AYRI bir surec olarak calistirilir.

    python tests/_askida_pencere.py [saniye]

stdout'a pencerenin hwnd'sini yazar. Pencereye WM_APP gonderilince pencere
ipligi o kadar saniye uyur, yani mesaj islemez: Windows'un "yanit vermiyor"
dedigi durum. Donmus bir uygulamada UIA cagrilarinin ne kadar bekledigini
olcmek icin. Ayri surec sart: ayni suretteki pencereye UIA cagrisi baska yol
izler.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _pencere import WM_APP, TestWindow  # noqa: E402

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0


def handler(hwnd, msg, wparam, lparam):  # noqa: ANN001, ANN201
    if msg == WM_APP:
        time.sleep(SECONDS)          # mesaj dongusu durur: pencere "askida"
        return 0
    return None


window = TestWindow("PcGoz askida test", handler=handler, noactivate=True,
                    rect=(40, 40, 360, 160), edits=(("askida", 0),), buttons=("Dugme",))
print(window.hwnd, flush=True)
window._thread.join()
