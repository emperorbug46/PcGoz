"""MCP arac tanimlari.

Arac yuzeyi tarayici panelindeki API'yi bilerek taklit ediyor (pc_read ~
read_page, pc_do ~ computer, pc_find ~ find). Ayni kalibi kullanmak modelin
zaten alisik oldugu is akisini masaustune tasiyor.

Butun UIA ve girdi isleri tek bir worker ipliginde seri calisir; MCP'nin
is parcacigi havuzu ile COM apartmanlari arasindaki karisiklik boylece
hic ortaya cikmiyor.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import hashlib
import time
from typing import Annotated, Any, Callable, Literal, TypeVar

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.func_metadata import func_metadata
from pydantic import Field

from . import __version__
from . import (capture, clipboard, config, effects, inputguard, occlusion, ocr, patterns, render,
               safety, uia, windows, worker)
from .input import (
    KeyParseError,
    click as _click,
    cursor_pos,
    drag as _drag,
    move as _move,
    parse_combo,
    press as _press,
    scroll as _scroll,
    type_text as _type_text,
    virtual_screen,
)
from .refs import registry
from .textutil import fold, one_line

mcp = MCPServer(
    name="pcgoz",
    version=__version__,
    instructions=(
        "Windows masaustunu gormek ve kullanmak icin. Ne zaman: kullanicinin acik bir "
        "uygulamasiyla ilgili is; GELISTIRDIGIN bir masaustu uygulamasini derleyip "
        "calistirdiktan sonra arayuzunu dogrulamak (derlemenin gecmesi arayuzun calistigini "
        "gostermez). Web sayfasi icerigi icin tarayici araclari, dosya/surec isleri icin kabuk "
        "daha iyi. Ayrintili rehber: masaustu-kullanimi becerisi.\n\n"
        "Tipik akis: pc_windows ile "
        "pencereyi bul -> pc_read ile icindeki ogeleri ve ref_N numaralarini al -> "
        "pc_invoke veya pc_do ile eylem yap. Gorsel yorum gerekiyorsa pc_screenshot; "
        "annotate=True ile ref numaralari resmin uzerine kutu olarak cizilir, yani "
        "agactaki ref_7 ile resimdeki 7 numarali kutu ayni ogedir.\n\n"
        "Tiklamadan once mumkunse pc_invoke'u tercih et: odagi calmaz, imleci "
        "oynatmaz, ortulu pencerede de calisir.\n\n"
        "Eylem araclari sonucta ekranda ne degistigini soyler (yeni pencere/menu, "
        "odak, yeni ogeler ref'leriyle); cogu zaman ayrica pc_read gerekmez. Birkac "
        "adimi onceden biliyorsan pc_batch ile tek cagride calistir.\n\n"
        "Agac bos ya da uygulama erisilebilirlik vermiyorsa (oyun, Qt, canvas, uzak "
        "masaustu) pc_ocr yaziyi ekran koordinatiyla verir.\n\n"
        "Bu araclar kullanicinin gercek faresini ve klavyesini surer; eylem surerken "
        "kullanicinin fiziksel fare/klavyesi kilitlenir -- kullanici o an kullaniyor olsa da "
        "(pc_batch'te butun toplu is boyunca), sol ustte uyari gorunur. Kullanici aktifken "
        "hedefi ref ile ver ya da once pc_focus: odaktaki pencere kullanicininki olabilir. "
        "Her eylem diske loglanir. Kullanici Ctrl+Alt+Shift+Esc ile her an durdurabilir."
    ),
)

#: Son okunan agac -- ekran goruntusu etiketlemesi ayni ref numaralarini kullansin diye.
_last: dict[str, Any] = {"hwnd": 0, "tree": None}

#: Son ekran goruntusunun ekrana eslemesi: space='screenshot' koordinatlari
#: bununla cevriliyor. Modelin "kaynak + konum / olcek" hesabini kendisi
#: yapmasi hata kaynagiydi.
_last_shot: dict[str, Any] = {}

F = TypeVar("F", bound=Callable[..., Any])

#: Kayitli araclarin ham fonksiyonlari ve arguman dogrulayicilari -- pc_batch
#: adimlari MCP'den gecmeden cagirirken ayni dogrulamayi yapabilsin diye.
_RAW: dict[str, Callable[..., Any]] = {}
_VALIDATE: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _tool(**options: Any) -> Callable[[F], F]:
    """`mcp.tool` gibi, ama aracin hata metni modele ulassin.

    mcp 2.x ToolError disindaki her istisnanin metnini gizleyip istemciye
    sadece "Error executing tool X" gonderiyor. Bu aracin hata mesajlari ise
    modele yazilmis talimatlar -- "DONDURULDU, kullaniciya sor", "kullanici
    mesgul, force=True", "bayat ref, pc_read ile tazele". Gizlenirlerse model
    neyin ters gittigini bilemez ve ayni seyi tekrar dener. Olculdu:
    tests/mcp_istemci.py, "hata metni modele ulasti".

    Kayit sarmalanmis fonksiyonla yapiliyor ama moduldeki ad ham fonksiyon
    kaliyor -- dogrudan cagiran testler StaleRef/Frozen gibi turleri
    yakalayabilsin.
    """
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Her cagri bir kimlik alir; sonucu ve suresi gunluge yazilir.
            token = safety.CALL_ID.set(safety.next_call_id())
            try:
                return _audited(fn.__name__, lambda: fn(*args, **kwargs), kwargs)
            except Exception as exc:  # noqa: BLE001 -- metin modele gitsin
                if isinstance(exc, ToolError):
                    raise
                raise ToolError(f"{type(exc).__name__}: {exc}") from exc
            finally:
                safety.CALL_ID.reset(token)

        mcp.tool(**options)(wrapper)
        _RAW[fn.__name__] = fn
        _VALIDATE[fn.__name__] = func_metadata(fn).validate_arguments
        return fn

    return decorator


#: Bu cagrinin hedef penceresi ve eylem oncesi durumu: bayat icerik uyarisi hangi
#: pencereye bakacagini buradan bilir. Sozluk nesnesi paylasiliyor: worker.call baglami
#: kopyalasa da UIA ipliginde yapilan yazim ayni sozluge gider.
_CALL_NOTES: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "pcgoz_cagri_notlari", default=None)


def _audited(name: str, call: Callable[[], Any], kwargs: dict[str, Any] | None = None,
             **fields: Any) -> Any:
    """Araci calistir, sonucunu ve suresini gunluge yaz; hedefin icerigi bayat olabilirse
    sonuca (hatada hata metnine) tek satir [!] uyarisi ekle. pc_batch adimlari da buradan
    gecer. kwargs: aracin argumanlari (canli testlerle ayni imza; gunluge yazilmaz)."""
    notes: dict[str, Any] = {}
    token = _CALL_NOTES.set(notes)
    started = time.perf_counter()
    try:
        result = call()
    except Exception as exc:
        extra = _stale_note(name, notes)
        safety.audit_outcome(name, started, exc, **fields, **_stale_field(notes))
        if extra and exc.args and isinstance(exc.args[0], str):
            exc.args = (f"{exc.args[0]}\n{extra}", *exc.args[1:])
        raise
    finally:
        _CALL_NOTES.reset(token)
    result = _with_extra(result, _stale_note(name, notes))
    safety.audit_outcome(name, started, None, **fields, **_stale_field(notes))
    return result


#: Sonucu ekranin/pencerenin o anki icerigine dayanan araclar; goruntu alanlar ayrica.
_STALE_CHECKED = frozenset({"pc_read", "pc_find", "pc_text", "pc_screenshot", "pc_ocr",
                            "pc_wait_for", "pc_invoke", "pc_do"})
_IMAGE_TOOLS = frozenset({"pc_screenshot", "pc_ocr"})
_ACTION_TOOLS = frozenset({"pc_invoke", "pc_do"})


def _stale_note(name: str, notes: dict[str, Any]) -> str | None:
    """Hedefin icerigi bayat olabilir mi? Neden sonuc kaydina "bayat" olarak girer.

    Tamamen ortulu Chromium penceresi sayfayi cizmiyor, UIA metnini de guncellemiyor:
    model ortuldugu andaki sayfayi okuyup buna gore tikliyordu (olcum: occlusion.py).
    Kontrol araci hicbir zaman bozmaz.
    """
    before = notes.pop("_bayat_once", None)        # _watch: eylemden onceki durum
    if name not in _STALE_CHECKED:
        return None
    if name in _ACTION_TOOLS and before == "taze":
        return None                                 # sonraki bayatlik eylemin kendi sonucu
    try:
        found = occlusion.stale_reason(notes.get("hwnd"), image=name in _IMAGE_TOOLS)
    except Exception:  # noqa: BLE001
        return None
    if found is None:
        return None
    notes["bayat"], text = found
    if name in _ACTION_TOOLS:
        text += (" Eylemin etkisi gorunmeyebilir: tekrarlama." if notes["bayat"] == "ust_hep_ustte"
                 else " Eylemin etkisi gorunmeyebilir: tekrarlamadan once pc_focus ile bak.")
    return text


def _stale_field(notes: dict[str, Any]) -> dict[str, Any]:
    return {"bayat": notes["bayat"]} if notes.get("bayat") else {}


def _stale_key(hwnd: int) -> str:
    """Eylem oncesi durum: bayatlik nedeni ya da "taze" (kontrol bozuksa da: uyari cikmaz)."""
    try:
        found = occlusion.stale_reason(hwnd, image=False)
    except Exception:  # noqa: BLE001
        return "taze"
    return found[0] if found else "taze"


def _content_stale(hwnd: int) -> bool:
    try:
        return occlusion.stale_reason(hwnd, image=False) is not None
    except Exception:  # noqa: BLE001
        return False


def _with_extra(result: Any, extra: str | None) -> Any:
    if not extra:
        return result
    if isinstance(result, str):
        return f"{result}\n{extra}"
    if isinstance(result, list) and result and isinstance(result[0], str):
        return [f"{result[0]}\n{extra}", *result[1:]]
    return result


def _note_target(hwnd: int) -> int:
    """Hedef pencereyi bu cagrinin notuna yaz: bayat icerik kontrolu ona bakar."""
    notes = _CALL_NOTES.get()
    if notes is not None and hwnd:
        notes["hwnd"] = hwnd
    return hwnd


# --------------------------------------------------------------------------
# Yardimcilar
# --------------------------------------------------------------------------

def _active_popup() -> windows.WindowInfo | None:
    """Ekranda acik bir menu varsa onu dondur.

    Menu acikken ekranin sahibi odur: klavye ve fare oraya gider, kullanicinin
    baktigi yer orasidir. Ust pencereyi okumak yaniltici olur.
    """
    popups = windows.popup_windows()
    return popups[0] if popups else None


def _resolve_hwnd(hwnd: int | None, query: str | None, *, prefer_popup: bool = False) -> int:
    """Hedef pencereyi belirle: acik hwnd > baslik aramasi > acik menu > on plandaki.

    Secilen pencere cagrinin notuna yazilir: bayat icerik kontrolu ona bakar."""
    return _note_target(_pick_hwnd(hwnd, query, prefer_popup))


def _pick_hwnd(hwnd: int | None, query: str | None, prefer_popup: bool) -> int:
    if hwnd:
        if not windows.is_window(hwnd):
            raise LookupError(f"hwnd {hwnd} artik yok. pc_windows ile guncel listeyi al.")
        return hwnd
    if query:
        matches = windows.find_window(query)
        if not matches:
            raise LookupError(
                f"{query!r} ile eslesen pencere yok. pc_windows ile acik pencereleri gor."
            )
        if len(matches) > 1:
            listing = "\n".join(f"  hwnd={w.hwnd} {w.process} -- {w.title}" for w in matches[:10])
            raise LookupError(
                f"{query!r} birden fazla pencereyle eslesti, hwnd ile net soyle:\n{listing}"
            )
        return matches[0].hwnd
    if prefer_popup:
        popup = _active_popup()
        if popup:
            return popup.hwnd
    fg = windows.foreground_window()
    if not fg:
        raise LookupError("On planda pencere yok. hwnd veya query ver.")
    return fg


def _snapshot(hwnd: int) -> uia.Node:
    registry.new_generation()      # bayat ref mesaji "kac okuma once" diyebilsin
    try:
        tree = uia.snapshot_window(hwnd, onscreen_only=config.ONSCREEN_ONLY)
    except Exception as exc:
        # E_FAIL ("Belirtilmemis hata") bir kez daha denenir: gunlukte find, screenshot,
        # read, wait_for ve text okumalarinda gorulen COMError'larin hepsi buydu.
        # Digerleri (UIA_E_TIMEOUT vb.) aynen gecer.
        if not uia.is_fail(exc):
            raise
        time.sleep(0.15)
        tree = uia.snapshot_window(hwnd, onscreen_only=config.ONSCREEN_ONLY)
    _last["hwnd"] = hwnd
    _last["tree"] = tree
    return tree


def _tree_for(hwnd: int) -> uia.Node:
    """Ayni pencere icin son agaci yeniden kullan; ref numaralari sabit kalsin."""
    if _last["hwnd"] == hwnd and _last["tree"] is not None:
        return _last["tree"]  # type: ignore[return-value]
    return _snapshot(hwnd)


def _boxes_for(tree: uia.Node, hwnd: int) -> tuple[list[uia.Node], int]:
    """Kutu cizilecek ogeler, GUNCEL konumlariyla. Doner: (ogeler, kaybolan sayisi).

    Agac son pc_read'ten kaliyor -- kutulardaki numaralar o ciktidaki ref'lerle
    ayni olsun diye. Ama arada tiklama ya da kaydirma olduysa eski
    dikdortgenler kutuyu yanlis yere ciziyordu; model de o kutuya bakip
    tiklama noktasi seciyor. Her ogenin konumu cizmeden once tazeleniyor
    (oge basina ~0.2 ms), kaybolan ogeye kutu cizilmiyor.
    """
    if not any(n.ref for n in tree.walk()):
        render.render(tree, hwnd)      # ref atamak icin; metni kullanmiyoruz
    nodes = [n for n in tree.walk() if n.ref and n.actionable]
    live = [n for n in nodes if uia.refresh_geometry(n)]
    return live, len(nodes) - len(live)


def _target_facts(target: uia.Node | None, hwnd: int) -> dict[str, Any]:
    """Guvenlik kontrolleri icin hedef: parola mi, hangi surec?

    Ref verilmediginde hedef "odakta ne varsa" oldugu icin, kontrol her zaman
    odaktaki oge uzerinden de yapiliyor.
    """
    node = target or uia.focused_node()
    info = windows.describe(hwnd) if windows.is_window(hwnd) else None
    return {
        "is_password": bool(node and node.password),
        "process_name": info.process if info else None,
        "label": node.label if node else "odaktaki oge",
    }


def _guard_typing(target: uia.Node | None, hwnd: int) -> None:
    """Parola alanina / kimlik bilgisi arayuzune yazmayi engelle."""
    safety.guard_text_target(**_target_facts(target, hwnd))


def _guard_keys(mods: list[int], vk: int, target: uia.Node | None, hwnd: int) -> None:
    """Parola alanina karakter ya da yapistirma tusu gondermeyi engelle."""
    safety.guard_key_target(mods, vk, **_target_facts(target, hwnd))


def _from_screenshot(x: int, y: int) -> tuple[int, int]:
    """Son ekran goruntusundeki pikseli ekran koordinatina cevir."""
    if not _last_shot:
        raise ValueError("space='screenshot' icin once pc_screenshot al.")
    width, height = _last_shot["size"]
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError(f"({x},{y}) son ekran goruntusunun ({width}x{height}) disinda.")
    (ox, oy), factor = _last_shot["origin"], _last_shot["factor"]
    return ox + round(x / factor), oy + round(y / factor)


def _point_for(ref: str | None, coordinate: list[int] | None,
               space: str = "screen") -> tuple[int, int, str]:
    """Eylemin hedef noktasini bul. Doner: (x, y, aciklama)."""
    if ref:
        node = registry.resolve(ref, for_input=True)
        x, y = node.center
        return x, y, f"{ref} = {node.label}"
    if coordinate:
        if len(coordinate) != 2:
            raise ValueError("coordinate [x, y] seklinde iki sayi olmali.")
        x, y = int(coordinate[0]), int(coordinate[1])
        if space == "screenshot":
            sx, sy = _from_screenshot(x, y)
            return sx, sy, f"resim ({x},{y}) = ekran ({sx},{sy})"
        return x, y, f"({x},{y})"
    raise ValueError("ref veya coordinate vermelisin.")


OBSERVE_HELP = (
    "Eylemden sonra ekranda ne degistigini ozetle (0.3-1.2 sn ekler). Art arda "
    "bircok eylemde ara adimlar icin false ver ya da pc_batch kullan."
)


def _join(*parts: str) -> str:
    return "\n\n".join(p for p in parts if p)


def _tail(value: str, limit: int = 60) -> str:
    value = one_line(value)
    return value if len(value) <= limit else "..." + value[-(limit - 3):]


def _hwnd_at(ref: str | None, x: int, y: int) -> int:
    """Eylemin gidecegi ust duzey pencere: ref'in sahibi ya da noktadaki pencere."""
    return (registry.owning_hwnd(ref) if ref else 0) or windows.top_level_at(x, y)


def _watch(target: int, act: Callable[[], str], observe: bool, *,
           skip: frozenset[tuple[int, ...]] = frozenset()) -> tuple[str, str, bool]:
    """Eylemi calistir; observe ise ekranda ne degistigini ozetle.

    Doner: (eylemin kendi sonucu, ozet, bir sey degisti mi). Gozlem
    istenmediyse ozet bos. Ozetteki yeni ogelere ref verilir -- model onlara
    ayrica pc_read yapmadan dokunabilsin.
    """
    if target:
        _note_target(target)            # bayat icerik kontrolu hedefi bilsin
        # Eylemden ONCE de bakilir: eylem hedefi kendisi kucultur, gizler ya da orterse
        # sonradan bayat gorunur ama eylem yapilmistir; uyari pc_focus'la geri aldirirdi.
        notes = _CALL_NOTES.get()
        if notes is not None:
            notes["_bayat_once"] = _stale_key(target)
    if not observe:
        return act(), "", True
    before = effects.capture(target)
    result = act()
    after, elapsed, settled = effects.settle(target)
    summary, changed = effects.describe(
        before, after, elapsed, settled, skip=skip,
        register=lambda node: registry.register(node, after.target),
    )
    return result, summary, changed


def _digest(text: str) -> str:
    """sha256'nin ilk 12 hanesi: ayni metnin tekrarlandigi metnin kendisi olmadan gorunur."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _type(ref: str | None, text: str | None, paste: bool, observe: bool) -> str:
    """pc_do type: metni yaz; nereye gittigini ve hedefin yeni degerini soyle.

    Gunlukte (31 Agustos) ayni metin uc kez yazildi: hedef sadece "odaktaki
    oge" diye biliniyordu ve metnin nereye gittigini goren yoktu. Artik hedef
    yazmadan once adiyla belirleniyor, degeri sonra geri okunuyor ve deger
    degismediyse bu soyleniyor.
    """
    if text is None:
        raise ValueError("action='type' icin text parametresi gerekli.")
    node = registry.resolve(ref, for_input=True) if ref else None
    hwnd = registry.owning_hwnd(ref) if ref else windows.foreground_window()
    _guard_typing(node, hwnd)
    target = node or uia.focused_node()
    # Uzun metinlerde tus tusa yazmak hem yavas hem riskli (ardisik
    # karakterlerin birlesmesi uzunlukla artiyor), otomatik panoya gec.
    use_paste = paste or len(text) > config.AUTO_PASTE_OVER
    auto = use_paste and not paste

    # Metnin kendisi gunluge yazilmaz, ozeti yazilir; ayni metnin tekrar yazildigi
    # ozetten yine anlasilir.
    safety.audit("pc_do", action="type", ref=ref,
                 target=target.label if target else "odakta oge yok",
                 chars=len(text), paste=use_paste, auto_paste=auto or None, text_sha=_digest(text))
    if config.DRY_RUN:
        return f"[DRY RUN] {len(text)} karakter yazilmadi."
    before_value = uia.live_value(target) if target else None

    def write() -> str:
        if node:
            _click(*node.center)
        if not use_paste:
            _type_text(text)
            return f"{len(text)} karakter yazildi."
        # Kullanicinin panosu (resim/dosya dahil) saklanip hedef metni
        # OKUDUKTAN sonra geri konuyor; bkz. clipboard.paste.
        # Sadece hedefin okumasi sayilsin: 26H2'de CrossDeviceService her pano
        # degisikligini ~200 ms'de okuyor; onu hedef sanmak eski panoyu yapistirtirdi.
        result = clipboard.paste(text, lambda: _press("ctrl+v"), readers=windows.input_pids())
        if result.read:
            note = (f"{len(text)} karakter pano uzerinden yapistirildi "
                    f"(hedef {result.read_ms} ms'de okudu).")
        else:
            note = (f"UYARI: {len(text)} karakter panoya kondu ve ctrl+v gonderildi ama "
                    "hedef uygulama metni 2 saniye icinde OKUMADI -- yapistirma olmamis "
                    "olabilir (odak metin alaninda degil mi?).")
        if auto:
            note += (f" (metin {config.AUTO_PASTE_OVER} karakterden uzun oldugu icin otomatik "
                     "olarak yapistirma secildi. Uygulamanin her tusa tek tek tepki vermesi "
                     "gerekiyorsa paste=False ile zorla.)")
        return note

    skip = frozenset({target.rid}) if target and target.rid else frozenset()
    result, summary, _ = _watch(hwnd, write, observe, skip=skip)
    if not observe:
        time.sleep(0.1)          # uygulama son tuslari islesin, sonra geri oku

    if target is None:
        report = ["Hedef: ODAKTA OGE YOKTU -- metin hicbir yere gitmemis olabilir."]
    else:
        report = [f"Hedef: {target.label}" + ("" if node else " (odaktaki oge)")]
        after_value = uia.live_value(target)
        if after_value is not None:
            report.append(f'Hedefin degeri simdi: "{_tail(after_value)}"')
            if after_value == before_value and text:
                report.append("UYARI: hedefin degeri DEGISMEDI -- metin baska bir yere gitmis "
                              "olabilir. Odagi kontrol et ya da ref vererek yaz.")
    return _join(result, "\n".join(report), summary)


# --------------------------------------------------------------------------
# Gorme
# --------------------------------------------------------------------------

@_tool(
    description=(
        "Acik pencereleri listeler: hwnd, surec, baslik, konum/boyut ve durum. "
        "Masaustunde ne oldugunu anlamak icin ilk adim. query verilirse baslik, "
        "surec adi ve pencere sinifi icinde arar."
    )
)
def pc_windows(
    query: Annotated[str | None, Field(description="Baslik/surec/sinif icinde aranacak metin")] = None,
) -> str:
    def run() -> str:
        found = windows.find_window(query) if query else windows.list_windows()
        if not found:
            return f"{query!r} ile eslesen pencere yok." if query else "Gorunur pencere yok."
        lines = []
        for w in found:
            state = []
            if w.foreground:
                state.append("ON PLANDA")
            if w.minimized:
                state.append("simge durumunda")
            if w.elevated:
                state.append("YONETICI (erisilemez)")
            if windows.is_hung(w.hwnd):
                state.append("YANIT VERMIYOR")
            suffix = f"  [{', '.join(state)}]" if state else ""
            lines.append(
                f"hwnd={w.hwnd}  {w.process or '?'}  ({w.rect[0]},{w.rect[1]} "
                f"{w.width}x{w.height}){suffix}\n    {w.title or '(basliksiz)'}"
            )
        popups = windows.popup_windows()
        if popups and not query:
            lines.append("")
            lines.append("ACIK MENU / ACILIR PENCERE  (ekranin sahibi su an bunlar):")
            for w in popups:
                lines.append(
                    f"  hwnd={w.hwnd}  {w.process or '?'}  {w.cls}  "
                    f"({w.rect[0]},{w.rect[1]} {w.width}x{w.height})"
                    + (f"  {w.title}" if w.title else "")
                )
            lines.append("  (pc_read bunlari hwnd vermeden de okur)")

        vx, vy, vw, vh = virtual_screen()
        header = f"Sanal ekran: ({vx},{vy}) {vw}x{vh} -- fiziksel piksel\n"
        screens = windows.monitors()
        if len(screens) > 1:
            # Coklu ekranda koordinatlar negatif olabilir, olcekler farkli olabilir.
            for index, screen in enumerate(screens, 1):
                left, top, right, bottom = screen.rect
                header += (f"  ekran {index}: ({left},{top} {right - left}x{bottom - top}) "
                           f"%{screen.scale}" + (" birincil" if screen.primary else "") + "\n")
        return header + "\n" + "\n".join(lines)

    return worker.call(run)


@_tool(
    description=(
        "Bir pencerenin erisilebilirlik agacini okur ve her ogeye ref_N verir. "
        "Masaustunu 'gormenin' asil yolu -- ekran goruntusunden farkli olarak "
        "ogelerin rolunu, metnini, durumunu ve tam koordinatini verir.\n\n"
        "filter='interactive' (varsayilan) sadece tiklanabilir/yazilabilir ogeleri "
        "ve gorunur metni gosterir; 'all' her seyi, 'text' sadece metni. "
        "Cikti buyukse ref_id ile bir alt agaca inerek daralt. Sadece EKRANDA gorunen "
        "ogeler gelir: asagidaki/ortulu kisim icin kaydir (pc_do scroll) ve tekrar oku."
    )
)
def pc_read(
    hwnd: Annotated[int | None, Field(description="Hedef pencere; bos ise on plandaki")] = None,
    query: Annotated[str | None, Field(description="hwnd yerine baslik/surec ile pencere bul")] = None,
    ref_id: Annotated[str | None, Field(description="Bu ref'in alt agacini oku (daraltmak icin)")] = None,
    filter: Annotated[Literal["interactive", "all", "text"], Field(description="Ayrinti duzeyi")] = "interactive",
    depth: Annotated[int | None, Field(description="Azami ic ice derinlik")] = None,
    max_chars: Annotated[int | None, Field(description="Azami cikti uzunlugu (varsayilan 20000)")] = None,
) -> str:
    def run() -> str:
        if ref_id:
            node = registry.resolve(ref_id)
            hwnd_owner = _note_target(registry.owning_hwnd(ref_id) or _resolve_hwnd(hwnd, query))
            tree = uia.snapshot(node.el, onscreen_only=config.ONSCREEN_ONLY)
            info = windows.describe(hwnd_owner) if windows.is_window(hwnd_owner) else None
            head = f"{ref_id} alt agaci ({node.label})"
        else:
            target = _resolve_hwnd(hwnd, query, prefer_popup=True)
            info = windows.describe(target)
            tree = _snapshot(target)
            hwnd_owner = target
            head = f"hwnd={target}  {info.process}  \"{info.title}\""
            if not hwnd and not query and windows.is_popup(target):
                head += "  <- acik menu/acilir pencere okundu, ust pencere degil"

        result = render.render(tree, hwnd_owner, mode=filter, depth=depth, max_chars=max_chars)
        note = ""
        if info and info.elevated:
            note = "\n[!] Bu pencere yonetici hakkiyla calisiyor -- girdi gonderemem.\n"
        if not ref_id and uia.looks_unpopulated(tree):
            # Bu uyarinin sebebi: bos agaci dolu sanmak, bos agactan daha kotu.
            # Sayfa icerigi gelmemisken "sayfada bir sey yok" demek yanlis cevap.
            content = uia.content_element_count(tree)
            note += (
                f"\n[!] Pencere okundu ama icerik alaninda sadece {content} oge var -- "
                "sayfa/uygulama icerigi BURADA OLMAYABILIR. Erisilebilirlik uyandirilip "
                "beklendi, yine de gelmediyse iki ihtimal var: (1) uygulama hic "
                "erisilebilirlik bilgisi vermiyor (oyun, Qt, canvas), (2) icerik hala "
                "yukleniyor. pc_screenshot ile GOZLE dogrula; gercekten bossa "
                "koordinatla calis, yukleniyorsa pc_wait_for ile bekle. "
                "Bu agaca bakip 'icerik yok' sonucuna VARMA.\n"
            )
        return f"{head}\n{note}\n{result.text}"

    return worker.call(run)


@_tool(
    description=(
        "Bir pencerede rol/metin/deger icinde arama yapar ve eslesen ogelere ref verir. "
        "Butun agaci dokmeden belirli bir seyi bulmanin yolu -- ne aradigin belliyse "
        "pc_read yerine bunu kullan."
    )
)
def pc_find(
    query: Annotated[str, Field(description="Aranacak metin (buyuk/kucuk harf duyarsiz)")],
    hwnd: Annotated[int | None, Field(description="Hedef pencere; bos ise on plandaki")] = None,
    window_query: Annotated[str | None, Field(description="hwnd yerine baslik ile pencere bul")] = None,
    limit: Annotated[int, Field(description="Azami sonuc sayisi")] = 20,
) -> str:
    def run() -> str:
        target = _resolve_hwnd(hwnd, window_query, prefer_popup=True)
        tree = _snapshot(target)
        needle = fold(query)
        matches = [
            node for node in tree.walk()
            if node.area > 0 and not node.offscreen
            and (needle in fold(node.text)
                 or needle in fold(node.value)    # adi olan kutunun icerigi: text onu gostermez
                 or needle in fold(node.role)
                 or needle in fold(node.aid))
        ]
        matches.sort(key=lambda n: (not n.actionable, n.area))
        body = render.render_matches(matches, target, limit=limit)
        return f"hwnd={target} icinde {query!r}: {len(matches)} eslesme\n\n{body}"

    return worker.call(run)


@_tool(
    description=(
        "Bir pencerenin ya da ogenin metnini duz yazi olarak dondurur. "
        "Ekranda ne yazdigini okumak icin -- agac yapisi gerekmiyorsa bundan daha ucuz."
    )
)
def pc_text(
    hwnd: Annotated[int | None, Field(description="Hedef pencere; bos ise on plandaki")] = None,
    query: Annotated[str | None, Field(description="hwnd yerine baslik ile pencere bul")] = None,
    ref_id: Annotated[str | None, Field(description="Sadece bu ogenin metni")] = None,
    max_chars: Annotated[int, Field(description="Azami uzunluk")] = 20000,
) -> str:
    def run() -> str:
        if ref_id:
            node = registry.resolve(ref_id)
            _note_target(registry.owning_hwnd(ref_id))      # bayat icerik kontrolu
            return patterns.collect_text(node, limit=max_chars) or "(metin yok)"
        target = _resolve_hwnd(hwnd, query, prefer_popup=True)
        tree = _snapshot(target)
        return patterns.collect_text(tree, limit=max_chars) or "(metin yok)"

    return worker.call(run)


def _crop_warning(target: int, how: str) -> tuple[str, bool]:
    """Pencere ekrandan kirpildiysa etiket uyarisi. Doner: (uyari, ustu ortulu mu).

    Kirpmada ustteki pencere gorunur; bunu soylemezsek model arkadaki pencereye
    baktigini sanip yanlis seyi okuyor.
    """
    if how == "kendi":
        return "", False
    why = ("Pencere yanit vermiyor (donmus ya da mesgul)" if how == "yanitsiz"
           else "Pencere kendi cizimini vermedi (PrintWindow bos)")
    cover = capture.covered_by(target)
    if not cover:
        return f"\n[!] {why}: ekrandan kirpildi, ustu acik.", False
    other = windows.describe(cover)
    return (f"\n[!] {why}: ekrandan kirpildi ve USTU ORTULU -- resimde hedef degil "
            f"hwnd={cover} {other.process} \"{other.title[:50]}\" gorunuyor. Once pc_focus, "
            "sonra tekrar cek."), True


@_tool(
    structured_output=False,
    description=(
        "Ekran goruntusu alir. hwnd verilirse sadece o pencere (arkada ya da ustu "
        "ortulu olsa bile), region verilirse o bolge, hicbiri verilmezse tum ekran.\n\n"
        "annotate=True (varsayilan) ogelerin uzerine numarali kutular cizer; bu "
        "numaralar ref_N'in N'idir, yani resimde gordugun 7 numarali kutu ile "
        "pc_read ciktisindaki ref_7 ayni ogedir. Yakinlastirmak icin region ver ve "
        "scale=2.0 kullan."
    )
)
def pc_screenshot(
    hwnd: Annotated[int | None, Field(description="Sadece bu pencereyi yakala")] = None,
    query: Annotated[str | None, Field(description="hwnd yerine baslik ile pencere bul")] = None,
    region: Annotated[list[int] | None, Field(description="[x, y, genislik, yukseklik] ekran bolgesi")] = None,
    annotate: Annotated[bool, Field(description="Ogelerin uzerine ref numarali kutu ciz")] = True,
    scale: Annotated[float | None, Field(
        description="Olcek (2.0 = yakinlastir). Modelin goruntu sinirinda kesilir. "
                    "Bos ise resim sadece siniri asiyorsa kucultulur."
    )] = None,
) -> list[Any]:
    def run() -> tuple[str, bytes]:
        boxes_wanted = annotate
        nodes: list[uia.Node] = []
        gone = 0
        if region:
            if len(region) != 4:
                raise ValueError("region [x, y, genislik, yukseklik] seklinde dort sayi olmali.")
            x, y, w, h = (int(v) for v in region)
            image = capture.capture_region(x, y, w, h)
            origin = (x, y)
            label = f"bolge ({x},{y} {w}x{h})"
            if boxes_wanted and _last["tree"] is not None:
                nodes, gone = _boxes_for(_last["tree"], _last["hwnd"])
        elif hwnd or query:
            target = _resolve_hwnd(hwnd, query)
            info = windows.describe(target)
            if info.minimized:
                raise ValueError(
                    f"hwnd {target} simge durumunda -- yakalanacak gorunur alani yok. "
                    "Once pc_focus ile geri getir."
                )
            image, origin, how = capture.capture_window(target)
            warning, covered = _crop_warning(target, how)
            label = f"hwnd={target} {info.process} \"{info.title}\"{warning}"
            if how == "yanitsiz" or covered:
                label += " Kutu cizilmedi."
                boxes_wanted = False
            if boxes_wanted:
                nodes, gone = _boxes_for(_tree_for(target), target)
        else:
            image, origin = capture.capture_screen()
            label = "tum ekran"
            if boxes_wanted:
                # Acik menu varsa onun ogeleri de numaralanmali: ekranin sahibi
                # o, ve tiklanacak sey buyuk ihtimalle onun icinde.
                targets = [w.hwnd for w in windows.popup_windows()]
                fg = windows.foreground_window()
                if fg and fg not in targets:
                    targets.append(fg)
                for target in targets:
                    live, lost = _boxes_for(_tree_for(target), target)
                    nodes += live
                    gone += lost

        boxes = capture.annotate(image, origin, nodes) if (boxes_wanted and nodes) else 0
        raw_size = (image.width, image.height)
        png, size, factor = capture.to_png(image, scale=scale)

        note = f"{label}\nyakalanan: {raw_size[0]}x{raw_size[1]} fiziksel piksel"
        # Guvenli masaustu, ekran kapali, ortulu Chromium: _audited ekler (occlusion.stale_reason).
        if abs(factor - 1.0) > 0.001:
            note += f" -> gonderilen: {size[0]}x{size[1]} (olcek {factor:.3f})"
            if scale is not None and factor < scale - 0.001:
                note += f" -- istenen {scale} modelin goruntu sinirinda kesildi"
        note += f"\nkaynak sol-ust: ({origin[0]},{origin[1]})"
        if boxes:
            note += f"\n{boxes} oge numaralandi -- kutudaki N sayisi ref_N demek."
        if gone:
            note += (f"\n{gone} oge son okumadan beri kayboldu (kutusu cizilmedi). Ekran "
                     "degismis; yeni ogeler icin pc_read ile tazele.")
        if boxes_wanted and not boxes:
            note += "\nEtiketlenecek oge bulunamadi (uygulama erisilebilirlik bilgisi vermiyor olabilir)."
        _last_shot.update(origin=origin, factor=factor, size=size, label=label)
        if abs(factor - 1.0) <= 0.001 and origin == (0, 0):
            note += "\nOlcek 1: resimdeki (x, y) dogrudan pc_do koordinatidir."
        else:
            note += ("\nResimdeki bir noktaya tiklamak icin pc_do'ya coordinate=[x, y] ile "
                     "space='screenshot' ver -- ekrana cevirmeyi arac yapar.")
        return note, png

    note, png = worker.call(run)
    return [note, Image(data=png, format="png")]


@_tool(
    description=(
        "Ekrandaki yaziyi OCR ile okur (Windows'un yerlesik OCR'i). Erisilebilirlik "
        "bilgisi vermeyen arayuzler icin: oyun, Qt, canvas, uzak masaustu, resim ya da PDF "
        "icindeki yazi. Her satir EKRAN koordinatiyla ve tiklama merkeziyle doner -- "
        "merkez pc_do coordinate'a dogrudan verilebilir. find verilirse sadece o metni "
        "iceren kelimeler (aksan ve buyuk/kucuk harf duyarsiz).\n\n"
        "UIA calisiyorsa once pc_read/pc_find: OCR hata yapabilir ('79/79' -> '79179'). "
        "Yazi otomatik 2x buyutulerek okunur. Sayi, tarih, yol gibi kritik metni "
        "pc_screenshot ile dogrula ('v0.4.0' -> 'vo.4.o', '\\' -> 'X' gibi hatalar kalir)."
    )
)
def pc_ocr(
    hwnd: Annotated[int | None, Field(description="Sadece bu pencereyi oku")] = None,
    query: Annotated[str | None, Field(description="hwnd yerine baslik ile pencere bul")] = None,
    region: Annotated[list[int] | None, Field(description="[x, y, genislik, yukseklik] ekran bolgesi")] = None,
    find: Annotated[str | None, Field(
        description="Sadece bu metni iceren yerleri dondur -- tiklanacak yazinin yerini bulmak icin"
    )] = None,
    max_lines: Annotated[int, Field(description="Azami satir")] = 150,
) -> str:
    def grab() -> tuple[Any, tuple[int, int], str]:
        if region:
            if len(region) != 4:
                raise ValueError("region [x, y, genislik, yukseklik] seklinde dort sayi olmali.")
            x, y, w, h = (int(v) for v in region)
            return capture.capture_region(x, y, w, h), (x, y), f"bolge ({x},{y} {w}x{h})"
        if hwnd or query:
            target = _resolve_hwnd(hwnd, query)
            info = windows.describe(target)
            if info.minimized:
                raise ValueError(f"hwnd {target} simge durumunda. Once pc_window restore ile geri getir.")
            image, origin, how = capture.capture_window(target)
            warning, _ = _crop_warning(target, how)
            return image, origin, f"hwnd={target} {info.process} \"{info.title[:50]}\"{warning}"
        image, origin = capture.capture_screen()
        return image, origin, "tum ekran"

    image, (ox, oy), label = worker.call(grab)
    started = time.perf_counter()
    lines, lang, scale = ocr.recognize(image)
    head = (f"{label} -- OCR ({lang}, {scale:g}x buyutme), {len(lines)} satir, "
            f"{time.perf_counter() - started:.2f} sn")

    def fmt(text: str, rect: tuple[int, int, int, int]) -> str:
        left, top, right, bottom = rect[0] + ox, rect[1] + oy, rect[2] + ox, rect[3] + oy
        return (f'"{one_line(text)}" ({left},{top} {right - left}x{bottom - top}) '
                f"merkez=({(left + right) // 2},{(top + bottom) // 2})")

    if find:
        hits = ocr.find(lines, find)
        if not hits:
            return (f"{head}\n{find!r} bulunamadi. OCR hata yapabilir: region ile daraltip "
                    "tekrar dene ya da pc_screenshot ile gozle bak.")
        body = "\n".join(fmt(text, rect) for text, rect in hits[:max_lines])
        return f"{head}\n{find!r}: {len(hits)} eslesme\n{body}"

    body = "\n".join(fmt(line.text, line.rect) for line in lines[:max_lines])
    more = f"\n... {len(lines) - max_lines} satir daha" if len(lines) > max_lines else ""
    return f"{head}\n\n{body or '(yazi bulunamadi)'}{more}"


# --------------------------------------------------------------------------
# Eylem
# --------------------------------------------------------------------------

@_tool(
    description=(
        "UIA deseniyle eylem yapar -- fareyi hic oynatmadan. Tiklamaya gore daha "
        "guvenilir: odagi calmaz, pencere ortuluyken de calisir, yanlis ogeye "
        "gitme ihtimali yok. Tiklanabilir bir ogeye eylem yapacaksan once bunu dene.\n\n"
        "invoke: butona bas | toggle: onay kutusu degistir | select: liste ogesi sec | "
        "expand/collapse: ac/kapa | value: metin kutusuna deger yaz (value parametresi) | "
        "scroll_into_view: gorunur alana kaydir\n\n"
        "Sonuc ekranda ne degistigini de soyler; 'Gorunur degisiklik yok' dediyse "
        "uygulama deseni yok saymis olabilir, pc_do ile tikla."
    )
)
def pc_invoke(
    ref: Annotated[str, Field(description="Hedef oge (pc_read/pc_find'dan)")],
    action: Annotated[
        Literal["invoke", "toggle", "select", "expand", "collapse", "value", "scroll_into_view"],
        Field(description="Uygulanacak desen"),
    ],
    value: Annotated[str | None, Field(description="action='value' icin yazilacak metin")] = None,
    force: Annotated[bool, Field(
        description="Eski parametre, etkisiz: arac kullanici mesgulken de calisir (girdisi kilitlenir)."
    )] = False,
    observe: Annotated[bool, Field(description=OBSERVE_HELP)] = True,
) -> str:
    def run() -> str:
        safety.check_not_frozen()
        safety.check_input_desktop()
        safety.check_user_not_busy(force)
        node = registry.resolve(ref)
        hwnd = registry.owning_hwnd(ref)
        if action == "value":
            _guard_typing(node, hwnd)
        safety.audit("pc_invoke", ref=ref, action=action, target=node.label,
                     rect=list(node.rect), chars=len(value) if value else None)
        if config.DRY_RUN:
            return f"[DRY RUN] {node.label} uzerinde '{action}' yapilmadi."
        result, summary, changed = _watch(hwnd, lambda: patterns.apply(node, action, value), observe)
        # Bayat Chromium penceresinde "degisiklik yok" eylemin etkisizligi demek degil:
        # tiklama onerisi eylemi ikiletirdi.
        if observe and action == "invoke" and not changed and not _content_stale(hwnd):
            # Olculen durum (31 Agustos): Invoke basarili dondu, hicbir sey
            # olmadi, ayni butona 4 sn sonra fiziksel olarak tiklandi.
            summary += ("\nInvoke basarili dondu ama hicbir sey degismedi -- bazi uygulamalar "
                        f"Invoke'u yok sayar. pc_do left_click ref={ref} ile dene.")
        return _join(result, summary)

    return _acting(run) + safety.hotkey_warning()


@_tool(
    description=(
        "Fare ve klavye eylemleri. Hedef olarak ref (tercih edilen) ya da "
        "coordinate [x, y] ver.\n\n"
        "left_click / right_click / double_click / middle_click / hover: tiklama ve uzerine gelme\n"
        "type: metin yaz (klavye duzeninden bagimsiz -- Turkce karakterler dogru cikar). "
        "paste=True uzun metinler icin pano uzerinden yapistirir, cok daha hizli.\n"
        "key: tus ya da kombinasyon -- 'enter', 'esc', 'f5', 'ctrl+s', 'alt+F4', 'ctrl+shift+p'\n"
        "scroll: direction (up/down/left/right) ve amount ile kaydir\n"
        "drag: ref/coordinate'dan to_ref/to_coordinate'a surukle\n"
        "wait: duration saniye bekle\n\n"
        "Her eylemden sonra ekranda ne degistigi ozetlenir: on plan, acilan/kapanan "
        "pencere ve menuler, odak, pencerede yeni/kaybolan/degisen ogeler (ref'leriyle). "
        "Cogu zaman ayrica pc_read gerekmez. type ayrica metnin hangi ogeye gittigini "
        "ve ogenin yeni degerini soyler."
    )
)
def pc_do(
    action: Annotated[
        Literal["left_click", "right_click", "double_click", "middle_click", "hover",
                "type", "key", "scroll", "drag", "wait"],
        Field(description="Yapilacak eylem"),
    ],
    ref: Annotated[str | None, Field(description="Hedef oge (koordinattan daha guvenilir)")] = None,
    coordinate: Annotated[list[int] | None, Field(description="[x, y] fiziksel ekran pikseli")] = None,
    text: Annotated[str | None, Field(description="action='type' icin yazilacak metin")] = None,
    key: Annotated[str | None, Field(description="action='key' icin tus, orn. 'ctrl+s'")] = None,
    paste: Annotated[bool, Field(description="type: pano uzerinden yapistir (uzun metinde cok hizli)")] = False,
    direction: Annotated[Literal["up", "down", "left", "right"], Field(description="scroll yonu")] = "down",
    amount: Annotated[int, Field(description="scroll miktari (tekerlek tiki)")] = 3,
    repeat: Annotated[int, Field(description="key: tekrar sayisi")] = 1,
    to_ref: Annotated[str | None, Field(description="drag hedefi (oge)")] = None,
    to_coordinate: Annotated[list[int] | None, Field(description="drag hedefi [x, y]")] = None,
    space: Annotated[Literal["screen", "screenshot"], Field(
        description="coordinate/to_coordinate hangi uzayda: 'screen' fiziksel ekran pikseli, "
                    "'screenshot' son pc_screenshot resmindeki piksel (arac ekrana cevirir)"
    )] = "screen",
    duration: Annotated[float, Field(description="wait: saniye")] = 1.0,
    force: Annotated[bool, Field(
        description="Eski parametre, etkisiz: arac kullanici mesgulken de calisir (girdisi kilitlenir)."
    )] = False,
    observe: Annotated[bool, Field(description=OBSERVE_HELP)] = True,
) -> str:
    def run() -> str:
        safety.check_not_frozen()
        if action != "wait":
            safety.check_input_desktop()
            safety.check_user_not_busy(force)

        if action == "wait":
            time.sleep(max(0.0, min(30.0, duration)))
            safety.audit("pc_do", action=action, duration=duration)
            return f"{duration} saniye beklendi."

        if action == "type":
            return _type(ref, text, paste, observe)

        if action == "key":
            if not key:
                raise ValueError("action='key' icin key parametresi gerekli.")
            try:
                mods, vk = parse_combo(key)
            except KeyParseError as exc:
                raise ValueError(str(exc)) from exc
            node = registry.resolve(ref, for_input=True) if ref else None
            hwnd = registry.owning_hwnd(ref) if ref else windows.foreground_window()
            _guard_keys(mods, vk, node, hwnd)
            safety.audit("pc_do", action="key", key=key, repeat=repeat, ref=ref)
            if config.DRY_RUN:
                return f"[DRY RUN] {key!r} gonderilmedi."

            def press() -> str:
                if node:
                    _click(*node.center)
                _press(key, repeat=repeat)
                return f"{key!r} gonderildi" + (f" x{repeat}" if repeat > 1 else "") + "."

            return _join(*_watch(hwnd, press, observe)[:2])

        if action == "scroll":
            if ref or coordinate:
                x, y, where = _point_for(ref, coordinate, space)
            else:
                x, y = cursor_pos()
                where = f"imlecin bulundugu yer ({x},{y})"
            safety.audit("pc_do", action="scroll", direction=direction, amount=amount, target=where)
            if config.DRY_RUN:
                return f"[DRY RUN] {where} kaydirilmadi."

            def wheel() -> str:
                _scroll(x, y, direction, amount)
                return f"{where} noktasinda {direction} yonunde {amount} tik kaydirildi."

            return _join(*_watch(_hwnd_at(ref, x, y), wheel, observe)[:2])

        if action == "drag":
            x1, y1, from_where = _point_for(ref, coordinate, space)
            x2, y2, to_where = _point_for(to_ref, to_coordinate, space)
            safety.audit("pc_do", action="drag", start=[x1, y1], end=[x2, y2],
                         from_target=from_where, to_target=to_where)
            if config.DRY_RUN:
                return f"[DRY RUN] {from_where} -> {to_where} suruklenmedi."

            def pull() -> str:
                _drag(x1, y1, x2, y2)
                return f"{from_where} -> {to_where} suruklendi."

            return _join(*_watch(_hwnd_at(ref, x1, y1), pull, observe)[:2])

        # Tiklama ve uzerine gelme
        x, y, where = _point_for(ref, coordinate, space)
        button = {"left_click": "left", "right_click": "right",
                  "double_click": "left", "middle_click": "middle"}.get(action, "left")
        count = 2 if action == "double_click" else 1
        safety.audit("pc_do", action=action, target=where, point=[x, y])
        if config.DRY_RUN:
            return f"[DRY RUN] {where} uzerinde {action} yapilmadi."

        def pointer() -> str:
            if action == "hover":
                from .input import move
                move(x, y)
                under = uia.element_from_point(x, y)
                extra = f" Altindaki oge: {under.label}" if under else ""
                return f"Imlec {where} uzerine getirildi.{extra}"
            _click(x, y, button=button, count=count)
            return f"{where} uzerinde {action} yapildi."

        return _join(*_watch(_hwnd_at(ref, x, y), pointer, observe)[:2])

    if action == "wait":
        return worker.call(run)
    return _acting(run) + safety.hotkey_warning()


@_tool(
    description=(
        "Bir ogenin belirmesini ya da kaybolmasini bekler. Sabit sure beklemek "
        "yerine bunu kullan -- yukleme ekranlari, acilan pencereler ve yavas "
        "uygulamalarda dogru zamani yakalamanin tek guvenilir yolu."
    )
)
def pc_wait_for(
    query: Annotated[str, Field(description="Beklenen ogenin metni/rolu")],
    hwnd: Annotated[int | None, Field(description="Hedef pencere; bos ise on plandaki")] = None,
    window_query: Annotated[str | None, Field(description="hwnd yerine baslik ile pencere bul")] = None,
    until: Annotated[Literal["appear", "disappear"], Field(description="Belirmesini mi kaybolmasini mi")] = "appear",
    timeout: Annotated[float, Field(description="Azami bekleme (saniye)")] = 10.0,
) -> str:
    needle = fold(query)
    limit = max(0.5, min(config.WAIT_FOR_MAX_S, timeout))

    def poll() -> str | None:
        """Tek yoklama (UIA ipliginde). Doner: bekleme bittiyse sonuc govdesi, yoksa None.

        Agac _snapshot ile degil dogrudan okunur: dongu iscinin disinda, yoklamalar
        baska cagrilarla ic ice geciyor; _last'i (pc_screenshot kutulari ondan
        cizilir) ve ref kusagini ezmesin.
        """
        try:
            target = _resolve_hwnd(hwnd, window_query)
            tree = uia.snapshot_window(target, onscreen_only=config.ONSCREEN_ONLY)
            found = [
                n for n in tree.walk()
                if n.area > 0 and not n.offscreen
                and (needle in fold(n.text) or needle in fold(n.value))
            ]
        except LookupError:
            found = []
            target = 0
        except Exception as exc:
            if not uia.is_fail(exc):
                raise
            return None        # gecici E_FAIL: bu tur sonuc sayilmaz ("kayboldu" da), yoklama surer
        if until == "appear" and found:
            return render.render_matches(found, target, limit=5)
        if until == "disappear" and not found:
            return ""
        return None

    # Dongu UIA ipliginin DISINDA, her yoklama ayri ve kisa bir worker.call.
    # Eskiden butun dongu tek cagriydi: worker.CALL_TIMEOUT_S'yi gecen bekleme
    # ipligi "takili" gosteriyor, arkasindaki cagrilari da dusuruyordu (gunlukteki
    # zaman asimlarinin cogu bu zincirdendi). Aradaki uykuda baska araclar da calisir.
    deadline = time.monotonic() + limit
    polls = 0
    while time.monotonic() < deadline:
        safety.check_not_frozen()
        polls += 1
        body = worker.call(poll)
        if body is not None:
            if until == "appear":
                return f"{query!r} belirdi ({polls} yoklama sonrasi):\n\n{body}"
            return f"{query!r} kayboldu ({polls} yoklama sonrasi)."
        time.sleep(0.35)
    state = "belirmedi" if until == "appear" else "hala duruyor"
    capped = f" (istenen {timeout:g} sn, ust sinir {limit:g} sn)" if limit < timeout else ""
    return (
        f"ZAMAN ASIMI: {query!r} {limit:g} saniyede {state} ({polls} yoklama){capped}. "
        "pc_read ile ekranda ne oldugune bak."
    )


@_tool(
    description=(
        "Panoyu okur ya da yazar. Bir metin kutusunun icerigini birebir almanin "
        "en guvenilir yolu (Ctrl+A, Ctrl+C, sonra pc_clipboard get)."
    )
)
def pc_clipboard(
    action: Annotated[Literal["get", "set"], Field(description="Oku ya da yaz")] = "get",
    text: Annotated[str | None, Field(description="action='set' icin panoya konacak metin")] = None,
) -> str:
    def run() -> str:
        if action == "get":
            content = clipboard.get_text()
            safety.audit("pc_clipboard", action="get", chars=len(content))
            return content or "(pano bos ya da metin icermiyor)"
        if text is None:
            raise ValueError("action='set' icin text parametresi gerekli.")
        safety.check_not_frozen()
        safety.audit("pc_clipboard", action="set", chars=len(text))
        if config.DRY_RUN:
            return f"[DRY RUN] {len(text)} karakter panoya yazilmadi."
        clipboard.set_text(text)
        return f"{len(text)} karakter panoya yazildi."

    return worker.call(run)


@_tool(
    description=(
        "Bir pencereyi on plana alir. Tiklamadan once genelde gerekli -- arka "
        "plandaki pencereye gonderilen klavye girdisi on plandaki pencereye gider. "
        "Windows odak kilidi engellerse baslik cubugunun dogrulanmis bir surukleme "
        "noktasina tiklayarak one alir."
    )
)
def pc_focus(
    hwnd: Annotated[int | None, Field(description="One alinacak pencere")] = None,
    query: Annotated[str | None, Field(description="hwnd yerine baslik/surec ile bul")] = None,
    force: Annotated[bool, Field(
        description="Eski parametre, etkisiz: arac kullanici mesgulken de calisir (girdisi kilitlenir)."
    )] = False,
) -> str:
    def run() -> str:
        safety.check_not_frozen()
        safety.check_input_desktop()
        safety.check_user_not_busy(force)      # odak calmak da girdi kadar bolucu
        target = _resolve_hwnd(hwnd, query)
        info = windows.describe(target)
        if info.elevated:
            raise PermissionError(
                f"hwnd {target} ({info.process}) yonetici hakkiyla calisiyor. "
                "Bu oturum yukseltilmemis, ona girdi gonderemem."
            )
        safety.audit("pc_focus", hwnd=target, title=info.title, process=info.process)
        if config.DRY_RUN:
            return f"[DRY RUN] {info.title!r} one alinmadi."
        _focus_and_wake(target)
        time.sleep(0.2)
        now = windows.foreground_window()
        note = _front_note(target, now)
        if note is not None:
            return f"hwnd={target} \"{info.title}\" -- on planda{note}"
        # Windows'un odak kilidi: on plandaki surec biz degilsek (or. kullanici
        # gorev cubuguna tiklamissa) SetForegroundWindow reddediliyor. Olculdu:
        # ipliklere baglanma, Alt hilesi, SwitchToThisWindow -- hicbiri asmiyor.
        # Tiklama ise asiyor (tests/tus_probu.py). Eskiden bu is modele
        # birakiliyordu; baslik cubugunun HTCAPTION dogrulanmis bir noktasina
        # tiklamak pencereyi one almaktan baska bir sey yapmaz.
        point = windows.caption_point(target)
        if point:
            safety.audit("pc_focus", hwnd=target, detail="baslik cubugu", point=list(point))
            home = cursor_pos()
            _click(*point)
            _move(*home, steps=1)               # kullanicinin imleci yerine donsun
            time.sleep(0.2)
            now = windows.foreground_window()
            note = _front_note(target, now)
            if note is not None:
                return (f"hwnd={target} \"{info.title}\" -- on planda (Windows odak kilidi "
                        f"vardi; baslik cubuguna {point} tiklanarak one alindi){note}")
        # Baslik ortulu ya da tiklama tutmadi: kucult + geri getir. Simge durumundan
        # geri gelen pencere odak kilidine takilmadan one geliyor (olculdu). Girdi
        # gondermez.
        # show beklemiyor (ShowWindowAsync), focus da yalniz o an simge durumundaki
        # pencereyi geri getiriyor: kuculmeyi gec isleyen pencere sonradan kuculup
        # kucuk kalabiliyordu. Yanit vermeyen pencere hic kucultulmez, focus kuculme
        # gorulunce cagrilir, cikista simge durumundaysa geri getirilir.
        responsive = windows.responds(target)
        if responsive:
            safety.check_not_frozen()      # acil durdurma ilk denemelerden sonra basilmis olabilir
            safety.audit("pc_focus", hwnd=target, detail="kucult + geri getir")
            visible = windows.is_visible(target)
            try:
                windows.show(target, "minimize")
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline and not windows.is_minimized(target):
                    time.sleep(0.03)
                if windows.is_minimized(target):
                    windows.focus(target)
                    time.sleep(0.2)
            finally:
                # Kuculme gec islenirse diye gorulmediyse kisa bir sure sonra bir kez
                # daha bakilir. Tepsiye saklanan uygulama kuculunce pencereyi gizler:
                # once gorunurduyse gizli de kalmasin. Yalniz gerekirse: SW_RESTORE
                # buyutulmus pencereyi normal boyuta indirir.
                if not windows.is_minimized(target):
                    time.sleep(0.3)
                if windows.is_minimized(target) or (visible and not windows.is_visible(target)):
                    windows.show(target, "restore")
            now = windows.foreground_window()
            note = _front_note(target, now)
            if note is not None:
                return (f"hwnd={target} \"{info.title}\" -- on planda (Windows odak kilidi "
                        f"vardi; pencere kucultulup geri getirilerek one alindi){note}")
        holder = windows.describe(now) if now else None
        where = f"hwnd={now} {holder.process} {holder.cls}" if holder else "(yok)"
        rest = ("kucultup geri getirme de tutmadi" if responsive else
                "pencere yanit vermedigi icin kucultup geri getirme denenmedi")
        tried = (f"baslik cubuguna tiklama da tutmadi, {rest}" if point else
                 f"baslik cubugunda guvenli tiklama noktasi yok (ortulu ya da ozel cizim), {rest}")
        return (
            f"UYARI: hwnd={target} \"{info.title}\" one ALINAMADI -- Windows odak degisimini "
            f"engelledi, {tried}; on planda hala {where} var. Klavye girdisi (key/type) ORAYA "
            "gider. Hedef pencerede tiklanabilir bir yere pc_do left_click yap (tiklama odagi "
            "tasir) ya da kullanicidan pencereyi one almasini iste."
        )

    return _acting(run)


def _focus_and_wake(target: int) -> None:
    """One al; Chromium ise ortulu pencere hesabini hemen yenilet.

    Zaten on plandaki pencere ortunun ustune ciksa da odak olayi yok: Chromium
    ortunun kalktigini fark etmeyip >=2,5 sn bayat kaliyordu. Ekranin tazelenmesi
    ~30-60 ms, cagiranin 0,2 sn'lik beklemesi icinde.
    """
    windows.focus(target)
    if occlusion.is_chromium(target):
        occlusion.wake()


def _front_note(target: int, now: int) -> str | None:
    """On planda hedef var mi? None: yok. Metin: var, sonuca eklenecek not.

    Hedefin kalici iletisim kutusu acikken on plan o kutudur, kok sahibi hedeftir;
    bu basarisizlik sayilirsa basliga tiklanip pencere kucultulup geri getirilir.
    Klavye girdisi kutuya gider; model bunu bilmeli.
    """
    if now == target:
        return ""
    if not now or windows.root_owner(now) != target:
        return None
    return (f"\nOnunde kendi iletisim kutusu acik: hwnd={now} \"{windows.describe(now).title[:60]}\" "
            "-- klavye girdisi oraya gider.")


@_tool(
    description=(
        "Pencere yonetimi: close / minimize / maximize / restore / move.\n\n"
        "close pencerenin X dugmesine basmak gibidir (WM_CLOSE): kaydedilmemis is varsa "
        "uygulama kendi sorusunu sorar, sureci oldurmez -- sonucta acilan soru "
        "penceresi gorunur. move: rect=[x, y, genislik, yukseklik] fiziksel piksel, "
        "pencerenin gorunur cercevesine gore. Gorev cubugu ve masaustu yonetilemez."
    )
)
def pc_window(
    action: Annotated[Literal["close", "minimize", "maximize", "restore", "move"],
                      Field(description="Yapilacak islem")],
    hwnd: Annotated[int | None, Field(description="Hedef pencere; bos ise on plandaki")] = None,
    query: Annotated[str | None, Field(description="hwnd yerine baslik/surec ile bul")] = None,
    rect: Annotated[list[int] | None, Field(description="move icin [x, y, genislik, yukseklik]")] = None,
    force: Annotated[bool, Field(
        description="Eski parametre, etkisiz: arac kullanici mesgulken de calisir (girdisi kilitlenir)."
    )] = False,
    observe: Annotated[bool, Field(description=OBSERVE_HELP)] = True,
) -> str:
    def run() -> str:
        safety.check_not_frozen()
        safety.check_input_desktop()
        safety.check_user_not_busy(force)
        target = _resolve_hwnd(hwnd, query)
        info = windows.describe(target)
        if info.cls in windows.SHELL_CLASSES:
            raise PermissionError(
                f"hwnd {target} Windows kabugu ({info.cls}) -- yonetmiyorum. Gorev cubuguna "
                "ya da masaustune WM_CLOSE 'Windows'u kapat' iletisim kutusunu acar."
            )
        if info.elevated:
            raise PermissionError(
                f"hwnd {target} ({info.process}) yonetici hakkiyla calisiyor; bu oturum "
                "yukseltilmemis oldugu icin Windows (UIPI) mesajlarimi engeller."
            )
        if action == "move" and (not rect or len(rect) != 4):
            raise ValueError("move icin rect=[x, y, genislik, yukseklik] gerekli.")
        safety.audit("pc_window", action=action, hwnd=target, title=info.title,
                     process=info.process, rect=rect)
        name = f"hwnd={target} \"{info.title[:50]}\""
        if config.DRY_RUN:
            return f"[DRY RUN] {name} uzerinde '{action}' yapilmadi."

        def act() -> str:
            if action == "close":
                windows.post_close(target)
                return f"{name} icin kapatma istegi gonderildi (WM_CLOSE)."
            if action == "move":
                windows.move_frame(target, *(int(v) for v in rect))
                return f"{name} tasindi."
            windows.show(target, action)
            return f"{name}: {action}."

        result, summary, _ = _watch(target, act, observe)
        if action == "move" and windows.is_window(target):
            left, top, right, bottom = windows.frame_rect(target)
            result += f" Gorunur cerceve simdi: ({left},{top} {right - left}x{bottom - top})."
        return _join(result, summary)

    return _acting(run) + safety.hotkey_warning()


# --------------------------------------------------------------------------
# Toplu calistirma
# --------------------------------------------------------------------------

def _acting(run: Callable[[], str]) -> str:
    """Eylem araci: reddedilecekse once reddet, sonra girdiyi kilitleyip calistir.

    0.6.0: kilit eylem + "ne degisti" izlemesi boyunca tutuluyor, kullanici o an
    fareyi kullaniyor olsa da (eskiden UserBusy ile reddediliyordu). Sol ustte
    uyari cikar (overlay.py). Dondurulmus ya da guvenli masaustu: kilit hic
    alinmaz -- reddedilecek bir is icin kullanicinin girdisi kesilmesin.
    """
    safety.check_not_frozen()
    safety.check_input_desktop()
    with inputguard.hold():
        return worker.call(run)


#: Girdi kilidi gerektiren toplu is adimlari (pc_do wait haric).
_ACTING = frozenset({"pc_do", "pc_invoke", "pc_window", "pc_focus"})


def _acts(step: Any) -> bool:
    if not isinstance(step, dict) or step.get("name") not in _ACTING:
        return False
    return not (step.get("name") == "pc_do" and (step.get("input") or {}).get("action") == "wait")


#: Bu araclarin adimlarinda "ne degisti" ozeti varsayilan olarak sadece son
#: eylem adiminda alinir; ara adimlarda her biri 0.3-1.2 sn eklerdi.
_OBSERVING = frozenset({"pc_do", "pc_invoke", "pc_window"})


@_tool(
    structured_output=False,
    description=(
        "Birden fazla araci TEK cagrida sirayla calistirir, ilk hatada durur. Iki ya da "
        "daha fazla adimi onceden bilebiliyorsan kullan -- her adim icin ayri tur "
        "beklemek yerine. Her adim {\"name\": arac adi, \"input\": o aracin kendi "
        "parametreleri}. Ornek: [{\"name\": \"pc_do\", \"input\": {\"action\": "
        "\"left_click\", \"ref\": \"ref_7\"}}, {\"name\": \"pc_do\", \"input\": "
        "{\"action\": \"type\", \"text\": \"merhaba\"}}, {\"name\": \"pc_do\", "
        "\"input\": {\"action\": \"key\", \"key\": \"enter\"}}].\n\n"
        "pc_do/pc_invoke/pc_window adimlarinda 'ne degisti' ozeti sadece SON eylem adiminda "
        "alinir; bir ara adim icin input'a observe=true koy. Kullanici klavyeye "
        "dokunursa ya da acil durdurmaya basarsa o adimda durur."
    ),
)
def pc_batch(
    actions: Annotated[list[dict[str, Any]], Field(
        description='Sirayla calisacak adimlar: [{"name": "pc_do", "input": {...}}, ...]'
    )],
) -> list[Any]:
    if not actions:
        raise ValueError("actions bos -- en az bir adim ver.")
    names = sorted(n for n in _RAW if n != "pc_batch")
    last_act = max((i for i, step in enumerate(actions)
                    if isinstance(step, dict) and step.get("name") in _OBSERVING), default=-1)

    # Girdi adimi varsa kullanicinin fiziksel girdisi BUTUN toplu is boyunca
    # tutulur: adim aralarinda fareye dokunmak isi yarida kesmesin ya da bir
    # tiklamayi kaydirmasin. Baslamadan once kullanici yaziyorsa araya girme.
    inputs = any(_acts(step) for step in actions)
    if inputs:
        safety.check_not_frozen()
        safety.check_input_desktop()
        safety.check_user_not_busy(any((step.get("input") or {}).get("force")
                                       for step in actions if isinstance(step, dict)))
    with inputguard.hold(config.BLOCK_BATCH_MAX_S) if inputs else contextlib.nullcontext():
        lock_until = time.monotonic() + config.BLOCK_BATCH_MAX_S if inputs else None
        return _run_steps(actions, names, last_act, lock_until)


def _clip_wait(given: dict[str, Any], lock_until: float, key: str = "timeout",
               default: float = 10.0) -> tuple[dict[str, Any], str]:
    """Kilit tutan toplu isteki bekleme adimini kalan kilit suresine kirp.

    key: sureyi veren arguman, default: verilmezse aracin bekledigi sure.
    Kilit BLOCK_BATCH_MAX_S sonra girdiyi birakiyor ama uyari, kanca ve oturumlar
    arasi mutex toplu is bitene dek kaliyor: 600 sn'lik bekleme baska oturumlari
    OtherSession'a dusurur, kullanici da 10 dk "kilit" uyarisi gorurdu. Yalniz
    pc_wait_for kirpiliyordu; pc_do wait de (her biri en cok 30 sn) birkac adimda
    siniri asabiliyordu.
    """
    value = given.get(key)
    try:
        wanted = float(default if value is None else value)
    except (TypeError, ValueError):
        return given, ""                     # dogrulama hatasi kendi yolundan gelsin
    remain = round(max(0.5, lock_until - time.monotonic()), 1)
    if wanted <= remain:
        return given, ""
    return ({**given, key: remain},
            f"\n(girdi kilidi tutan toplu iste bekleme {wanted:g} sn yerine {remain:g} sn'ye "
            "kirpildi; uzun beklemeyi toplu isin disinda ayri cagir)")


def _run_steps(actions: list[dict[str, Any]], names: list[str], last_act: int,
               lock_until: float | None = None) -> list[Any]:
    batch_id = safety.CALL_ID.get()
    out: list[Any] = []
    for index, step in enumerate(actions):
        name = step.get("name") if isinstance(step, dict) else None
        head = f"[{index + 1}/{len(actions)}] {name}"
        clipped = ""
        try:
            if name not in _RAW or name == "pc_batch":
                raise ValueError(f"bilinmeyen arac {name!r}. Secenekler: {', '.join(names)}")
            given = step.get("input") or {}
            if not isinstance(given, dict):
                raise ValueError("input bir nesne olmali: {\"action\": ..., ...}")
            if name in _OBSERVING and "observe" not in given:
                given = {**given, "observe": index == last_act}
            if name == "pc_wait_for" and lock_until is not None:
                given, clipped = _clip_wait(given, lock_until)
            elif name == "pc_do" and given.get("action") == "wait" and lock_until is not None:
                given, clipped = _clip_wait(given, lock_until, "duration", 1.0)
            # Her adim kendi kimligini alir; gunlukte hangi topluya ait oldugu gorunur.
            # Tek cagriyla ayni yol: bayat icerik uyarisi adimin sonucuna eklenir.
            token = safety.CALL_ID.set(safety.next_call_id())
            try:
                result = _audited(name, lambda: _RAW[name](**_VALIDATE[name](given)), given,
                                  batch=batch_id)
            finally:
                safety.CALL_ID.reset(token)
        except Exception as exc:  # noqa: BLE001 -- adim hatasi rapora girer, toplu is durur
            out.append(f"{head} HATA -- {type(exc).__name__}: {exc}")
            rest = len(actions) - index - 1
            if rest:
                out.append(f"Kalan {rest} adim calistirilmadi.")
            break
        if isinstance(result, list):          # pc_screenshot: [not, resim]
            out.append(f"{head}: {result[0]}")
            out.extend(result[1:])
        else:
            out.append(f"{head}: {result}{clipped}")
    return _merge_text(out)


def _merge_text(parts: list[Any]) -> list[Any]:
    """Ardisik metinleri tek blokta birlestir.

    Her dizge ayri bir metin blogu olarak gidiyor ve Claude Code bloklari
    arada ayrac olmadan yapistiriyor: "...secim kalkti[2/2] pc_window" diye
    okunuyordu. Resimler kendi bloklarinda kalir.
    """
    merged: list[Any] = []
    for part in parts:
        if isinstance(part, str) and merged and isinstance(merged[-1], str):
            merged[-1] += "\n\n" + part
        else:
            merged.append(part)
    return merged
