"""Ekran kapali canli denemesi -- EKRANI KAPATIR. Elle: uv run python tests/ekran_kapali.py

Yalniz kullaniciya haber verilip o bilgisayar basinda degilken. Olcer:
1) occlusion.display_state() kapanista 0'a, acilista 1'e donuyor mu (penceresiz
   geri cagri kaydi gercek gecisi de goruyor mu).
2) Ekran kapaliyken Chromium gizli mi; PrintWindow bayat mi; pc_text uyari veriyor mu.
3) Chromium olmayan pencere (tk, 200 ms'de bir renk degistirir) icin PrintWindow ve
   ekran kirpmasi bayat mi -- goruntu uyarisi Chromium disinda gerekli mi.
Ekrani acmak icin fareyi 1 px oynatip geri alir (SendInput).
"""

from __future__ import annotations

import ctypes

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import time  # noqa: E402
import tkinter as tk  # noqa: E402

import _gunluk  # noqa: E402,F401 -- gercek denetim gunlugunu kirletme
from _sayac import SAYFA, baslik, coz, ekran_sayac, ortalama, pw_sayac  # noqa: E402
from _tarayici import GeciciBrave  # noqa: E402

from pcgoz import capture, occlusion, server, windows  # noqa: E402
from pcgoz.input import cursor_pos, move  # noqa: E402

user32 = ctypes.windll.user32
WM_SYSCOMMAND, SC_MONITORPOWER, HWND_BROADCAST = 0x0112, 0xF170, 0xFFFF


def wake_display() -> None:
    # Kendi INPUT yapisi 48 bayttaydi, SendInput 40 ister: sessizce basarisizdi (inceleme).
    x, y = cursor_pos()
    move(x + 1, y, steps=1)
    move(x, y, steps=1)


def main() -> int:
    root = tk.Tk()
    root.title("PcGozE sayac")
    root.geometry("300x200+1000+150")
    count = {"n": 0}

    def tick() -> None:
        count["n"] += 1
        n = count["n"]
        root.configure(bg=f"#{n % 16 * 16 + 8:02x}{(n >> 4) % 16 * 16 + 8:02x}c8")
        root.after(200, tick)

    tick()

    def pump(sec: float) -> None:
        end = time.perf_counter() + sec
        while time.perf_counter() < end:
            root.update()
            time.sleep(0.01)

    hk = int(root.wm_frame(), 16)

    def tk_frames() -> str:
        img, _origin, how = capture.capture_window(hk)
        cx, cy = img.width // 2, img.height // 2 + 20
        drawn = coz(ortalama(img.crop((cx - 4, cy - 4, cx + 5, cy + 5))))
        return f"tk sayac={count['n'] % 256} PrintWindow={drawn} ({how}) ekran={ekran_sayac(hk)}"

    def wait_state(want: int, limit: float) -> float | None:
        start = time.perf_counter()
        while time.perf_counter() - start < limit:
            pump(0.1)
            if occlusion.display_state() == want:
                return (time.perf_counter() - start) * 1000
        return None

    with GeciciBrave(SAYFA.as_uri(), "pcgozd") as brave:
        hb = brave.hwnd
        if not hb:
            raise SystemExit("Brave penceresi gelmedi")
        windows.move_frame(hb, 200, 150, 760, 560)
        pump(2.0)
        print(f"once: ekran={occlusion.display_state()} brave={baslik(hb)} pw={pw_sayac(hb)}; {tk_frames()}")
        user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
        off = wait_state(0, 10)
        print(f"kapanis bildirimi: {'%.0f ms' % off if off is not None else 'GELMEDI (10 sn)'}")
        pump(4.0)
        text = server._audited("pc_text", lambda: server.pc_text(hwnd=hb), {"hwnd": hb})
        note = [line for line in text.splitlines() if line.startswith("[!]")]
        print(f"kapaliyken: brave={baslik(hb)} pw={pw_sayac(hb)}; {tk_frames()}")
        print("  pc_text uyarisi:", note or "YOK")
        wake_display()
        on = wait_state(1, 10)
        pump(1.5)
        print(f"acilis bildirimi: {'%.0f ms' % on if on is not None else 'GELMEDI'}; "
              f"sonra brave={baslik(hb)} pw={pw_sayac(hb)}; {tk_frames()}")
    root.destroy()
    print(f"kapandi={brave.closed} profil silindi={brave.profile_removed}")
    return 0 if off is not None and on is not None and note else 1


if __name__ == "__main__":
    raise SystemExit(main())
