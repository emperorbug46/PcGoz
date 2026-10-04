"""MCP sinirindan uctan uca test. Elle calistirilir: uv run python tests/mcp_istemci.py

Diger testler sunucu fonksiyonlarini dogrudan cagiriyor; bu ise Claude Code'un
yaptigi gibi sureci stdio ile baslatip protokol uzerinden konusuyor. Iki riski
sadece bu sinir yakalar:

1. Sunucu hic acilmiyor. 2026-08-31'deki Windows guncellemesinden sonra
   import aninda oluyordu; Claude'da sadece "CONNECTION_CLOSED" gorundu.
2. Hata metni modele ulasmiyor. mcp 2.x ToolError disindaki her istisnanin
   metnini gizleyip "Error executing tool X" gonderiyor -- "kullanici mesgul",
   "DONDURULDU", "bayat ref" gibi uyarilar dogrudan cagrida gorunur ama
   modele hic gitmez.

Girdi gondermez: sunucu PCGOZ_DRY_RUN=1 ile acilir, sadece okuma araclari
cagrilir. Denetim gunlugu gecici bir klasore yonlendirilir.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TOOLS = {
    "pc_windows", "pc_read", "pc_find", "pc_text", "pc_screenshot",
    "pc_invoke", "pc_do", "pc_wait_for", "pc_clipboard", "pc_focus", "pc_batch",
    "pc_window", "pc_ocr",
}

#: Sunucu importunu bilerek bozan baslatici -- yedek modu denemek icin.
#: sys.modules'a None koymak o modulun importunu ImportError'a cevirir.
BROKEN_START = (
    "import sys; sys.modules['pcgoz.uia'] = None; sys.argv = ['pcgoz']; "
    "from pcgoz.__main__ import main; main()"
)

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (passed if ok else failed).append(name)
    mark = "OK  " if ok else "HATA"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))


def _text(result) -> str:  # noqa: ANN001
    return "\n".join(getattr(c, "text", "") for c in result.content)


def _params(args: list[str], log_dir: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=args,
        env={
            "PYTHONPATH": str(ROOT / "src"),
            "PCGOZ_DRY_RUN": "1",
            "PCGOZ_HOTKEY": "0",        # calisan bir oturumun kisayoluyla carpismasin
            "PCGOZ_LOG_DIR": str(Path(log_dir) / "PcGoz"),   # gercek denetim gunlugunu kirletmesin
            "LOCALAPPDATA": log_dir,    # eski gunluk yerleri de bos: tasima denenmesin
        },
        cwd=str(ROOT),
    )


async def _session(args: list[str], log_dir: str, body) -> None:  # noqa: ANN001
    # Sunucunun stderr'i (istisna izleri) test ciktisina karismasin.
    errlog = Path(log_dir) / "sunucu_stderr.txt"
    with errlog.open("a", encoding="utf-8") as err:
        try:
            async with stdio_client(_params(args, log_dir), errlog=err) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await asyncio.wait_for(session.initialize(), timeout=30)
                    await body(session, init)
        except Exception as exc:  # noqa: BLE001 -- sunucu olduyse sebebi test ciktisinda gorunsun
            check("sunucuyla oturum kuruldu", False, f"{type(exc).__name__}: {exc}"[:120])


async def test_normal(log_dir: str) -> None:
    print("Normal sunucu")

    async def body(session: ClientSession, init) -> None:  # noqa: ANN001
        listing = (await session.list_tools()).tools
        tools = {t.name for t in listing}
        check(f"{len(EXPECTED_TOOLS)} arac listelendi", tools == EXPECTED_TOOLS,
              ", ".join(sorted(tools ^ EXPECTED_TOOLS)) or f"{len(tools)} arac")

        # Araclar hata metnini tasiyan bir sarmalayiciyla kayitli; sema
        # sarmalayicidan degil asil fonksiyondan cikmali.
        schema = next(t for t in listing if t.name == "pc_do").input_schema
        props = schema.get("properties", {})
        check("pc_do semasi korundu",
              "action" in schema.get("required", []) and "coordinate" in props
              and "enum" in str(props.get("action")), f"{len(props)} parametre")

        result = await session.call_tool("pc_windows", {})
        text = _text(result)
        check("pc_windows calisti", not result.is_error and "Sanal ekran" in text, text.splitlines()[0])

        # Olmayan pencere: aracin kendi aciklamasi modele ulasmali.
        result = await session.call_tool("pc_read", {"hwnd": 1})
        text = _text(result)
        check("hata isaretlendi", result.is_error, text[:80])
        check("hata metni modele ulasti", "artik yok" in text, text[:120])

        # pc_batch: adimlar sirayla, ilk hatada durur; adim argumanlari da
        # aracin kendi semasiyla dogrulanir (MCP'den gecmedikleri halde).
        result = await session.call_tool("pc_batch", {"actions": [
            {"name": "pc_windows", "input": {}},
            {"name": "pc_read", "input": {"hwnd": 1}},
            {"name": "pc_windows", "input": {}},
        ]})
        text = _text(result)
        check("pc_batch ilk adimi calistirdi", "[1/3] pc_windows: Sanal ekran" in text)
        check("pc_batch hatada durdu ve kalani bildirdi",
              "[2/3] pc_read HATA" in text and "Kalan 1 adim" in text and "[3/3]" not in text,
              next((line for line in text.splitlines() if "HATA" in line), "")[:90])
        result = await session.call_tool("pc_batch", {"actions": [
            {"name": "pc_read", "input": {"hwnd": "bir"}}]})
        check("pc_batch adim argumanini dogruladi", "ValidationError" in _text(result),
              _text(result).splitlines()[0][:90])
        result = await session.call_tool("pc_batch", {"actions": [{"name": "pc_yok", "input": {}}]})
        check("pc_batch bilinmeyen araci reddetti", "bilinmeyen arac" in _text(result))

        # Gunluk icin bir eylem (DRY_RUN: tus gitmez): on kayit + sonuc kaydi.
        await session.call_tool("pc_do", {"action": "key", "key": "esc", "force": True})

        # OCR, MCP'nin is parcacigi havuzunda kendi olay dongusunu aciyor.
        result = await session.call_tool("pc_ocr", {"region": [0, 0, 400, 120]})
        check("pc_ocr MCP uzerinden calisti", not result.is_error and "OCR (" in _text(result),
              _text(result).splitlines()[0][:80])

    await _session(["-m", "pcgoz"], log_dir, body)

    # Denetim gunlugu: her cagrinin sonucu ve suresi, eylemin on kaydiyla ayni kimlik.
    import json

    log = Path(log_dir) / "PcGoz" / "actions.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    results = [r for r in rows if r.get("event") == "sonuc"]
    check("okuma araclarinin sonucu yazildi",
          any(r["tool"] == "pc_windows" and r["ok"] and "ms" in r for r in results))
    check("hata turu yazildi",
          any(r["tool"] == "pc_read" and not r["ok"] and r["error"] == "LookupError" for r in results))
    check("pc_batch adimlari topluya bagli",
          any(r.get("batch") and r["tool"] == "pc_windows" for r in results))
    key = next((r for r in rows if r["tool"] == "pc_do" and r.get("action") == "key"), None)
    linked = [r for r in results if key and r.get("call") == key.get("call") and r["tool"] == "pc_do"]
    check("eylemin on kaydi ile sonucu ayni kimlikte", bool(linked),
          f"on kayit call={key.get('call') if key else None}")


async def test_fallback(log_dir: str) -> None:
    print("Yedek mod (import bilerek bozuldu)")

    async def body(session: ClientSession, init) -> None:  # noqa: ANN001
        instructions = init.instructions or ""
        check("sunucu yine de acildi", True)
        check("tanitim metni arizayi soyluyor", "BASLATILAMADI" in instructions,
              instructions.splitlines()[0][:80] if instructions else "(bos)")

        tools = {t.name for t in (await session.list_tools()).tools}
        check("ayni arac adlari sunuldu", tools == EXPECTED_TOOLS,
              ", ".join(sorted(tools ^ EXPECTED_TOOLS)) or f"{len(tools)} arac")

        # Model araci her zamanki argumanlarla cagirir; reddedilmemeli,
        # arizayi anlatmali.
        result = await session.call_tool("pc_do", {"action": "left_click", "ref": "ref_1"})
        text = _text(result)
        check("cagri hata olarak isaretlendi", result.is_error)
        check("hata metni sebebi ve onarimi soyluyor",
              "pcgoz.uia" in text and "--repair" in text, text[:120])

    await _session(["-c", BROKEN_START], log_dir, body)

    log = Path(log_dir) / "PcGoz" / "actions.jsonl"
    content = log.read_text(encoding="utf-8") if log.exists() else ""
    check("ariza denetim gunlugune yazildi", "BASLATILAMADI" in content)


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="pcgoz-test-") as log_dir:
        await test_normal(log_dir)
        await test_fallback(log_dir)
    print("=" * 58)
    print(f"{len(passed)} gecti, {len(failed)} kaldi")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
