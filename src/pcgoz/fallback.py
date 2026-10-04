"""Sunucu kurulamadiginda devreye giren yedek.

Asil sunucunun importu patlarsa surec olur ve Claude'da sadece
"CONNECTION_CLOSED" gorunur: araclar yok, sebep yok. 2026-08-31'deki Windows
guncellemesinden sonra 23 gun boyle kaldi. Yedek ayni arac adlarini acar --
model araci aradiginda bulur, cagirdiginda arizanin sebebini ve onarimini
okur. Ayni metin tanitim metninde de var, yani oturum basinda sistem
promptunda da gorunur.

Sadece mcp'ye ve standart kutuphaneye dayanir: asil sunucuyu bozan sey
burada da import edilirse yedek de olur.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__

#: server.py'deki araclarla ayni olmali; --doctor ikisini karsilastirir.
TOOL_NAMES = (
    "pc_windows", "pc_read", "pc_find", "pc_text", "pc_screenshot",
    "pc_invoke", "pc_do", "pc_wait_for", "pc_clipboard", "pc_focus", "pc_batch",
    "pc_window", "pc_ocr",
)


def _location(exc: BaseException) -> str:
    """Istisnanin cikti gercek dosya:satir -- importlib ic cercevelerini atla."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__)
              if not f.filename.startswith("<frozen")]
    if not frames:
        return ""
    return f" ({Path(frames[-1].filename).name}:{frames[-1].lineno})"


def explain(exc: BaseException) -> str:
    python = sys.executable
    return (
        f"PcGoz BASLATILAMADI -- {type(exc).__name__}: {exc}{_location(exc)}\n\n"
        "Masaustu araclari su an calismiyor; hepsi sadece bu mesaji dondurur. "
        "Kullaniciya soyle ve su komutlari oner:\n"
        f'  teshis: "{python}" -m pcgoz --doctor\n'
        f'  onarim: "{python}" -m pcgoz --repair\n'
        "Onarimdan sonra Claude Code yeniden baslatilmali -- sunucu oturum "
        "basinda acilir."
    )


def build(exc: BaseException) -> MCPServer:
    message = explain(exc)
    server = MCPServer(name="pcgoz", version=__version__, instructions=message)

    # Parametresiz: model araci her zamanki argumanlarla cagirir, fazlalari
    # yok sayilir ve cagri reddedilmek yerine bu mesaja ulasir.
    def broken() -> str:
        raise ToolError(message)

    for name in TOOL_NAMES:
        server.add_tool(
            broken, name=name,
            description=f"PcGoz CALISMIYOR -- cagrilirsa sebebini ve onarimini soyler.\n\n{message}",
        )
    return server
