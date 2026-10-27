"""IUIAutomation sarmalayicisi.

Hiz burada tek bir seye bagli: `BuildUpdatedCache` ile TreeScope_Subtree.
Tek COM cagrisi butun alt agaci istenen ozelliklerle birlikte getiriyor
(olculdu: 155 oge / 40 ms), sonrasindaki gezinme tamamen bellekte (1 ms).
Ozellik-basina-COM-cagrisi yaklasimi ayni agacta saniyeler suruyor.
"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import comtypes
import comtypes.client

from . import config
from .textutil import clean, one_line

user32 = ctypes.WinDLL("user32", use_last_error=True)


def _load_uia_types() -> Any:
    """UIAutomationClient tip modulunu getir; bayatsa yeniden uret.

    comtypes'in comtypes/gen altina urettigi sarmalayici dll'in mtime'ina
    kilitli. Windows Update dll'i degistirince dogrudan
    `from comtypes.gen import UIAutomationClient` "Typelib different than
    module" ile patliyor ve sunucu hic acilmiyor -- 2026-08-31'deki
    guncellemeden sonra 23 gun boyle kaldi. GetModule ayni hatayi yakalayip
    modulu yeniden uretiyor (ilk acilista ~1-2 sn).
    """
    return comtypes.client.GetModule(config.UIA_TYPELIB)


UIA = _load_uia_types()

# --------------------------------------------------------------------------
# Sabitler
# --------------------------------------------------------------------------

#: Cache'e alinan ozellikler. Hepsi tek COM cagrisinda geliyor.
_CACHED_PROPS = (
    "Name", "ControlType", "AutomationId", "ClassName", "BoundingRectangle",
    "IsEnabled", "IsOffscreen", "IsKeyboardFocusable", "IsPassword", "ProcessId",
    "HelpText", "NativeWindowHandle",
    "ValueValue", "ToggleToggleState", "SelectionItemIsSelected",
    "ExpandCollapseExpandCollapseState", "RangeValueValue",
    "IsInvokePatternAvailable", "IsTogglePatternAvailable", "IsValuePatternAvailable",
    "IsRangeValuePatternAvailable", "IsExpandCollapsePatternAvailable",
    "IsSelectionItemPatternAvailable", "IsTextPatternAvailable",
    "IsScrollItemPatternAvailable", "IsLegacyIAccessiblePatternAvailable",
)

#: ControlType id -> okunabilir ad ("UIA_ButtonControlTypeId" -> "Button")
CONTROL_TYPES: dict[int, str] = {
    v: k[4:-13] for k, v in vars(UIA).items()
    if k.startswith("UIA_") and k.endswith("ControlTypeId")
}

#: Deseni olmasa da etkilesimli sayilan roller.
INTERACTIVE_ROLES = frozenset({
    "Button", "CheckBox", "ComboBox", "Edit", "Hyperlink", "ListItem", "MenuItem",
    "RadioButton", "Slider", "SplitButton", "TabItem", "TreeItem", "Spinner",
    "Thumb", "ScrollBar", "DataItem", "HeaderItem",
})

#: Etkilesim saglayan desenler.
ACTION_PATTERNS = frozenset({"Invoke", "Toggle", "ExpandCollapse", "SelectionItem", "Value"})

#: Icerik tasidigi icin tutulan (ama tiklanmayan) roller.
TEXT_ROLES = frozenset({"Text", "Document", "StatusBar", "ToolTip", "Image"})

WM_GETOBJECT = 0x003D
OBJID_CLIENT = -4
SMTO_ABORTIFHUNG = 0x0002

#: UIA cagrisi zaman asimina ugradiginda donen HRESULT (UIA_E_TIMEOUT).
UIA_E_TIMEOUT = -2146233083

#: E_FAIL, Turkce Windows'ta "Belirtilmemis hata". Gunlukteki COMError'larin hepsi buydu.
E_FAIL = -2147467259


def is_fail(exc: BaseException) -> bool:
    """COMError E_FAIL mi? (Zaman asimi, erisim reddi gibi digerleri degil.)"""
    return isinstance(exc, comtypes.COMError) and exc.hresult == E_FAIL


#: Donmus bir saglayiciya baglanma / islem suresi siniri (ms).
CONNECTION_TIMEOUT_MS = 2000
TRANSACTION_TIMEOUT_MS = 10000


class NotResponding(LookupError):
    """Hedef pencere mesaj islemiyor (donmus ya da cok mesgul)."""


def not_responding(hwnd: int | None = None) -> NotResponding:
    where = f"hwnd {hwnd}" if hwnd else "Hedef uygulama"
    return NotResponding(
        f"{where} yanit vermiyor (donmus ya da cok mesgul) -- beklemedim, cunku donmus "
        "pencereye yapilan her cagri o kendine gelene kadar asili kalir. Birkac saniye "
        "sonra tekrar dene; devam ederse uygulama donmustur, kullaniciya soyle."
    )


_iuia: Any = None


def iuia() -> Any:
    """UIA kok nesnesi. Sadece worker ipliginden cagrilmali.

    CUIAutomation8, eski CUIAutomation'dan farkli olarak zaman asimi ayari
    veriyor. Olculdu: donmus bir pencerede eski sinif 20.7 sn (pencere
    kendine gelene kadar) bekledi, bu 2.0 sn'de UIA_E_TIMEOUT ile dondu.
    Butun araclar tek iplikte sirayla calistigi icin eski davranis donmus
    tek bir uygulamanin butun araclari kilitlemesi demekti.
    """
    global _iuia
    if _iuia is None:
        try:
            auto = comtypes.CoCreateInstance(
                UIA.CUIAutomation8._reg_clsid_, interface=UIA.IUIAutomation2,
                clsctx=comtypes.CLSCTX_INPROC_SERVER,
            )
            auto.ConnectionTimeout = CONNECTION_TIMEOUT_MS
            auto.TransactionTimeout = TRANSACTION_TIMEOUT_MS
        except Exception:  # noqa: BLE001 -- Windows 8 oncesi: CUIAutomation8 yok
            auto = comtypes.CoCreateInstance(
                UIA.CUIAutomation._reg_clsid_, interface=UIA.IUIAutomation,
                clsctx=comtypes.CLSCTX_INPROC_SERVER,
            )
        _iuia = auto
    return _iuia


def _element_request() -> Any:
    """Tek oge icin (odak, noktadaki oge): alt agac DEGIL.

    Eskiden bunlar da _cache_request'i, yani TreeScope_Subtree'yi kullaniyordu:
    tarayicida odak sayfa belgesindeyken tek ogeyi okumak butun sayfayi
    (9000 oge) onbellege cekiyordu -- her 'ne degisti' turunda.
    """
    cr = iuia().CreateCacheRequest()
    for prop in _CACHED_PROPS:
        cr.AddProperty(getattr(UIA, f"UIA_{prop}PropertyId"))
    cr.TreeScope = UIA.TreeScope_Element
    return cr


def _cache_request(*, onscreen_only: bool = False) -> Any:
    """onscreen_only: ekran disindaki ogeleri UIA'nin kendisi elesin.

    Olculdu (Brave, 1500 satirlik sayfa, 9055 oge): butun agac 509 ms,
    suzgecle 146 ms ve 223 oge -- Python'a cevirme de o oranda kisaliyor
    (4000 ogelik goruntu toplamda 1.4 sn surerken). Suzulen ebeveynin
    gorunur cocuklari yukari tasinir, yani duz bir liste isteyen yerler
    (effects) icin ayni sonuc; agac yapisi gereken pc_read icin degil.
    """
    auto = iuia()
    cr = auto.CreateCacheRequest()
    for prop in _CACHED_PROPS:
        cr.AddProperty(getattr(UIA, f"UIA_{prop}PropertyId"))
    cr.TreeScope = UIA.TreeScope_Subtree
    cr.TreeFilter = auto.ControlViewCondition
    if onscreen_only:
        cr.TreeFilter = auto.CreateAndCondition(
            auto.ControlViewCondition,
            auto.CreatePropertyCondition(UIA.UIA_IsOffscreenPropertyId, False))
    return cr


# --------------------------------------------------------------------------
# Dugum
# --------------------------------------------------------------------------

@dataclass
class Node:
    """Bir UIA ogesinin bellekteki goruntusu + canli COM isaretcisi."""

    el: Any
    role: str
    name: str
    value: str
    aid: str
    cls: str
    rect: tuple[int, int, int, int]      # left, top, right, bottom (fiziksel piksel)
    enabled: bool
    offscreen: bool
    focusable: bool
    password: bool
    pid: int
    hwnd: int
    patterns: frozenset[str]
    toggle: int | None
    selected: bool
    expand: int | None
    help: str = ""
    #: UIA RuntimeId -- ogenin kimligi. Ayni oge her okumada ayni ref'i alsin
    #: ve iki anlik goruntu arasinda "ne degisti" eslestirilebilsin diye.
    rid: tuple[int, ...] | None = None
    children: list["Node"] = field(default_factory=list)
    ref: str | None = None

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def center(self) -> tuple[int, int]:
        return ((self.rect[0] + self.rect[2]) // 2, (self.rect[1] + self.rect[3]) // 2)

    @property
    def text(self) -> str:
        """Ogenin tasidigi en anlamli metin."""
        return self.name or self.value or self.help or self.aid or ""

    @property
    def label(self) -> str:
        """Log ve hata mesajlarinda kullanilan kisa tanim."""
        return f'{self.role} "{one_line(self.text or self.cls or "?")[:60]}"'

    @property
    def actionable(self) -> bool:
        return bool(self.focusable or (self.patterns & ACTION_PATTERNS)
                    or self.role in INTERACTIVE_ROLES)

    def walk(self) -> Iterator["Node"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def count(self) -> int:
        return sum(1 for _ in self.walk())


def _prop(el: Any, name: str, default: Any = None) -> Any:
    try:
        val = el.GetCachedPropertyValue(getattr(UIA, f"UIA_{name}PropertyId"))
    except Exception:  # noqa: BLE001 -- ozellik desteklenmiyorsa varsayilan
        return default
    return default if val is None else val


def _runtime_id(el: Any) -> tuple[int, ...] | None:
    """Olculdu: surecler arasi cagri degil, 229 oge icin toplam 1.3 ms."""
    try:
        rid = el.GetRuntimeId()
    except Exception:  # noqa: BLE001 -- bazi ogeler RuntimeId vermez
        return None
    return tuple(int(v) for v in rid) if rid else None


def _to_node(el: Any) -> Node:
    raw = _prop(el, "BoundingRectangle", None)
    if raw is not None and len(raw) == 4:
        left, top, width, height = (int(v) for v in raw)
        rect = (left, top, left + width, top + height)
    else:
        rect = (0, 0, 0, 0)

    patterns: set[str] = set()
    for pat in ("Invoke", "Toggle", "Value", "RangeValue", "ExpandCollapse",
                "SelectionItem", "Text", "ScrollItem", "LegacyIAccessible"):
        if _prop(el, f"Is{pat}PatternAvailable", False):
            patterns.add(pat)

    # Desen destegi olmayan ozellikleri okumak yaniltici: UIA None degil
    # varsayilan doner. Kontrol edilmezse her metin dugumu "belirsiz" durumlu
    # ve deger="0.0" gorunuyor -- hem yanlis hem her satiri sisiriyor.
    value = (_prop(el, "ValueValue", "") or "") if "Value" in patterns else ""
    if not value and "RangeValue" in patterns:
        rng = _prop(el, "RangeValueValue", None)
        if rng is not None:
            value = str(rng)

    ct = int(_prop(el, "ControlType", 0) or 0)
    return Node(
        el=el,
        role=CONTROL_TYPES.get(ct, f"Type{ct}"),
        name=clean(_prop(el, "Name", "") or ""),
        value=clean(str(value)),
        aid=clean(_prop(el, "AutomationId", "") or ""),     # Qt nesne yolu burada da geliyor
        cls=(_prop(el, "ClassName", "") or "").strip(),
        rect=rect,
        enabled=bool(_prop(el, "IsEnabled", True)),
        offscreen=bool(_prop(el, "IsOffscreen", False)),
        focusable=bool(_prop(el, "IsKeyboardFocusable", False)),
        password=bool(_prop(el, "IsPassword", False)),
        pid=int(_prop(el, "ProcessId", 0) or 0),
        hwnd=int(_prop(el, "NativeWindowHandle", 0) or 0),
        patterns=frozenset(patterns),
        toggle=_prop(el, "ToggleToggleState", None) if "Toggle" in patterns else None,
        selected=bool(_prop(el, "SelectionItemIsSelected", False))
        if "SelectionItem" in patterns else False,
        expand=_prop(el, "ExpandCollapseExpandCollapseState", None)
        if "ExpandCollapse" in patterns else None,
        help=clean(_prop(el, "HelpText", "") or ""),
        rid=_runtime_id(el),
    )


# --------------------------------------------------------------------------
# Anlik goruntu
# --------------------------------------------------------------------------

def snapshot(el: Any, *, max_elements: int | None = None, onscreen_only: bool = False) -> Node:
    """Alt agacin tamamini tek COM cagrisiyla al ve Node agacina cevir."""
    limit = max_elements or config.MAX_ELEMENTS
    try:
        cached = el.BuildUpdatedCache(_cache_request(onscreen_only=onscreen_only))
    except comtypes.COMError as exc:
        if exc.hresult == UIA_E_TIMEOUT:
            raise not_responding() from exc
        raise
    count = 0

    def build(src: Any, depth: int) -> Node:
        nonlocal count
        node = _to_node(src)
        count += 1
        if count >= limit or depth >= config.DEPTH_HARD_LIMIT:
            return node
        try:
            kids = src.GetCachedChildren()
        except Exception:  # noqa: BLE001 -- yaprakta cocuk dizisi olmayabilir
            return node
        if not kids:
            return node
        for i in range(kids.Length):
            if count >= limit:
                break
            node.children.append(build(kids.GetElement(i), depth + 1))
        return node

    return build(cached, 0)


def nudge_accessibility(hwnd: int) -> None:
    """Chromium/Electron erisilebilirligi tembel acar.

    WM_GETOBJECT/OBJID_CLIENT mesaji uygulamaya "birileri seni okuyor" der.
    Olculdu: Electron penceresi ilk dokunusta 15 oge veriyordu, aktivasyondan
    sonra 155. Bu yuzden kucuk agaclarda durtup tekrar deniyoruz.
    """
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(
        ctypes.c_void_p(hwnd), WM_GETOBJECT, 0, ctypes.c_ssize_t(OBJID_CLIENT),
        SMTO_ABORTIFHUNG, 800, ctypes.byref(result),
    )


#: Tarayici pencere siniflari. Bunlarda erisilebilirlik her zaman tembel acilir,
#: o yuzden ilk dokunusta kosulsuz durtuluyor.
BROWSER_CLASS_PREFIXES = ("Chrome_WidgetWin_", "MozillaWindowClass")

#: Bir kez uyandirilan pencereyi tekrar durtmeye gerek yok; her okumaya
#: gereksiz gecikme eklemesin diye hangi pencereleri durttugumuzu tutuyoruz.
_nudged: set[int] = set()

#: Icerik alanini ararken pencerenin ustunden atilan serit (arac cubugu / sekmeler).
_CHROME_BAND_RATIO = 0.12
_CHROME_BAND_MIN = 60
_CHROME_BAND_MAX = 170

#: Icerik alaninda bundan az gorunur oge varsa "sayfa daha gelmemis" sayilir.
_CONTENT_SUSPICIOUS_BELOW = 5

#: Buyumeyi bekleme butcesi. Olculdu: soguk bir tarayicida youtube.com'un
#: sayfa agaci pencere gorundukten 2.5 sn sonra geliyor. 2.5 tam sinirda
#: kaliyordu, pay birakildi.
_GROWTH_TIMEOUT = 4.5
_GROWTH_POLL = 0.15

#: Bilinen bir pencereyi tekrar okurken kisa onay turu.
_RECHECK_TIMEOUT = 0.6


def window_class_of(hwnd: int) -> str:
    from . import windows        # yerel import: windows.py'nin uia'yi cagirmasi dairesel olur

    return windows.window_class(hwnd)


def content_element_count(tree: Node) -> int:
    """Pencerenin arac cubugu seridi altinda kac gorunur oge var?

    "Pencere geldi ama ici bos" durumunu yakalayan sinyal bu. Toplam oge
    sayisina bakmak ise yaramiyor: tarayici cercevesi tek basina 57-74 oge,
    bos sayfa 3, dolu sayfa 1300+. Sabit bir esik bu araligin neresine
    konursa konsun yanlis taraftan kesiyor.
    """
    top, bottom = tree.rect[1], tree.rect[3]
    height = max(1, bottom - top)
    band = min(_CHROME_BAND_MAX, max(_CHROME_BAND_MIN, int(height * _CHROME_BAND_RATIO)))
    # Kisa pencerelerde asgari serit butun yuksekligi yutuyor: 60px'lik gorev
    # cubugunda icerik alani her zaman 0 cikiyordu. Serit hicbir zaman
    # pencerenin %40'indan fazlasini kaplamasin.
    band = min(band, int(height * 0.4))
    cutoff = top + band
    return sum(
        1 for n in tree.walk()
        if n.rect[1] >= cutoff and n.area > 100 and not n.offscreen
    )


def document_size(tree: Node) -> int:
    """Sayfa (Document) dugumunun altindaki oge sayisi. Document yoksa -1."""
    docs = [n for n in tree.walk() if n.role == "Document"]
    if not docs:
        return -1
    return max(n.count() - 1 for n in docs)


def looks_unpopulated(tree: Node) -> bool:
    """Agac 'daha yuklenmemis' gorunuyor mu?

    Tarayicilarda olcut Document dugumunun boyutu, pencerede kac oge oldugu
    DEGIL. Soguk bir tarayicida youtube.com acilirken olculdu:

        t=0.1s  toplam 76   Document-alti -1   icerik alani 15
        t=0.9s  toplam 91   Document-alti  1   icerik alani 24
        t=1.7s  toplam 120  Document-alti 29   icerik alani 48
        t=2.5s  toplam 129  Document-alti 38   icerik alani 57  <- sayfa geldi

    Icerik alani sayaci ilk andan itibaren 15 -- yani hicbir esigin altina
    dusmuyor ve "sayfa yok" durumunu hic yakalamiyor. Document-alti ise
    -1 -> 1 -> 29 diye keskin ayiriyor. Tarayici disi pencerelerde Document
    olmadigi icin icerik alani sayacina donuluyor.

    Yanlis pozitifin bedeli birkac yuz milisaniye bekleme; yanlis negatifin
    bedeli bos sayfayi dolu sanip yanlis cevap vermek. Supheden yana taraflıyız.
    """
    if tree.cls.startswith(BROWSER_CLASS_PREFIXES):
        return document_size(tree) < _CONTENT_SUSPICIOUS_BELOW
    return content_element_count(tree) < _CONTENT_SUSPICIOUS_BELOW


def snapshot_window(hwnd: int, *, max_elements: int | None = None,
                    onscreen_only: bool = False) -> Node:
    """Pencereyi anlik goruntule, gerekirse erisilebilirligi uyandirip bekle.

    Chromium/Electron erisilebilirligi tembel acar: pencere hazir gorunur ama
    sayfa agaci bos gelir. Olculdu -- durtme gonderilmeden agac 7 saniye
    boyunca 57 ogede kaldi (icerik alani 3); WM_GETOBJECT durtmesinden sonra
    80'e cikti (icerik alani 18). Yani beklemek tek basina yetmiyor,
    durtme sart.
    """
    el = element_from_handle(hwnd)
    tree = snapshot(el, max_elements=max_elements, onscreen_only=onscreen_only)

    is_browser = window_class_of(hwnd).startswith(BROWSER_CLASS_PREFIXES)
    first_touch = hwnd not in _nudged
    unpopulated = looks_unpopulated(tree)

    if first_touch:
        # Ilk dokunus: uyandir ve buyumeye zaman tani.
        _nudged.add(hwnd)
        if is_browser or unpopulated:
            nudge_accessibility(hwnd)
            budget = _GROWTH_TIMEOUT
        else:
            return tree
    elif unpopulated and is_browser:
        # Bilinen bir tarayici penceresi bos gorunuyor: muhtemelen sayfa
        # degisiyor. Kisa bir onay turu yeter.
        budget = _RECHECK_TIMEOUT
    else:
        # Tarayici olmayan kucuk bir pencere gercekten kucuk olabilir --
        # her okumada beklemek sacma. (Gorev cubugu her okumada 2.7s
        # sururuyordu, olculdu.)
        return tree

    # Erken cikis SADECE "durağan VE dolu" oldugunda. Sadece duraganliga
    # bakmak yetmiyor: soguk tarayicida agac 0.1s-0.9s arasinda 76'dan 91'e
    # gidip bir sure oyle kaliyor, bu da "oturdu" gibi gorunuyor -- oysa
    # sayfa daha gelmemis. Duragan ama bos ise sonuna kadar beklenir.
    best = tree
    stable = 0
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        time.sleep(_GROWTH_POLL)
        current = snapshot(el, max_elements=max_elements, onscreen_only=onscreen_only)
        if current.count() > best.count():
            best, stable = current, 0
        else:
            stable += 1
        if stable >= 2 and not looks_unpopulated(best):
            break
    return best


def refresh_geometry(node: Node) -> bool:
    """Ogenin guncel konumunu ve gorunurlugunu oku. Oge artik yoksa False.

    Olculdu: oge basina 0.1-0.25 ms (iki surecler arasi cagri).
    """
    try:
        raw = node.el.CurrentBoundingRectangle
        offscreen = bool(node.el.CurrentIsOffscreen)
    except Exception:  # noqa: BLE001 -- oge yok olmus
        return False
    node.rect = (int(raw.left), int(raw.top), int(raw.right), int(raw.bottom))
    node.offscreen = offscreen
    return node.area > 0


def element_from_handle(hwnd: int) -> Any:
    from . import windows        # yerel import: dairesel bagimlilik

    if windows.is_window(hwnd) and not windows.responds(hwnd):
        raise not_responding(hwnd)
    try:
        return iuia().ElementFromHandle(ctypes.c_void_p(hwnd))
    except Exception as exc:  # noqa: BLE001
        raise LookupError(_access_hint(hwnd, exc)) from exc


def element_from_point(x: int, y: int) -> Node | None:
    point = UIA.tagPOINT(x=int(x), y=int(y))
    try:
        el = iuia().ElementFromPointBuildCache(point, _element_request())
    except Exception:  # noqa: BLE001
        return None
    return _to_node(el)


def focused_node() -> Node | None:
    try:
        el = iuia().GetFocusedElementBuildCache(_element_request())
    except Exception:  # noqa: BLE001 -- odakta oge olmayabilir
        return None
    return _to_node(el)


def live_value(node: Node) -> str | None:
    """Ogenin su anki degeri; ValuePattern yoksa None.

    Desen yoksa okumuyoruz: UIA desteklenmeyen ozellik icin None degil
    varsayilan doner ("0.0" gibi), degeri varmis gibi gorunur.
    """
    if "Value" not in node.patterns:
        return None
    try:
        return str(node.el.GetCurrentPropertyValue(UIA.UIA_ValueValuePropertyId) or "")
    except Exception:  # noqa: BLE001 -- oge yok olmus
        return None


def _access_hint(hwnd: int, exc: Exception) -> str:
    """Erisim hatasini belirsiz COM hatasi yerine anlasilir sebebe cevir."""
    from . import windows

    if not windows.is_window(hwnd):
        return f"hwnd {hwnd} artik yok (pencere kapanmis olabilir)."
    if windows.is_elevated_window(hwnd):
        return (
            f"hwnd {hwnd} yonetici hakkiyla calisan bir surece ait. Bu oturum "
            "yukseltilmemis oldugu icin Windows (UIPI) erisimi engelliyor -- "
            "okuyamam da tiklayamam da. Kullanicinin kendisi yapmali."
        )
    return f"hwnd {hwnd} icin UIA ogesi alinamadi: {exc}"
