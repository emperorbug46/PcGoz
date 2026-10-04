"""Agir web sayfasinda okuma araclarinin maliyeti ve dogrulugu.

Elle calistirilir: uv run python tests/agir_sayfa.py [satir]

Gecici profilli ayri bir Brave'de N satirlik (varsayilan 1500; satir basina
baglanti + dugme + metin, ~9000 UIA ogesi) yerel bir sayfa acar. Girdi
gondermez: kaydirma UIA ScrollItem deseniyle, kapatma WM_CLOSE ile.

Olculenler: soguk pc_find (ilk dokunus, erisilebilirlik uyandirma dahil),
ilik pc_find, pc_read, pc_window close ("ne degisti" dahil). Dogruluk: sayfa
sona kaydirilinca son satir bulunuyor mu -- MAX_ELEMENTS (4000) belge sirasiyla
kesiyordu, gorunen oge ilk 4000'in disinda kalabiliyordu.
"""

from __future__ import annotations

import ctypes

ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

import shutil  # noqa: E402
import statistics  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

from pcgoz import config, server, uia, worker  # noqa: E402
import _gunluk  # noqa: E402,F401 -- gercek denetim gunlugunu kirletme
import _tarayici  # noqa: E402

ROWS = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
LAST = f"Dugme {ROWS - 1}"

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    print(f"  [{'OK  ' if ok else 'HATA'}] {name}" + (f" -- {detail}" if detail else ""))


def timed(fn, n: int = 1) -> tuple[object, float]:  # noqa: ANN001
    """Doner: (son sonuc, medyan ms)."""
    samples, result = [], None
    for _ in range(n):
        started = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - started) * 1000)
    return result, statistics.median(samples)


def matches(text: str) -> int:
    """pc_find cikti basligindaki eslesme sayisi: "... 'x': 3 eslesme"."""
    head = text.split("\n", 1)[0]
    try:
        return int(head.rsplit(":", 1)[1].split()[0])
    except (IndexError, ValueError):
        return -1


def scroll_to(hwnd: int, name: str) -> bool:
    """Ogeyi UIA ile bul ve gorunur alana kaydir -- bizim agac goruntumuzden bagimsiz."""
    def run() -> bool:
        auto = uia.iuia()
        root = uia.element_from_handle(hwnd)
        cond = auto.CreatePropertyCondition(uia.UIA.UIA_NamePropertyId, name)
        el = root.FindFirst(uia.UIA.TreeScope_Descendants, cond)
        if not el:
            return False
        pattern = el.GetCurrentPattern(uia.UIA.UIA_ScrollItemPatternId)
        pattern.QueryInterface(uia.UIA.IUIAutomationScrollItemPattern).ScrollIntoView()
        return True
    return worker.call(run)


def main() -> int:
    if not _tarayici.BRAVE.exists():
        print("Brave kurulu degil.")
        return 1
    folder = Path(tempfile.mkdtemp(prefix="pcgoz-test-agir-"))
    page = folder / "agir.html"
    rows = "\n".join(
        f'<tr><td><a href="#r{i}">Baglanti {i}</a></td><td><button>Dugme {i}</button></td>'
        f"<td>Satir {i} aciklama metni</td></tr>" for i in range(ROWS))
    page.write_text("<!doctype html><meta charset=utf-8><title>Agir Sayfa</title>"
                    f"<button>Tikla</button><table>{rows}</table>", encoding="utf-8")

    print(f"Agir sayfa: {ROWS} satir, ekran suzgeci "
          f"{'ACIK' if getattr(config, 'ONSCREEN_ONLY', False) else 'yok/kapali'}")
    print("=" * 58)
    results: dict[str, float] = {}
    try:
        with _tarayici.GeciciBrave(page.as_uri(), "agir sayfa") as brave:
            hwnd = brave.hwnd
            check("pencere acildi", bool(hwnd))
            if not hwnd:
                return 1

            out, results["pc_find soguk"] = timed(lambda: server.pc_find(query="Dugme 1", hwnd=hwnd))
            check("soguk pc_find sayfayi gordu", matches(out) > 0, out.split("\n", 1)[0])
            out, results["pc_find ilik"] = timed(lambda: server.pc_find(query="Dugme 1", hwnd=hwnd), 3)
            out, results["pc_read"] = timed(lambda: server.pc_read(hwnd=hwnd), 3)
            check("pc_read ustteki dugmeyi gosterdi", '"Dugme 0"' in out)

            check("son satira kaydirildi (UIA ScrollIntoView)", scroll_to(hwnd, LAST))
            time.sleep(0.5)
            out, results["pc_find (sona kaydirilmis)"] = timed(
                lambda: server.pc_find(query=LAST, hwnd=hwnd))
            check(f"sona kaydirilmis sayfada {LAST!r} bulundu", matches(out) > 0,
                  out.split("\n", 1)[0])
            out = server.pc_read(hwnd=hwnd)
            check("sona kaydirilmis sayfada pc_read gorunen satirlari gosterdi",
                  f'"{LAST}"' in out, f"{len(out)} karakter")

            out, results["pc_window close"] = timed(
                lambda: server.pc_window(action="close", hwnd=hwnd, force=True))
            check("pencere kapandi", "kapandi" in out)
        check("gecici profil silindi", brave.profile_removed)
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    print("-" * 58)
    for name, ms in results.items():
        print(f"  {name:28s} {ms:7.0f} ms")
    print("=" * 58)
    print(f"{len(passed)} gecti, {len(failed)} kaldi")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
