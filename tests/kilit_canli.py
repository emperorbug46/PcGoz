"""Girdi kilidi canli denemesi -- KULLANICI ile. Elle: uv run python tests/kilit_canli.py

Kilidin kullanicinin fiziksel fare/klavyesini yuttugunu SendInput ile olcmek
mumkun degil: programla gonderilen her olay "injected" sayilir ve kilitten
gecer. O yuzden bu deneme insana ihtiyac duyuyor: 3 sn sonra 8 sn kilit;
bu surede fareyi oynat, birkac tusa bas. Hicbir sey olmamali, ve ekranin sol
ustunde yari saydam "PcGoz calisiyor -- fare ve klavye kilitli" uyarisi
gorunmeli (0.6.0). Kilit basladiginda bir tusu (or. Shift) basili tutarsan
uyari "Basili tusu birakin" der; birakinca kilit devam eder.

Ctrl+Alt+Shift+Esc kilidi her an birakir; 8 sn sonra kendiliginden kalkar.
Hicbir pencereye girdi gondermez.
"""

from __future__ import annotations

import ctypes

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import time  # noqa: E402

import _gunluk  # noqa: E402,F401 -- gercek denetim gunlugunu kirletme

from pcgoz import inputguard, overlay, safety  # noqa: E402
from pcgoz import input as pin  # noqa: E402


def main() -> int:
    print("5 sn sonra fare ve klavye 8 sn kilitlenecek; sol ustte uyari cikacak.")
    print("Kilit sirasinda fareyi oynat ve birkac tusa bas; Ctrl+Alt+Shift+Esc birakir.")
    for left in range(5, 0, -1):
        print(f"  {left}...", flush=True)
        time.sleep(1)
    print("KILITLI -- simdi fareyi oynat, tuslara bas", flush=True)
    home = pin.cursor_pos()
    positions = set()
    overlay_seen = False
    started = time.monotonic()
    with inputguard.hold(8):
        while inputguard.guard.active() and not safety.is_frozen():
            positions.add(pin.cursor_pos())
            overlay_seen = overlay_seen or overlay.overlay.visible()
            time.sleep(0.05)
    held = time.monotonic() - started
    moved = len(positions) > 1 or home not in positions
    swallowed = inputguard.guard.swallowed
    print("=" * 58)
    print(f"kilit suresi            : {held:.1f} sn")
    print(f"yutulan fiziksel olay   : {swallowed}")
    print(f"imlec kilitte oynadi mi : {'EVET -- kilit fareyi tutmadi' if moved else 'hayir'}")
    print(f"acil durdurma basildi   : {'evet, kilit hemen kalkti' if safety.is_frozen() else 'hayir'}")
    print(f"kanca kaldirildi        : {not inputguard.guard._hooks}")
    print(f"basili birakilan tuslar : {inputguard.guard.released_held or 'yok'}")
    print(f"sol ust uyari gorundu   : {'evet' if overlay_seen else 'HAYIR'}")
    if swallowed == 0:
        print("Kilit sirasinda fareye/klavyeye dokunulmadi -- yutma olculemedi, tekrar dene.")
        return 1
    return 1 if moved else 0


if __name__ == "__main__":
    raise SystemExit(main())
