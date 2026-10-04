"""UIA desenleriyle eylem -- fareye dokunmadan.

Fiziksel tiklamadan neden daha iyi: odagi calmaz, imleci oynatmaz, pencere
kismen ortuluyken ya da arkadayken de calisir, ve "tam olarak bu ogeye"
gider -- araya bir ipucu balonu ya da acilan menu girse bile yanlis seye
tiklama ihtimali yok. Once bu denenmeli, tiklama geri dusus yolu.
"""

from __future__ import annotations

from typing import Any

from .uia import UIA, Node

_PATTERNS: dict[str, tuple[int, Any]] = {
    "invoke": (UIA.UIA_InvokePatternId, UIA.IUIAutomationInvokePattern),
    "toggle": (UIA.UIA_TogglePatternId, UIA.IUIAutomationTogglePattern),
    "value": (UIA.UIA_ValuePatternId, UIA.IUIAutomationValuePattern),
    "expand": (UIA.UIA_ExpandCollapsePatternId, UIA.IUIAutomationExpandCollapsePattern),
    "collapse": (UIA.UIA_ExpandCollapsePatternId, UIA.IUIAutomationExpandCollapsePattern),
    "select": (UIA.UIA_SelectionItemPatternId, UIA.IUIAutomationSelectionItemPattern),
    "scroll_into_view": (UIA.UIA_ScrollItemPatternId, UIA.IUIAutomationScrollItemPattern),
}

ACTIONS = tuple(_PATTERNS)


class PatternUnavailable(RuntimeError):
    """Oge bu deseni desteklemiyor."""


def _get(node: Node, action: str) -> Any:
    if action not in _PATTERNS:
        raise ValueError(f"bilinmeyen eylem: {action!r} (secenekler: {', '.join(ACTIONS)})")
    pattern_id, interface = _PATTERNS[action]
    try:
        raw = node.el.GetCurrentPattern(pattern_id)
    except Exception as exc:  # noqa: BLE001
        raise PatternUnavailable(f"{node.label}: desen alinamadi ({exc})") from exc
    if not raw:
        raise PatternUnavailable(
            f"{node.label} '{action}' desenini desteklemiyor "
            f"(destekledikleri: {', '.join(sorted(node.patterns)) or 'yok'}). "
            "pc_do ile fiziksel tiklamayi dene."
        )
    return raw.QueryInterface(interface)


def apply(node: Node, action: str, value: str | None = None) -> str:
    """Ogeye deseni uygula, ne olduguna dair kisa bir ozet dondur."""
    pattern = _get(node, action)

    if action == "invoke":
        pattern.Invoke()
        return f"{node.label} calistirildi (Invoke)."

    if action == "toggle":
        before = pattern.CurrentToggleState
        pattern.Toggle()
        return f"{node.label} degistirildi (ToggleState {before} -> {pattern.CurrentToggleState})."

    if action == "value":
        if value is None:
            raise ValueError("'value' eylemi icin value parametresi gerekli.")
        if pattern.CurrentIsReadOnly:
            raise PatternUnavailable(f"{node.label} salt okunur, degeri degistirilemez.")
        pattern.SetValue(value)
        return f"{node.label} degeri ayarlandi ({len(value)} karakter)."

    if action == "expand":
        pattern.Expand()
        return f"{node.label} acildi."

    if action == "collapse":
        pattern.Collapse()
        return f"{node.label} kapatildi."

    if action == "select":
        pattern.Select()
        return f"{node.label} secildi."

    if action == "scroll_into_view":
        pattern.ScrollIntoView()
        return f"{node.label} gorunur alana kaydirildi."

    raise ValueError(f"islenmemis eylem: {action}")


def read_text(node: Node) -> str:
    """Ogenin metnini en iyi kaynaktan al: TextPattern -> Value -> Name."""
    try:
        raw = node.el.GetCurrentPattern(UIA.UIA_TextPatternId)
        if raw:
            pattern = raw.QueryInterface(UIA.IUIAutomationTextPattern)
            text = pattern.DocumentRange.GetText(-1)
            if text:
                return text
    except Exception:  # noqa: BLE001 -- TextPattern yoksa asagi dus
        pass

    try:
        raw = node.el.GetCurrentPattern(UIA.UIA_ValuePatternId)
        if raw:
            value = raw.QueryInterface(UIA.IUIAutomationValuePattern).CurrentValue
            if value:
                return str(value)
    except Exception:  # noqa: BLE001
        pass

    return node.name or ""


def _text_pattern_text(node: Node) -> str | None:
    try:
        raw = node.el.GetCurrentPattern(UIA.UIA_TextPatternId)
        if not raw:
            return None
        text = raw.QueryInterface(UIA.IUIAutomationTextPattern).DocumentRange.GetText(-1)
        return text if text else None
    except Exception:  # noqa: BLE001
        return None


def collect_text(root: Node, limit: int = 20000) -> str:
    """Alt agactaki metni topla.

    Once TextPattern tasiyan bir alt oge araniyor -- belge/duzenleme alaninin
    gercek icerigi sadece orada. Pencerenin kendisinde TextPattern yok, ve
    Name/Value'ya guvenmek yaniltici: Not Defteri'nde belgenin Name'i
    "Metin duzenleyici", Value'su "0.0" cikiyor; ikisi de icerik degil.
    """
    document = _text_pattern_text(root)
    if not document:
        for node in root.walk():
            if "Text" not in node.patterns or node.offscreen or node.area <= 0:
                continue
            found = _text_pattern_text(node)
            if found and (document is None or len(found) > len(document)):
                document = found

    # TextPattern'i bulup isi bitmis saymak yaniltiyordu: Ayarlar'da bir alt
    # ogenin TextPattern'i 105 karakter veriyor, ad toplama ise pencerenin
    # tamamini 1389 karakterle anlatiyor. Ikisi farkli seyler -- biri belgenin
    # icerigi, digeri arayuzun yazilari -- o yuzden ikisi de toplanip
    # birbirini gectigi olcude birlikte donuluyor.
    seen: set[str] = set()
    parts: list[str] = []
    used = 0
    for node in root.walk():
        if node.offscreen or node.area <= 0:
            continue
        text = (node.name or node.value or "").strip()
        if not text or text in seen or len(text) > 2000:
            continue
        seen.add(text)
        parts.append(text)
        used += len(text) + 1
        if used >= limit:
            parts.append("... (kesildi)")
            break
    labels = "\n".join(parts)

    if document and len(document) >= len(labels):
        return document[:limit]
    if document:
        head = document[: limit // 2]
        rest = labels[: limit - len(head) - 40]
        return f"{head}\n\n--- arayuz metinleri ---\n{rest}"
    return labels[:limit]
