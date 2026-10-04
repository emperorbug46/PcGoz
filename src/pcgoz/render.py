"""Node agacini metne cevirir.

Bu modul aracin kullanilabilir olup olmamasini belirliyor. Ham bir UIA agaci
on binlerce dugum olabilir; oldugu gibi dokulurse tek cagri butun baglami yer.
Uc kademe filtre var: hangi ogeler tutulacak, ayni gorunen kardesler nasil
katlanacak, ve sert bir karakter tavani.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config
from .refs import registry
from .textutil import one_line
from .uia import ACTION_PATTERNS, INTERACTIVE_ROLES, TEXT_ROLES, Node

TOGGLE_NAMES = {0: "isaretsiz", 1: "isaretli", 2: "belirsiz"}
EXPAND_NAMES = {0: "kapali", 1: "acik", 2: "kismen-acik", 3: "yaprak"}


@dataclass
class RenderResult:
    text: str
    shown: int
    total: int
    truncated: bool


def _is_kept(node: Node, mode: str) -> bool:
    """Bu oge kendi basina gosterilmeye deger mi?"""
    if node.area <= 0 or node.offscreen:
        return False
    if mode == "all":
        return True
    if mode == "text":
        return bool(node.text) and (node.role in TEXT_ROLES or "Text" in node.patterns)
    # mode == "interactive"
    if node.enabled and (
        node.focusable
        or (node.patterns & ACTION_PATTERNS)
        or node.role in INTERACTIVE_ROLES
    ):
        return True
    # Etkilesimli olmayan ama pencerede ne yazdigini soyleyen ogeler.
    return node.role in TEXT_ROLES and bool(node.text)


def _describe(node: Node) -> str:
    parts = [node.role]
    if node.text:
        text = one_line(node.text)
        shown = text if len(text) <= 120 else text[:117] + "..."
        parts.append(f'"{shown}"')
    if node.ref:
        parts.append(f"[{node.ref}]")

    left, top = node.rect[0], node.rect[1]
    parts.append(f"({left},{top} {node.width}x{node.height})")

    flags = []
    if not node.enabled:
        flags.append("pasif")
    if node.password:
        flags.append("PAROLA")
    if node.toggle is not None and node.toggle in TOGGLE_NAMES:
        flags.append(TOGGLE_NAMES[node.toggle])
    if node.expand is not None and node.expand in EXPAND_NAMES and node.expand != 3:
        flags.append(EXPAND_NAMES[node.expand])
    if node.selected:
        flags.append("secili")
    if node.value and node.value != node.name:
        value = one_line(node.value)
        val = value if len(value) <= 60 else value[:57] + "..."
        flags.append(f'deger="{val}"')
    elif node.role == "ComboBox":
        # Kapali acilir kutunun secimi ogenin degeri degil, secili cocugu (WinUI
        # Ayarlar'da "Olcek"): eskiden gorunmuyordu, ref_id ile ayrica okumak gerekti.
        chosen = next((c for c in node.children if c.selected and c.name), None)
        if chosen is not None:
            flags.append(f'secim="{one_line(chosen.name)[:60]}"')
    if flags:
        parts.append(" ".join(flags))
    return " ".join(parts)


def _first_text(node: Node, look: int = 40) -> str:
    """Alt agactaki ilk bos olmayan metin (ilk `look` oge icinde)."""
    for index, child in enumerate(node.walk()):
        if index >= look:
            break
        if child.text:
            return child.text[:40]
    return ""


def _signature(node: Node) -> tuple[str, str]:
    """Kardes katlamasi icin: ayni rol + ayni metin = tekrar.

    Kendi adi olmayan kapsayicida (tablo satiri, liste ogesi) alt agacin ilk
    metnine bakilir. Eskiden sadece kendi adi sayiliyordu: adsiz satirlarin
    hepsi ("DataItem", "") gorunup ekrandaki satirlar "... ayni turden 18 oge
    daha" diye gizleniyordu -- icerikleri farkliyken (olculdu, 2026-09-24).
    """
    return (node.role, node.text[:40] if node.text else _first_text(node))


def render(
    root: Node,
    hwnd: int,
    *,
    mode: str = "interactive",
    depth: int | None = None,
    max_chars: int | None = None,
) -> RenderResult:
    max_depth = depth if depth is not None else config.DEFAULT_DEPTH
    budget = max_chars if max_chars is not None else config.DEFAULT_MAX_CHARS
    total = root.count()

    # 1) Hangi ogeler kendi basina tutuluyor.
    kept: dict[int, bool] = {}
    for node in root.walk():
        kept[id(node)] = _is_kept(node, mode)

    # 2) Tutulan bir torunu olan ust ogeler yapiyi korumak icin gosterilir.
    visible: dict[int, bool] = {}

    def mark(node: Node) -> bool:
        any_child = False
        for child in node.children:
            if mark(child):
                any_child = True
        result = kept[id(node)] or any_child
        visible[id(node)] = result
        return result

    mark(root)

    lines: list[str] = []
    shown = 0
    truncated = False
    used = 0

    def emit(node: Node, indent: int) -> None:
        nonlocal shown, truncated, used
        if truncated:
            return
        registry.register(node, hwnd)
        line = "  " * indent + _describe(node)
        used += len(line) + 1
        if used > budget:
            truncated = True
            return
        lines.append(line)
        shown += 1

    def walk(node: Node, indent: int) -> None:
        if truncated or indent > max_depth:
            return

        children = [c for c in node.children if visible.get(id(c))]

        # 3) Gecis kutularini atla: kendisi icerik tasimayan ve tek gorunur
        #    cocugu olan sarmalayicilar. Chromium agaclarindaki
        #    Pane > Pane > Pane zincirlerini duzlestiriyor.
        if not kept[id(node)] and len(children) == 1 and indent > 0:
            walk(children[0], indent)
            return

        emit(node, indent)
        if truncated:
            return

        # 4) Ayni gorunen ardisik kardesleri katla.
        index = 0
        while index < len(children) and not truncated:
            sig = _signature(children[index])
            run = index
            while run < len(children) and _signature(children[run]) == sig:
                run += 1
            group = children[index:run]
            if len(group) > config.SIBLING_COLLAPSE_AFTER:
                for child in group[: config.SIBLING_COLLAPSE_AFTER]:
                    walk(child, indent + 1)
                hidden = len(group) - config.SIBLING_COLLAPSE_AFTER
                lines.append("  " * (indent + 1) + f"... ayni turden {hidden} oge daha")
            else:
                for child in group:
                    walk(child, indent + 1)
            index = run

    walk(root, 0)

    text = "\n".join(lines)
    scope = "ekrandaki " if config.ONSCREEN_ONLY else ""
    if truncated:
        text += (
            f"\n\n[kesildi -- {scope}{total} ogeden {shown} tanesi gosterildi. "
            "Daraltmak icin: ref_id ile bir alt agaca in, filter='interactive' "
            "kullan, ya da aradigin sey belliyse pc_find'i tercih et.]"
        )
    elif mode == "interactive" and shown < total:
        text += (f"\n\n[{scope}{total} ogeden {shown} tanesi gosterildi (filter='interactive'). "
                 "Tamami icin filter='all'.]")

    return RenderResult(text=text or "(gosterilecek oge yok)", shown=shown,
                        total=total, truncated=truncated)


def render_matches(nodes: list[Node], hwnd: int, limit: int = 20) -> str:
    """pc_find sonuclari -- duz liste, hiyerarsi yok."""
    if not nodes:
        return "(eslesme yok)"
    lines = []
    for node in nodes[:limit]:
        registry.register(node, hwnd)
        lines.append(_describe(node))
    if len(nodes) > limit:
        lines.append(f"... {len(nodes) - limit} eslesme daha (sorguyu daralt)")
    return "\n".join(lines)
