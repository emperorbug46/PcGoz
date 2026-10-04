"""PcGoz giris noktasi.

DPI farkindaligi bu dosyanin ILK isi olmak zorunda -- comtypes, PIL ya da
herhangi bir pencere/COM nesnesi olusmadan once. Sonraya kalirsa Windows
sureci "DPI-unaware" olarak muhurluyor ve butun koordinatlar sessizce
olcek oraninda kayiyor. Bu makinede olculdu: %125 olcekte ekran 1920x1080
yerine 1536x864 gorunuyor, yani her tiklama %25 yanlis yere gidiyordu.
"""

from __future__ import annotations

import ctypes

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
_DPI_SET_ERROR: str | None = None

try:
    if not ctypes.windll.user32.SetProcessDpiAwarenessContext(
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    ):
        err = ctypes.get_last_error()
        # ERROR_ACCESS_DENIED (5) = zaten ayarlanmis; manifest'ten gelmis olabilir.
        if err not in (0, 5):
            _DPI_SET_ERROR = f"SetProcessDpiAwarenessContext basarisiz (WinError {err})"
except (AttributeError, OSError) as exc:      # Windows 10 1703 oncesi
    _DPI_SET_ERROR = f"DPI API yok: {exc}"

import asyncio  # noqa: E402
import sys  # noqa: E402
import traceback  # noqa: E402
from typing import Any  # noqa: E402

from . import config, safety  # noqa: E402

AWARENESS_NAMES = {0: "UNAWARE (BOZUK)", 1: "SYSTEM (kismen bozuk)", 2: "PER-MONITOR (dogru)"}


def dpi_awareness() -> tuple[int, str]:
    try:
        ctx = ctypes.windll.user32.GetThreadDpiAwarenessContext()
        value = ctypes.windll.user32.GetAwarenessFromDpiAwarenessContext(ctx)
    except (AttributeError, OSError):
        return (-1, "bilinmiyor")
    return (value, AWARENESS_NAMES.get(value, f"? ({value})"))


def _check_server(problems: list[str]) -> Any:
    """Sunucu modulu import ediliyor mu? Claude Code'un yaptigi ilk sey bu.

    Burada yakalanmazsa --doctor da ayni satirda traceback'le cokup hicbir sey
    soylemiyordu (2026-09-23, bayat comtypes onbellegi).
    """
    try:
        from . import fallback, server
    except Exception as exc:  # noqa: BLE001 -- teshis araci cokmemeli
        print(f"Sunucu modulu      : HATA -- {type(exc).__name__}: {exc}")
        print("    " + traceback.format_exc().rstrip().replace("\n", "\n    "))
        problems.append(
            f"Sunucu import edilemiyor: {type(exc).__name__}: {exc}\n"
            "      Claude'da araclar yedek modda acilir ve sadece bu hatayi soyler.\n"
            f'      Onarim: "{sys.executable}" -m pcgoz --repair'
        )
        return None

    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    print(f"Sunucu modulu      : yuklendi, {len(names)} arac")
    expected = set(fallback.TOOL_NAMES)
    if names != expected:
        problems.append(
            "fallback.TOOL_NAMES server.py ile uyusmuyor -- yedek modda su araclar "
            f"eksik/fazla olur: {', '.join(sorted(names ^ expected))}"
        )
    return server


def _runtime_checks() -> list[str]:
    """Aracin sessizce yanlis calismasina yol acan her seyi tek tek kontrol et."""
    from .input import virtual_screen

    problems: list[str] = []

    if _DPI_SET_ERROR:
        problems.append(_DPI_SET_ERROR)
    level, name = dpi_awareness()
    print(f"DPI farkindaligi   : {name}")
    if level != 2:
        problems.append(
            f"DPI farkindaligi {name} -- koordinatlar olcek orani kadar kayacak. "
            "SetProcessDpiAwarenessContext cagrisi gec kalmis olabilir."
        )

    vx, vy, vw, vh = virtual_screen()
    dpi = ctypes.windll.user32.GetDpiForSystem()
    print(f"Sanal ekran        : ({vx},{vy}) {vw}x{vh}")
    print(f"Sistem DPI         : {dpi} (%{round(dpi / 96 * 100)} olcek)")

    if _check_server(problems) is not None:
        from . import uia, windows, worker

        try:
            root = worker.call(lambda: uia.iuia().GetRootElement().CurrentName)
            print(f"UIA kok ogesi      : {root!r}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"UIA calismiyor: {exc}")
            print(f"UIA kok ogesi      : HATA -- {exc}")

        try:
            count = len(worker.call(windows.list_windows))
            print(f"Gorunur pencere    : {count}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Pencere listesi alinamadi: {exc}")

    safety.start_hotkey()
    status = safety.hotkey_status()
    if status["registered"]:
        # Eskiden hangi kisayol alinirsa alinsin sabit "Ctrl+Alt+Shift+Esc" yaziyordu.
        taken = f"  ({status['note']} -- muhtemelen calisan bir PcGoz sunucusu)" if status.get("note") else ""
        print(f"Acil durdurma      : {status['combo']} kayitli{taken}")
    else:
        print(f"Acil durdurma      : KAYITSIZ -- {status['error']}")
        problems.append(f"Acil durdurma kisayolu kaydedilemedi: {status['error']}")
    frozen = safety._frozen
    print(f"Ortak dondurma     : {frozen.name if frozen.shared else 'YOK -- sadece bu surec'}"
          + ("  [SU AN DONDURULMUS]" if frozen.is_set() else ""))
    if not frozen.shared:
        problems.append("Ortak dondurma nesnesi acilamadi: acil durdurma baska PcGoz "
                        "sureclerini durdurmaz.")

    elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    print(f"Yonetici           : {'evet' if elevated else 'hayir'}"
          + ("" if elevated else "  (yonetici pencerelerine erisilemez)"))
    print(f"Denetim gunlugu    : {config.LOG_PATH}"
          + ("" if config.LOG_PATH.exists() else "  (henuz kayit yok)"))
    left = config.legacy_log_dirs()
    if left and config.LOG_DIR == config.DEFAULT_LOG_DIR:
        print(f"Eski gunluk yeri   : {len(left)} klasor tasinamadi -- {', '.join(map(str, left))}")
    print(f"Dry run            : {'ACIK -- girdi gonderilmeyecek' if config.DRY_RUN else 'kapali'}")
    return problems


def _report(title: str, problems: list[str]) -> int:
    print("=" * 58)
    if problems:
        print(f"{len(problems)} SORUN:")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("Her sey yolunda.")
    return 0


def selftest() -> int:
    """Sadece calisabilirlik kontrolu (kayda bakmaz)."""
    print("PcGoz self-test")
    print("=" * 58)
    return _report("self-test", _runtime_checks())


def check_registration() -> list[str]:
    """MCP kaydi hala bu repoyu gosteriyor mu?

    En sinsi ariza bu: klasor tasinir ya da venv silinir, sunucu hic baslamaz,
    araclar Claude'un elinde YOKMUS gibi olur ve hata da gorunmez. Sessiz
    kaybolmayi yakalamanin tek yolu kaydi dosya sistemiyle karsilastirmak.
    """
    import json
    import os
    from pathlib import Path

    problems: list[str] = []
    cfg = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude.json"
    print(f"MCP kayit dosyasi  : {cfg}")
    if not cfg.exists():
        problems.append(f"{cfg} yok -- MCP kaydi okunamiyor.")
        return problems

    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"{cfg} okunamadi/bozuk: {exc}")
        return problems

    entry = (data.get("mcpServers") or {}).get("pcgoz")
    scope = "kullanici (tum projeler)"
    if entry is None:
        for name, proj in (data.get("projects") or {}).items():
            found = (proj.get("mcpServers") or {}).get("pcgoz")
            if found:
                entry, scope = found, f"sadece proje: {name}"
                break

    if entry is None:
        problems.append(
            "pcgoz hicbir kapsamda kayitli degil. Kayit icin:\n"
            "      claude mcp add pcgoz --scope user -- "
            f"{Path(sys.executable)} -m pcgoz"
        )
        return problems

    print(f"Kayit kapsami      : {scope}")
    command = entry.get("command", "")
    print(f"Kayitli komut      : {command}")

    if not command:
        problems.append("Kayitta 'command' bos.")
    elif not Path(command).exists():
        problems.append(
            f"Kayitli komut DISKTE YOK: {command}\n"
            "      Klasor tasinmis ya da venv silinmis olabilir. Sunucu hic "
            "baslamaz ve araclar sessizce kaybolur.\n"
            f"      Duzeltmek icin kaydi su yola guncelle: {sys.executable}"
        )
    elif Path(command).resolve() != Path(sys.executable).resolve():
        problems.append(
            f"Kayit baska bir Python'u gosteriyor:\n"
            f"      kayitli : {Path(command).resolve()}\n"
            f"      calisan : {Path(sys.executable).resolve()}\n"
            "      Iki kurulum karisiyor olabilir."
        )
    if scope.startswith("sadece proje"):
        problems.append(
            "Kayit proje kapsaminda -- baska projelerde acilmaz. "
            "Her yerde acik olmasi icin kullanici kapsamina tasi."
        )
    return problems


def _check_plugin_version() -> list[str]:
    """Surum tek yerde (__init__.py), ama plugin.json JSON -- oradan okunamiyor.
    Elle tutuluyor; unutulursa burada gorunsun."""
    import json
    from pathlib import Path

    from . import __version__

    manifest = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
    if not manifest.exists():
        return []
    declared = json.loads(manifest.read_text(encoding="utf-8")).get("version")
    if declared != __version__:
        return [f"plugin.json surumu {declared}, paket {__version__} -- plugin.json'i guncelle."]
    return []


SKILL_NAME = "masaustu-kullanimi"


def _check_skill() -> list[str]:
    """Kullanim becerisi Claude'a ulasiyor mu?

    Beceri repoda duruyordu ama hicbir yere kurulmamisti (2026-09-24): ne
    ~/.claude/skills'te ne eklenti olarak. Yonlendirme ve tuzak notlari hicbir
    oturuma ulasmiyor, sadece sunucunun kisa tanitim metni gorunuyordu.
    Kurulum: ~/.claude/skills/<ad> -> repodaki skills/<ad> (junction).
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "skills" / SKILL_NAME
    installed = Path.home() / ".claude" / "skills" / SKILL_NAME
    if not (installed / "SKILL.md").exists():
        print(f"Kullanim becerisi  : KURULU DEGIL ({installed})")
        return [
            f"Beceri kurulu degil: yonlendirme hicbir oturuma ulasmiyor. Kur: New-Item "
            f"-ItemType Junction -Path '{installed}' -Target '{source}'"
        ]
    same = installed.resolve() == source.resolve()
    print(f"Kullanim becerisi  : {installed}"
          + ("  (repoya bagli)" if same else "  (KOPYA -- repodaki surumden ayrisabilir)"))
    return []


def doctor() -> int:
    """Tam teshis: calisabilirlik + kayit tutarliligi."""
    print("PcGoz doctor")
    print("=" * 58)
    from pathlib import Path

    print(f"Paket              : pcgoz {__import__('pcgoz').__version__}")
    print(f"Calisan Python     : {sys.executable}")
    print(f"Kaynak dizini      : {Path(__file__).resolve().parent}")

    problems = check_registration()
    problems += _check_plugin_version()
    problems += _check_skill()
    print("-" * 58)
    problems += _runtime_checks()

    print("=" * 58)
    if problems:
        print(f"{len(problems)} SORUN:")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("Her sey yolunda.")
    return 0


def repair() -> int:
    """UIA tip onbellegini silip yeniden uret, sonra tam teshis.

    Bayat onbellek icin normalde gerekmiyor -- uia.py onu GetModule ile
    kendisi yeniliyor. Bu, onbellek baska bir sekilde bozulduysa (yarim
    yazilmis dosya, elle kurcalama) temiz bir baslangic icin.
    """
    from pathlib import Path

    import comtypes.client
    from comtypes import typeinfo
    from comtypes.tools import codegenerator

    print("PcGoz repair")
    print("=" * 58)
    try:
        tlib = typeinfo.LoadTypeLibEx(config.UIA_TYPELIB)
        gen_dir = comtypes.client.gen_dir
        modules = (codegenerator.name_wrapper_module(tlib),
                   codegenerator.name_friendly_module(tlib))
        for module in modules:
            if not (gen_dir and module):
                continue
            path = Path(gen_dir) / f"{module.rsplit('.', 1)[-1]}.py"
            if path.exists():
                path.unlink()
                print(f"Silindi            : {path}")
        comtypes.client.GetModule(config.UIA_TYPELIB)
        print(f"Yeniden uretildi   : {config.UIA_TYPELIB}")
    except Exception as exc:  # noqa: BLE001
        print(f"Onarim basarisiz   : {type(exc).__name__}: {exc}")
        return 1
    print()
    return doctor()


def stats() -> int:
    """Denetim gunlugunun ozeti: arac basina cagri, hata turleri, sure.

    Gelistirmelerin ise yarayip yaramadigini olcmenin yolu bu: kac eylem
    reddedildi (kullanici mesgul, bayat ref, yanit vermiyor), araclar ne
    kadar suruyor. Sonuc kayitlari 0.2.0 ile basladi; oncekiler sayilmaz.
    """
    import json
    from collections import Counter, defaultdict

    rows: list[dict[str, Any]] = []
    for path in (config.LOG_PATH.with_suffix(".1.jsonl"), config.LOG_PATH):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    # Eski yerden aktarilan kayitlar dosyanin sonuna eklenmis olabilir.
    outcomes = sorted((r for r in rows if r.get("event") == "sonuc" and not r.get("dry_run")),
                      key=lambda r: str(r.get("ts", "")))
    print(f"Gunluk: {config.LOG_PATH}")
    if not outcomes:
        print("Sonuc kaydi yok -- 0.2.0 oncesi surumler sadece eylemi yaziyordu.")
        return 0
    failed = sum(1 for r in outcomes if not r.get("ok"))
    print(f"Donem: {outcomes[0]['ts']} -> {outcomes[-1]['ts']}  |  {len(outcomes)} cagri, "
          f"{failed} hata (%{round(failed * 100 / len(outcomes))})")
    print("=" * 58)
    print(f"{'arac':14s} {'cagri':>6s} {'hata':>5s} {'p50 ms':>7s} {'p95 ms':>7s}  hata turleri")
    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in outcomes:
        by_tool[record["tool"]].append(record)
    for tool, records in sorted(by_tool.items(), key=lambda item: -len(item[1])):
        durations = sorted(int(r.get("ms", 0)) for r in records)
        p50 = durations[len(durations) // 2]
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
        errors = Counter(r.get("error", "?") for r in records if not r.get("ok"))
        kinds = ", ".join(f"{name} x{count}" for name, count in errors.most_common(4))
        print(f"{tool:14s} {len(records):6d} {sum(errors.values()):5d} {p50:7d} {p95:7d}  {kinds}")
    return 0


def main() -> None:
    # 0.4.0'a kadar gunluk AppData'daydi (bkz. config.LOG_DIR). Sunucu modunda
    # stdout MCP kanali -- oraya yazilmaz, sadece gunluge.
    moved = safety.migrate_legacy_logs()
    if moved:
        if any(flag in sys.argv for flag in ("--doctor", "--stats", "--selftest", "--repair")):
            print("Eski denetim gunlugu tasindi:\n  " + "\n  ".join(moved) + "\n")
        safety.audit("server", detail="eski gunluk tasindi", moved=moved)

    if "--doctor" in sys.argv:
        raise SystemExit(doctor())
    if "--stats" in sys.argv:
        raise SystemExit(stats())
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--repair" in sys.argv:
        raise SystemExit(repair())

    try:
        from .server import mcp
    except Exception as exc:  # noqa: BLE001 -- olmek yerine sebebi soyleyen yedekle ac
        # Olursek Claude'da sadece "CONNECTION_CLOSED" gorunur, sebep hic
        # gorunmez. Yedek ayni arac adlariyla acilip arizayi anlatir.
        from . import fallback

        traceback.print_exc()      # stderr: Claude Code'un MCP gunlugune duser
        safety.audit("server", detail="BASLATILAMADI", error=f"{type(exc).__name__}: {exc}")
        fallback.build(exc).run()
        return

    safety.start_hotkey()
    safety.audit("server", detail="basladi", dry_run=config.DRY_RUN)
    mcp.run()


if __name__ == "__main__":
    main()
