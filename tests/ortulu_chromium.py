"""Ortulu Chromium canli denemesi. Elle: uv run python tests/ortulu_chromium.py

Gecici profilli Brave'de sayac sayfasi (_sayac); ustune bu surecin tk ortuleri gelir.
1) occlusion.chromium_hidden yedi duzende Chromium'un kendi karariyla (baslik
   v=h/v) uyusuyor mu: tam ortu, 30 px serit, iki pencere, arac penceresi,
   yari saydam, buyutulmus, hep ustte ortu (ayri neden: ust_hep_ustte).
   27.10.2026: ilk alti 6/6.
2) Ortuluyken pc_screenshot / pc_text uyari veriyor mu; Brave zaten on plandayken
   pc_focus onu uyandiriyor mu (once >=2,5 sn bayat kaliyordu).

Ekranda ~45 sn pencere acilip kapanir; pc_focus adiminda girdi bir an kilitlenir.
Tus/tiklama gondermez.
"""

from __future__ import annotations

import ctypes

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import time  # noqa: E402
import tkinter as tk  # noqa: E402

import _gunluk  # noqa: E402,F401 -- gercek denetim gunlugunu kirletme
from _sayac import SAYFA, baslik, pw_sayac  # noqa: E402
from _tarayici import GeciciBrave  # noqa: E402

from pcgoz import occlusion, server, windows  # noqa: E402

user32 = ctypes.windll.user32
vp = ctypes.c_void_p
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
NOACTIVATE, NOMOVE, NOSIZE = 0x10, 0x2, 0x1
X, Y, W, H = 200, 150, 760, 560


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = RECT()
    user32.GetWindowRect(vp(hwnd), ctypes.byref(r))
    return r.l, r.t, r.r, r.b


def warnings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("[!]")]


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    covers = []
    for i in range(2):
        cover = tk.Toplevel(root)
        cover.title(f"PcGozD ortu {i}")
        cover.configure(bg="#808080")
        cover.withdraw()
        covers.append(cover)

    def pump(sec: float) -> None:
        end = time.perf_counter() + sec
        while time.perf_counter() < end:
            root.update()
            time.sleep(0.01)

    def place(cover: tk.Toplevel, rect: tuple[int, int, int, int]) -> None:
        # Etkinlesmeden en uste: hep ustte yap, sonra birak (HWND_NOTOPMOST normallerin en ustu).
        cover.deiconify()
        root.update()
        hwnd = int(cover.wm_frame(), 16)
        left, top, right, bottom = rect
        user32.SetWindowPos(vp(hwnd), vp(HWND_TOPMOST), left, top, right - left, bottom - top, NOACTIVATE)
        user32.SetWindowPos(vp(hwnd), vp(HWND_NOTOPMOST), 0, 0, 0, 0, NOACTIVATE | NOMOVE | NOSIZE)

    def reset() -> None:
        for cover in covers:
            cover.state("normal")           # once: withdraw'dan sonra cagrilirsa pencereyi yeniden gosterir
            cover.attributes("-toolwindow", False)
            cover.attributes("-alpha", 1.0)
            cover.attributes("-topmost", False)
            cover.withdraw()
        root.update()
        occlusion.wake()
        pump(1.0)

    bad = 0
    with GeciciBrave(SAYFA.as_uri(), "pcgozd") as brave:
        hb = brave.hwnd
        if not hb:
            raise SystemExit("Brave penceresi gelmedi")
        windows.move_frame(hb, X, Y, W, H)
        pump(2.0)
        bl, bt, br, bb = window_rect(hb)
        mid = (bl + br) // 2
        full = (bl - 40, bt - 40, br + 40, bb + 40)

        def tool() -> None:
            covers[0].attributes("-toolwindow", True)
            place(covers[0], full)

        def alpha() -> None:
            covers[0].attributes("-alpha", 0.95)
            place(covers[0], full)

        def zoomed() -> None:
            place(covers[0], full)
            covers[0].state("zoomed")

        def pinned() -> None:
            place(covers[0], full)
            covers[0].attributes("-topmost", True)

        layouts = [
            ("tam ortu", lambda: place(covers[0], full)),
            ("30 px serit acik", lambda: place(covers[0], (bl - 40, bt - 40, br - 30, bb + 40))),
            ("iki pencere", lambda: (place(covers[0], (bl - 40, bt - 40, mid + 5, bb + 40)),
                                     place(covers[1], (mid - 5, bt - 40, br + 40, bb + 40)))),
            ("arac penceresi", tool),
            ("yari saydam", alpha),
            ("buyutulmus", zoomed),
            ("hep ustte ortu", pinned),
        ]
        def raise_brave() -> bool:
            """Brave'i ortusuz en uste al. Kullanici etkinken odak kilidi reddeder: o zaman
            etkinlestirmeden z-sirasiyla (hep ustte yap, birak). Doner: on planda mi."""
            windows.focus(hb)
            if windows.foreground_window() != hb:
                for where in (HWND_TOPMOST, HWND_NOTOPMOST):
                    user32.SetWindowPos(vp(hb), vp(where), 0, 0, 0, 0, NOACTIVATE | NOMOVE | NOSIZE)
            occlusion.wake()
            pump(0.6)
            return windows.foreground_window() == hb

        print("duzen               | bizim hesap   | Chromium (baslik) | uyusma")
        for name, build in layouts:
            raise_brave()
            if occlusion.chromium_hidden(hb) is not None:
                # Baska bir pencere (kullanicinin) hala ortuyor: duzen olculemez.
                print(f"{name:19} | GECERSIZ: Brave ortusuz one alinamadi")
                continue
            build()
            root.update()
            occlusion.wake()      # arac penceresi / saydamlik degisimi Chromium'a olay uretmeyebilir
            pump(1.5)
            why = occlusion.chromium_hidden(hb)
            ours = why is not None
            hidden = baslik(hb)[1] == "h"
            # Hep ustteki ortu ayri nedenle bildirilmeli: pc_focus onu acmaz.
            bad += ours != hidden or (name == "hep ustte ortu") != (why == "ust_hep_ustte")
            print(f"{name:19} | {why or 'gorunur':13} | {'gizli' if hidden else 'gorunur':17} | "
                  f"{'EVET' if ours == hidden else 'HAYIR'}")
            reset()

        front = raise_brave()      # Brave on planda, ustu etkinlesmeyen ortuyle kapali
        place(covers[0], full)
        root.update()
        pump(2.5)
        shot = server._audited("pc_screenshot", lambda: server.pc_screenshot(hwnd=hb, annotate=False),
                               {"hwnd": hb})
        text = server._audited("pc_text", lambda: server.pc_text(hwnd=hb), {"hwnd": hb})
        print(f"\nortulu: baslik={baslik(hb)} PrintWindow={pw_sayac(hb)} on plan Brave={windows.foreground_window() == hb}")
        print("  pc_screenshot:", warnings(shot[0]) or "UYARI YOK")
        print("  pc_text      :", warnings(text) or "UYARI YOK")
        bad += not warnings(shot[0]) or not warnings(text)
        if not front:
            # Senaryo "zaten ondeki ortulu pencere"; on planda degilse pc_focus odak kilidinde
            # baslik cubuguna GERCEK tiklama yapar -- deneme bunu yapmasin.
            print("  pc_focus     : ATLANDI (odak kilidi: kullanici etkin, Brave on plana alinamadi)")
            reset()
            root.destroy()
            print(f"\nkapandi={brave.closed} profil silindi={brave.profile_removed}; sorun={bad} (eksik)")
            return 1
        out = server._audited("pc_focus", lambda: server.pc_focus(hwnd=hb), {"hwnd": hb})
        drawn, (count, _v) = pw_sayac(hb), baslik(hb)
        fresh = isinstance(drawn, int) and count is not None and drawn >= count - 1
        bad += not fresh
        print(f"  pc_focus     : {out.splitlines()[0][:80]}")
        print(f"  hemen sonra  : PrintWindow={drawn} baslik={count} -> {'taze' if fresh else 'BAYAT'}")
        text = server._audited("pc_text", lambda: server.pc_text(hwnd=hb), {"hwnd": hb})
        print("  pc_text      :", warnings(text) or "uyari yok (dogru)")
        bad += bool(warnings(text))
        reset()
    root.destroy()
    print(f"\nkapandi={brave.closed} profil silindi={brave.profile_removed}; sorun={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
