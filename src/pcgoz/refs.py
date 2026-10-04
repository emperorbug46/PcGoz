"""ref_N kayit defteri.

`pc_read` / `pc_find` her ogeye bir `ref_N` verir; sonraki cagrilar bu ref'le
hedef gosterir. Kritik nokta cozumleme sirasi:

    canli COM isaretcisi  ->  RuntimeId ile yeniden bul  ->  TEMIZ HATA

Son adim bilerek "hata" -- eski koordinata dusmek yok. Pencere kapandiysa ya da
oge kayboldusa, o koordinatta simdi baska bir sey vardir ve oraya tiklamak
kullanicinin hesaba katmadigi bir eylem olur. Bu yuzden bayat ref bir guvenlik
sinirlamasi, kolaylik meselesi degil.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import uia, windows

#: Defter bu kadar kayittan sonra en eskiden budanir.
_MAX_ENTRIES = 6000


class StaleRef(LookupError):
    """Ref artik canli bir ogeye cozulmuyor."""


@dataclass
class RefEntry:
    node: uia.Node
    runtime_id: tuple[int, ...] | None
    hwnd: int
    generation: int


class Registry:
    def __init__(self) -> None:
        self._entries: dict[str, RefEntry] = {}
        #: (hwnd, RuntimeId) -> ref: ayni oge tekrar okununca ayni numara.
        self._by_rid: dict[tuple[int, tuple[int, ...]], str] = {}
        self._counter = 0
        self._generation = 0

    # -- kayit ---------------------------------------------------------------

    def new_generation(self) -> int:
        """Yeni bir okuma turu basliyor.

        Eski ref'ler silinmiyor -- hala cozulebiliyorlarsa gecerliler.
        Kusak numarasi sadece hata mesajlarinda "ne kadar eski" demek icin.
        """
        self._generation += 1
        return self._generation

    def register(self, node: uia.Node, hwnd: int) -> str:
        """Ogeye ref ver. Daha once gorulmus bir ogeyse AYNI ref'i.

        Eskiden her okuma ayni ogeye yeni numara veriyordu: numaralar bir
        gunde ref_136'ya cikiyordu ve bir onceki pc_read ciktisindaki ref_7
        ile yeni ekran goruntusundeki kutu farkli numaralar tasiyordu. Kimlik
        (RuntimeId) ayni ve rol ayniysa ayni oge sayiliyor; rol farkliysa
        kimlik yeniden kullanilmis demektir, yeni numara verilir.
        """
        key = (hwnd, node.rid) if node.rid else None
        known = self._by_rid.get(key) if key else None
        entry = self._entries.get(known) if known else None
        if entry is not None and entry.node.role == node.role:
            # Sona tasi: budama en eskiden basliyor, yeni gorulen korunmali.
            del self._entries[known]
            entry.node, entry.generation = node, self._generation
            self._entries[known] = entry
            node.ref = known
            return known

        self._counter += 1
        ref = f"ref_{self._counter}"
        node.ref = ref
        self._entries[ref] = RefEntry(node, node.rid, hwnd, self._generation)
        if key:
            self._by_rid[key] = ref
        self._prune()
        return ref

    def _prune(self) -> None:
        excess = len(self._entries) - _MAX_ENTRIES
        if excess > 0:
            for ref in list(self._entries)[:excess]:
                entry = self._entries.pop(ref)
                key = (entry.hwnd, entry.runtime_id) if entry.runtime_id else None
                if key and self._by_rid.get(key) == ref:
                    del self._by_rid[key]

    # -- cozumleme -----------------------------------------------------------

    def resolve(self, ref: str, *, for_input: bool = False) -> uia.Node:
        """Ref'i canli bir ogeye cevir ve dikdortgenini tazele.

        `for_input=True` ise ayrica "bu noktaya tiklarsam gercekten bu pencereye
        mi vurmus olurum" kontrolu yapilir. Okuma icin bu kontrol yapilmiyor:
        arkadaki bir pencereyi okumak mesru, ustune tiklamak degil.
        """
        ref = ref.strip()
        entry = self._entries.get(ref)
        if entry is None:
            known = self._counter
            raise StaleRef(
                f"{ref} bu oturumda hic verilmedi (simdiye kadar ref_1..ref_{known}). "
                "Once pc_read veya pc_find calistir."
            )

        # Sahibi pencere gitmisse hicbir sey denemeye gerek yok.
        # UIA olu bir surecin ogesi icin de eski dikdortgeni dondurebiliyor --
        # yani "isaretci calisiyor" testi tek basina yeterli degil.
        if entry.hwnd and not windows.is_window(entry.hwnd):
            raise StaleRef(
                f"{ref} ({entry.node.label}) penceresi kapanmis (hwnd {entry.hwnd} artik yok). "
                "Eski koordinata dokunmuyorum: orada simdi baska bir uygulama olabilir. "
                "pc_windows ile duruma bak, sonra pc_read ile yeni ref al."
            )

        # Donmus pencerede asagidaki her cagri 2 sn zaman asimina kadar bekler
        # ve sonunda "oge yok" gibi gorunur -- oysa oge yerinde, pencere cevap
        # vermiyor. Once yokla, dogru sebebi soyle.
        if entry.hwnd and not windows.responds(entry.hwnd):
            raise uia.not_responding(entry.hwnd)

        fresh = self._refresh(entry.node)
        if fresh is not None:
            if for_input:
                self._assert_point_reachable(ref, fresh, entry)
            return fresh

        recovered = self._recover(entry)
        if recovered is not None:
            if for_input:
                self._assert_point_reachable(ref, recovered, entry)
            return recovered

        age = self._generation - entry.generation
        raise StaleRef(
            f"{ref} ({entry.node.label}) artik ekranda yok -- pencere degismis "
            f"veya oge yeniden olusturulmus olabilir "
            f"({age} okuma turu once alinmisti). pc_read ile tazele ve yeni ref kullan. "
            "Eski koordinata tiklamiyorum: orada simdi baska bir sey olabilir."
        )

    def _assert_point_reachable(self, ref: str, node: uia.Node, entry: RefEntry) -> None:
        """Tiklama noktasinda gercekten hedef uygulama mi var?

        Ogenin dikdortgeni guncel olsa bile uzerine baska bir pencere gelmis
        olabilir -- o zaman tiklama hedefe degil, ustteki pencereye gider.
        Karsilastirma surec duzeyinde: acilan menu ve liste kutulari ayri birer
        ust duzey pencere olsa da ayni uygulamanindir, onlari engellememeliyiz.
        """
        x, y = node.center
        top = windows.top_level_at(x, y)
        if not top:
            raise StaleRef(
                f"{ref} ({node.label}) icin ({x},{y}) noktasinda pencere yok. "
                "pc_read ile tazele."
            )
        if top == entry.hwnd:
            return

        expected = windows.window_pid(entry.hwnd) if entry.hwnd else node.pid
        actual = windows.window_pid(top)
        if expected and actual and expected == actual:
            return

        info = windows.describe(top)
        raise StaleRef(
            f"{ref} ({node.label}) tiklanamaz: ({x},{y}) noktasinda simdi baska bir "
            f"uygulama var -- hwnd={top} {info.process} \"{info.title[:40]}\". "
            "Hedefin ustu ortulmus. Once pc_focus ile dogru pencereyi one al, "
            "sonra pc_read ile tazele."
        )

    def _refresh(self, node: uia.Node) -> uia.Node | None:
        """Canli isaretci hala calisiyor mu? Calisiyorsa guncel dikdortgeni al.

        Cache'lenmis dikdortgen yaniltici olabilir -- liste kaydirildiysa oge
        durur ama yeri degisir. O yuzden Current* okunuyor, Cached* degil.
        """
        try:
            offscreen = bool(node.el.CurrentIsOffscreen)
        except Exception:  # noqa: BLE001 -- oge yok olmus
            return None

        if offscreen:
            # Kaydirilip gorunmez olmus olabilir. Insanin yapacagi seyi yap:
            # once gorunur alana getir, sonra tekrar bak.
            if "ScrollItem" in node.patterns:
                from . import patterns as _patterns
                try:
                    _patterns.apply(node, "scroll_into_view")
                except Exception:  # noqa: BLE001
                    return None
                try:
                    if node.el.CurrentIsOffscreen:
                        return None
                except Exception:  # noqa: BLE001
                    return None
            else:
                return None

        try:
            raw = node.el.CurrentBoundingRectangle
        except Exception:  # noqa: BLE001
            return None
        node.rect = (int(raw.left), int(raw.top), int(raw.right), int(raw.bottom))
        if node.area <= 0:
            return None
        return node

    def _recover(self, entry: RefEntry) -> uia.Node | None:
        """Isaretci oldu ama oge yeniden olusmus olabilir: RuntimeId ile ara."""
        if entry.runtime_id is None or not entry.hwnd:
            return None
        if not windows.is_window(entry.hwnd):
            return None
        try:
            tree = uia.snapshot_window(entry.hwnd)
        except uia.NotResponding:
            raise
        except Exception:  # noqa: BLE001
            return None
        for candidate in tree.walk():
            if candidate.rid == entry.runtime_id:
                candidate.ref = entry.node.ref
                entry.node = candidate
                return self._refresh(candidate)
        return None

    # -- yardimcilar ---------------------------------------------------------

    def get_entry(self, ref: str) -> RefEntry | None:
        return self._entries.get(ref.strip())

    def owning_hwnd(self, ref: str) -> int:
        entry = self._entries.get(ref.strip())
        return entry.hwnd if entry else 0


#: Tek defter -- worker ipliginde yasadigi icin kilit gerekmiyor.
registry = Registry()
