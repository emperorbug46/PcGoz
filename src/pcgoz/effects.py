"""Eylemden once ve sonra ekran: ne degisti?

Denetim gunlugunde (2026-08-30/31, 239 eylem) eylemler arasi 5-40 sn vardi:
model her eylemden sonra sonucu ayrica okuyordu. Daha kotusu, pc_invoke
"basarili" donup hicbir sey yapmadiginda bu fark edilmiyordu -- ayni butona
4 sn sonra pc_do ile tekrar tiklandi. Eylemin kendi cevabi ne degistigini
soylerse ikisi de gerekmez.

Yontem: eylemden once hedef pencerenin anlik goruntusu, eylemden sonra ekran
duruluncaya (iki ardisik goruntu ayni) kadar yenileri. Ogeler RuntimeId ile
eslestiriliyor. Olculdu: goruntu basina 20-60 ms, odak 15 ms, pencere
listesi 0.5 ms.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from . import uia, windows
from .render import EXPAND_NAMES, TOGGLE_NAMES
from .textutil import one_line

#: Ilk bakis ve yoklama araligi. Durulma butcesi dolarsa "ekran hala
#: degisiyordu" denir -- saat, ilerleme cubugu gibi hic durmayan arayuzler.
_FIRST_LOOK_S = 0.15
_POLL_S = 0.15
_SETTLE_BUDGET_S = 1.2

#: Bundan kucuk pencereler (gizli yardimci pencereler) sayilmaz.
_MIN_WINDOW_AREA = 400

#: Her kategoriden en fazla bu kadar ornek gosterilir.
_SHOW = 4

#: Karsilastirilan durum: rol, ad, deger, isaret, secili, acik/kapali, etkin.
State = tuple[str, str, str, "int | None", bool, "int | None", bool]


def _state(node: uia.Node) -> State:
    return (node.role, node.name[:80], node.value[:80], node.toggle,
            node.selected, node.expand, node.enabled)


def _short(text: str, limit: int = 40) -> str:
    text = one_line(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass
class Scene:
    target: int
    fg: int
    windows: dict[int, str]                              # hwnd -> kisa tanim
    focus: uia.Node | None
    nodes: dict[tuple[int, ...], uia.Node] | None        # hedefin gorunur ogeleri
    responding: bool = True

    def signature(self) -> tuple:
        nodes = (frozenset((rid, _state(n)) for rid, n in self.nodes.items())
                 if self.nodes is not None else None)
        return (self.fg, frozenset(self.windows.items()), _focus_key(self.focus), nodes)


def _focus_key(node: uia.Node | None) -> object:
    if node is None:
        return None
    return node.rid or node.label


def _window_label(hwnd: int) -> str:
    title = windows.window_text(hwnd)
    process = windows.process_name(windows.window_pid(hwnd)) or "?"
    kind = " menu/acilir" if windows.is_popup(hwnd) else ""
    return f'{process}{kind} "{_short(title, 50)}"' if title else f"{process}{kind} {windows.window_class(hwnd)}"


def capture(target: int) -> Scene:
    shown: dict[int, str] = {}
    for hwnd in windows.visible_hwnds():
        left, top, right, bottom = windows.frame_rect(hwnd)
        if (right - left) * (bottom - top) >= _MIN_WINDOW_AREA:
            shown[hwnd] = _window_label(hwnd)

    nodes = None
    responding = True
    if target and windows.is_window(target):
        try:
            # Sadece ekrandakiler: agir sayfada 1.4 sn -> ~0.2 sn (bkz. uia._cache_request).
            tree = uia.snapshot(uia.element_from_handle(target), onscreen_only=True)
            nodes = {n.rid: n for n in tree.walk() if n.rid and n.area > 0 and not n.offscreen}
        except uia.NotResponding:
            responding = False
        except Exception:  # noqa: BLE001 -- pencere o anda kapaniyor olabilir
            nodes = None
    fg = windows.foreground_window()
    # Donmus on plan penceresinde odak okumasi 2 sn zaman asimina kadar bekler.
    focus = uia.focused_node() if fg and windows.responds(fg) else None
    return Scene(target, fg, shown, focus, nodes, responding)


def settle(target: int) -> tuple[Scene, float, bool]:
    """Ekran duruluncaya kadar bekle. Doner: (son durum, gecen sure, duruldu mu)."""
    started = time.monotonic()
    time.sleep(_FIRST_LOOK_S)
    previous = capture(target)
    if not previous.responding:
        return previous, time.monotonic() - started, True
    while time.monotonic() - started < _SETTLE_BUDGET_S:
        time.sleep(_POLL_S)
        current = capture(target)
        if not current.responding:
            # Donmus pencereyi izlemeye devam etmek her turda yoklama bekler.
            return current, time.monotonic() - started, True
        if current.signature() == previous.signature():
            return current, time.monotonic() - started, True
        previous = current
    return previous, time.monotonic() - started, False


def _change(old: uia.Node, new: uia.Node) -> str:
    parts = []
    if old.name != new.name:
        parts.append(f'ad "{_short(old.name)}" -> "{_short(new.name)}"')
    if old.value != new.value:
        parts.append(f'deger "{_short(old.value)}" -> "{_short(new.value)}"')
    if old.toggle != new.toggle:
        parts.append(f"{TOGGLE_NAMES.get(old.toggle, old.toggle)} -> {TOGGLE_NAMES.get(new.toggle, new.toggle)}")
    if old.selected != new.selected:
        parts.append("secildi" if new.selected else "secim kalkti")
    if old.expand != new.expand:
        parts.append(f"{EXPAND_NAMES.get(old.expand, old.expand)} -> {EXPAND_NAMES.get(new.expand, new.expand)}")
    if old.enabled != new.enabled:
        parts.append("etkin oldu" if new.enabled else "pasif oldu")
    return ", ".join(parts)


def _worth_showing(nodes: list[uia.Node]) -> list[uia.Node]:
    """Metni olanlar once, tiklanabilirler once; metinsiz kapsayicilar hic."""
    with_text = [n for n in nodes if n.text]
    with_text.sort(key=lambda n: (not n.actionable, n.rect[1], n.rect[0]))
    return with_text[:_SHOW]


def describe(before: Scene, after: Scene, elapsed: float, settled: bool, *,
             skip: frozenset[tuple[int, ...]] = frozenset(),
             register: Callable[[uia.Node], str] | None = None) -> tuple[str, bool]:
    """Iki durum arasindaki farki kisa metne cevir. Doner: (metin, bir sey degisti mi).

    `register` verilirse gosterilen yeni/degisen ogelere ref verilir -- model
    onlara ayrica pc_read yapmadan dokunabilsin.
    """
    def tag(node: uia.Node) -> str:
        return f" [{register(node)}]" if register else ""

    def fg_label(scene: Scene) -> str:
        if not scene.fg:
            return "(on planda pencere yok)"
        return scene.windows.get(scene.fg, f"hwnd={scene.fg}")

    lines: list[str] = []
    if after.fg != before.fg:
        where = f" (hwnd={after.fg})" if after.fg in after.windows else ""
        lines.append(f"on plan: {fg_label(before)} -> {fg_label(after)}{where}")
    opened = [h for h in after.windows if h not in before.windows]
    closed = [h for h in before.windows if h not in after.windows]
    lines += [f"acildi: hwnd={h} {after.windows[h]}" for h in opened[:_SHOW]]
    lines += [f"kapandi: hwnd={h} {before.windows[h]}" for h in closed[:_SHOW]]
    retitled = [h for h in after.windows
                if h in before.windows and before.windows[h] != after.windows[h]]
    lines += [f"baslik: hwnd={h} {before.windows[h]} -> {after.windows[h]}" for h in retitled[:_SHOW]]
    if _focus_key(before.focus) != _focus_key(after.focus):
        was = before.focus.label if before.focus else "(yok)"
        now = after.focus.label if after.focus else "(yok)"
        lines.append(f"odak: {was} -> {now}")

    if not after.responding:
        lines.append(f"hedef pencere (hwnd={after.target}) YANIT VERMIYOR -- eylemden sonra "
                     "dondu ya da cok mesgul; birkac saniye sonra tekrar bak")
    elif before.nodes is not None and after.nodes is None:
        lines.append(f"hedef pencere (hwnd={before.target}) kapandi ya da artik okunamiyor")
    elif before.nodes is not None and after.nodes is not None:
        added = [n for rid, n in after.nodes.items() if rid not in before.nodes]
        removed = [n for rid, n in before.nodes.items() if rid not in after.nodes]
        changed = [(before.nodes[rid], n) for rid, n in after.nodes.items()
                   if rid in before.nodes and rid not in skip
                   and _state(before.nodes[rid]) != _state(n)]
        if added or removed or changed:
            lines.append(f"pencere icerigi: {len(added)} yeni, {len(removed)} kayboldu, "
                         f"{len(changed)} degisti")
            # Ayni rol+adla hem kaybolup hem belirenler cogu zaman yeniden cizilen
            # ayni ogedir (Gorev Yoneticisi sekme degisiminde). "-" listesinde
            # gostermek hala orada olan bir seyi "gitti" diye bildirirdi.
            gone = Counter(n.label for n in removed)
            again: set[int] = set()
            for n in added:
                if gone[n.label]:
                    gone[n.label] -= 1
                    again.add(id(n))
            back = Counter(n.label for n in added if id(n) in again)
            only_removed = []
            for n in removed:
                if back[n.label]:
                    back[n.label] -= 1
                else:
                    only_removed.append(n)
            lines += [f"  + {n.label}{tag(n)}" + (" (yeniden olusturuldu)" if id(n) in again else "")
                      for n in _worth_showing(added)]
            lines += [f"  - {n.label}" for n in _worth_showing(only_removed)]
            lines += [f"  ~ {old.label}{tag(new)}: {_change(old, new)}"
                      for old, new in changed[:_SHOW]]

    if not lines:
        return f"Gorunur degisiklik yok ({elapsed:.1f} sn izlendi).", False
    note = "" if settled else ", ekran hala degisiyordu"
    return f"Sonuc ({elapsed:.1f} sn izlendi{note}):\n" + "\n".join(lines), True
