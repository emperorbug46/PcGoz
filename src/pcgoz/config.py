"""Ayarlar ve ortak sabitler."""

from __future__ import annotations

import os
from pathlib import Path


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "evet"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


#: Girdi ureten araclar log'lar ama hicbir sey yapmaz.
DRY_RUN = _env_flag("PCGOZ_DRY_RUN", False)

#: UIA tip kutuphanesi. comtypes sarmalayicisi bunun mtime'ina kilitli (bkz. uia.py).
UIA_TYPELIB = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "UIAutomationCore.dll")

#: Denetim gunlugu. AppData'da DEGIL: Claude masaustu uygulamasi bir MSIX paketi
#: ve Windows paketin sureclerinin AppData'ya yazdiklarini sessizce
#: %LOCALAPPDATA%\Packages\Claude_...\LocalCache\Local'a yonlendiriyor. Paketin
#: icindeki sunucu gunlugu orada tutuyordu; paket disindan calisan --stats ve
#: --doctor gercek AppData'ya bakip "kayit yok" diyordu (2026-09-24). Kullanici
#: klasoru sanallastirilmiyor (~/.claude da orada).
DEFAULT_LOG_DIR = Path.home() / ".pcgoz"
LOG_DIR = Path(os.environ.get("PCGOZ_LOG_DIR") or DEFAULT_LOG_DIR)
LOG_PATH = LOG_DIR / "actions.jsonl"
LOG_MAX_BYTES = 5 * 1024 * 1024

#: pc_wait_for'un azami beklemesi. Dongu UIA ipliginin disinda oldugu icin
#: worker.CALL_TIMEOUT_S'ye bagli degil; dosya yuklemesi gibi isler 600 sn istedi.
WAIT_FOR_MAX_S = 600.0


def legacy_log_dirs() -> list[Path]:
    """0.4.0'a kadarki gunluk yerleri: %LOCALAPPDATA%\\PcGoz ve paketlerin sanal kopyalari."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    root = Path(local)
    try:
        virtual = sorted((root / "Packages").glob("*/LocalCache/Local/PcGoz"))
    except OSError:
        virtual = []
    return [path for path in (root / "PcGoz", *virtual) if path.is_dir() and path != LOG_DIR]

#: Acil dondurma kisayolu: Ctrl+Alt+Shift+Esc (ac/kapa).
HOTKEY_ENABLED = _env_flag("PCGOZ_HOTKEY", True)

#: pc_read varsayilanlari -- token butcesini bunlar korur.
DEFAULT_MAX_CHARS = _env_int("PCGOZ_MAX_CHARS", 20000)
DEFAULT_DEPTH = _env_int("PCGOZ_DEPTH", 60)
MAX_ELEMENTS = _env_int("PCGOZ_MAX_ELEMENTS", 4000)

#: Okuma araclari (pc_read/pc_find/pc_text/pc_wait_for) sadece ekrandaki ogeleri
#: ister; suzgeci UIA'nin kendisi uygular. Ekran disi ogeler zaten hic
#: gosterilmiyordu (render/pc_find sonradan atiyordu), ama once cekiliyordu:
#: 1500 satirlik sayfada 9055 oge. Iki sonucu vardi (2026-09-24, olculdu):
#: soguk pc_find 4.7 sn, ve MAX_ELEMENTS belge sirasiyla kestigi icin sona
#: kaydirilmis sayfada gorunen satirlar HIC gelmiyordu (pc_read sadece tarayici
#: cercevesini gosteriyordu). IsOffscreen'i yanlis bildiren bir uygulama icin 0.
ONSCREEN_ONLY = _env_flag("PCGOZ_ONSCREEN_ONLY", True)

#: Agac yuruyusunun sert derinlik siniri (dongusel/patolojik agaclara karsi emniyet).
DEPTH_HARD_LIMIT = 80

#: Ayni gorunen kardesler bu sayidan sonra katlanir.
SIBLING_COLLAPSE_AFTER = 8

#: Ekran goruntusu bu sinirlari asarsa kucultulur, asmazsa OLDUGU GIBI gider.
#:
#: Sinirlar modelin kendi goruntu sinirlari: bunlari asan resmi API bir kez
#: daha kucultur ve modelin okudugu koordinatlar bizim hesapladigimiz olcekle
#: tutmaz -- tiklamalar kayar. Sinirin icinde kalan resimde ise guncel
#: modellerin koordinatlari piksellerle 1:1 esleniyor; 1920x1080 ekran
#: kucultulmeden gider ve resimdeki konum dogrudan ekran koordinatidir.
#:
#: Varsayilan: yuksek cozunurluk kusagi (Opus 4.7+, Opus 5/5.5, Sonnet 5,
#: Fable) -- uzun kenar 2576 px, 3.75 MP. Eski modellerle (Haiku 4.5,
#: Sonnet 4.6 ve oncesi) PCGOZ_MAX_EDGE=1568 ve PCGOZ_MAX_PIXELS=1150000 ver.
#: Eskiden sabit 1568 idi: guncel modelde gereksiz kucultme + carpan hesabi,
#: eski modelde de 1568x882 = 1.38 MP ile sinirin ustu.
SCREENSHOT_MAX_EDGE = _env_int("PCGOZ_MAX_EDGE", 2576)
SCREENSHOT_MAX_PIXELS = _env_int("PCGOZ_MAX_PIXELS", 3_750_000)

#: Yazarken tus basina gecikme araligi (ms).
#:
#: Bu deger keyfi degil, olculdu. 6-18 ms'de Windows 11 Not Defteri'nde ardisik
#: karakterler birlesip son harfe cokuyordu: "ABCDEFGHIJ" -> "DDDDEFGHIJ",
#: "123" -> "333". Sebep, uygulamanin mesaj kuyrugunu bosaltmasina firsat
#: kalmadan KEYEVENTF_UNICODE olaylarinin ust uste binmesi. 20 ms'den itibaren
#: 3/3 temiz cikti; guvenlik payiyla 25-40 alindi. Dusurmeden once
#: tests/smoke.py'deki pano round-trip testini calistir.
TYPE_DELAY_MS = (25, 40)

#: Bu uzunlugu asan metinler otomatik olarak pano uzerinden yapistirilir.
#: 200 karakter tus tusa ~6 saniye surer; ayrica birlesme riski uzunlukla artar.
AUTO_PASTE_OVER = _env_int("PCGOZ_AUTO_PASTE_OVER", 200)

#: Kullanicinin son girdisinden sonra bu sure gecmeden girdi gonderilmez --
#: SADECE REFUSE_WHEN_BUSY aciksa. Varsayilan kapali (0.6.0, kullanicinin
#: istegi): kullanici fareyi/klavyeyi kullaniyor olsa da eylem reddedilmez,
#: girdisi kilitle kesilir ve sol ustte uyari cikar (overlay.py).
USER_BUSY_MS = _env_int("PCGOZ_USER_BUSY_MS", 1500)
REFUSE_WHEN_BUSY = _env_flag("PCGOZ_REFUSE_WHEN_BUSY", False)

#: Kilit alinirken kullanici bir degistirici tusu ya da fare dugmesini basili
#: tutuyorsa bu kadar beklenir (birakma olaylari kilitten gecer). Dolarsa
#: sentetik birakma gonderilir. Bkz. inputguard._settle_held.
HELD_WAIT_S = 1.5

#: Baska bir PcGoz oturumu (baska Claude oturumunun sunucusu) eylemdeyse
#: ortak girdi kilidi icin azami bekleme.
SESSION_WAIT_S = 20.0

#: Girdi kilitliyken sol ustte yari saydam uyari.
OVERLAY = _env_flag("PCGOZ_OVERLAY", True)

#: Girdi eylemi suresince kullanicinin FIZIKSEL fare/klavyesi tutulur
#: (inputguard). Acil durdurma kisayolu yine calisir. Kapatmak icin 0.
BLOCK_USER_INPUT = _env_flag("PCGOZ_BLOCK_INPUT", True)
#: Kilidin azami suresi: tek eylem / pc_batch. Sure dolunca kilit kendiliginden kalkar.
BLOCK_MAX_S = 30.0
BLOCK_BATCH_MAX_S = 60.0

#: Tiklamadan once fareyi tasima adimlari (hover/tooltip/surukleme dogru calissin).
MOUSE_STEPS = 8
MOUSE_MOVE_MS = 60

#: Bu sureclere yazma her kosulda reddedilir (kimlik bilgisi arayuzleri).
CREDENTIAL_PROCESSES = frozenset({
    "consent.exe",
    "credentialuibroker.exe",
    "logonui.exe",
    "lsaiso.exe",
    "winlogon.exe",
})
