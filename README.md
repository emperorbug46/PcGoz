# PcGöz

[![CI](https://github.com/emperorbug46/PcGoz/actions/workflows/python-app.yml/badge.svg)](https://github.com/emperorbug46/PcGoz/actions/workflows/python-app.yml)

**An MCP server that lets Claude Code see and use your Windows desktop.**

![Claude works out 1234 × 5678 in Calculator: it takes a screenshot with numbered boxes, then clicks the buttons by ref](docs/pcgoz-demo.gif)

▶ [Watch the 4-minute demo with narration](https://github.com/emperorbug46/PcGoz/releases/download/v0.6.2/PcGoz-demo.mp4): nine scenes, from feature demos and token savings to real tasks in WhatsApp, the browser and a PDF app.

It reads the UI Automation tree of a window, takes screenshots with numbered boxes drawn over the elements, and clicks and types with real mouse and keyboard input. The numbers match: `ref_7` in the tree is box 7 in the screenshot, so Claude can look at the picture and act on the element without guessing pixel coordinates. After each action it reports what changed on screen.

[Türkçe](README.tr.md) · Unofficial, not affiliated with Anthropic.

## Why PcGöz

- **Numbered refs, not pixel guessing.** Every element gets a `ref_N`, and the screenshot shows the same numbers in boxes. Claude points at an element by its number, so a click can't drift onto the wrong button.
- **Text first, far fewer tokens.** Claude gets the UI tree, the plain text or just the element it asked for instead of a screenshot. Measured on a sign-in form: about 40 tokens of text against about 1,600 for a full-screen image.
- **A change report after every action.** Each click or keystroke comes back with what changed on screen, so Claude rarely needs another screenshot to check. Fewer images, fewer tokens.
- **Works without the mouse.** `pc_invoke` presses, toggles, selects and expands through UI Automation: the cursor stays where it is, and it works while the window is covered (some apps bring themselves to the front when a button is invoked).
- **Built for your real machine.** Your own input is held back while an action runs, `Ctrl+Alt+Shift+Esc` freezes everything, password fields and UAC prompts are refused, and a click on an element that moved or closed is refused too.
- **Apps without an accessibility tree.** Canvas apps, Qt and games are read with the OCR built into Windows, locally.
- **Knows when the screen is stale.** A covered or minimized browser or Electron app stops repainting. PcGöz notices and says so instead of handing Claude old content.
- **Private by design.** No network connections, screenshots are never written to disk, typed text is never logged.

## What you can ask for

- "Build and run my WinForms app and check that the main window opens with all its buttons."
- "Read the error dialog that's open on my screen and tell me what went wrong."
- "Open Settings > Display and tell me the recommended scaling."
- "Open Notepad, type this text and check that every character came through."

Works with Claude Code, either the CLI or the Code tab in the Claude desktop app.

## Install

You need Windows 10 2004 or later (or Windows 11), Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/emperorbug46/PcGoz
cd PcGoz
uv sync
claude mcp add pcgoz --scope user -- "$PWD\.venv\Scripts\python.exe" -m pcgoz
```

Restart Claude Code, then check the setup:

```powershell
.\.venv\Scripts\python.exe -m pcgoz --doctor
```

The server is registered with the venv's `python.exe` rather than `uv run`. Claude Code starts MCP servers on demand, and if `uv run` stalls on a dependency check the tools silently never show up.

Optional: the `masaustu-kullanimi` skill tells Claude when to use PcGöz instead of shell commands and how to handle focus and navigation quirks.

```powershell
New-Item -ItemType Junction -Path "$HOME\.claude\skills\masaustu-kullanimi" -Target "$PWD\skills\masaustu-kullanimi"
```

`pc_ocr` uses the OCR built into Windows, which needs the *Optical character recognition* feature for at least one installed language (Settings > Time & language > Language & region).

## Safety

PcGöz drives your real mouse and keyboard and doesn't ask before each click. These are the guards instead:

- **Emergency stop:** `Ctrl+Alt+Shift+Esc` freezes every running PcGöz instance. Press it again to resume. If another app already holds that shortcut, Pause, F12 or Backspace with the same modifiers is used. If none of them can be registered, every action result carries a warning.
- **Input lock:** while an action runs, your own mouse and keyboard input is held back so you don't collide with it. Key releases still go through, so nothing gets stuck. A small notice in the top-left corner shows when the lock is on. It lets go after 30 seconds at most (60 for a batch). `PCGOZ_BLOCK_INPUT=0` turns it off.
- **Passwords:** typing or pasting into password fields and Windows credential prompts is refused. Only navigation and delete keys (Tab, Enter, arrows, Backspace, ...) are allowed there.
- **Secure desktop:** no input is sent while a UAC prompt, the lock screen or Ctrl+Alt+Del is showing. The tool returns an error instead.
- **Stale targets:** if an element has closed, moved or been covered since it was read, the click is refused rather than landing on whatever is there now.
- **One session at a time:** several Claude sessions wait their turn for the mouse and keyboard instead of fighting over them.
- **Dry run:** with `PCGOZ_DRY_RUN=1` the read-only tools work normally and the input tools only log what they would have done.

If you want to check this yourself before installing, ask Claude Code to review the source. It's also a good way to see exactly what each tool can and can't do.

## Privacy

- PcGöz makes no network connections. Screenshots and OCR are processed locally with Windows APIs.
- Screenshots are never written to disk. They do reach Anthropic as part of your conversation, like any image you paste into Claude.
- Actions are logged to `~/.pcgoz/actions.jsonl`, rotated at 5 MB. Set `PCGOZ_LOG_DIR` to move it. The log contains the keys sent with `pc_do`, but not the text typed: only its length and a short SHA-256 prefix, enough to spot the same text typed twice. Clipboard contents are never logged, only their length. Read-only tools log just the tool name, duration and result.

`python -m pcgoz --stats` summarizes the log: calls, error rate and latency per tool.

## Tools

| Tool | What it does | Sends input |
|---|---|---|
| `pc_windows` | Lists open windows, popup menus and monitors | no |
| `pc_read` | UI Automation tree of a window, with `ref_N` numbers | no |
| `pc_find` | Searches a window by name, value, role or automation ID, ignoring case and accents | no |
| `pc_text` | Plain text of a window or element | no |
| `pc_screenshot` | Screen, window or region. `annotate=True` draws the ref boxes. If the window is hidden behind another, it says which one | no |
| `pc_ocr` | Windows OCR, for apps that expose no accessibility info (Qt, canvas, games) | no |
| `pc_wait_for` | Waits until an element appears or disappears (up to 600 s; other tools keep working meanwhile) | no |
| `pc_invoke` | Invoke, toggle, select, expand or set a value through UI Automation, without moving the mouse | yes |
| `pc_do` | Click, right/double/middle click, hover, type, key, scroll, drag | yes |
| `pc_focus` | Brings a window to the front, also when Windows' focus lock or a covered title bar is in the way | yes |
| `pc_window` | Close, minimize, maximize, restore, move | yes |
| `pc_clipboard` | Reads or sets clipboard text | only `set` |
| `pc_batch` | Runs several calls in one go and stops at the first error | if any step does |

Tool descriptions and messages are in Turkish (plain ASCII). Claude reads them without trouble and still answers you in your own language.

## What's new in 0.6.2

- **Stale content is flagged.** A Chromium or Electron window (browsers, VS Code, Discord, ...) that is fully covered, minimized or on another virtual desktop stops repainting and stops updating its accessibility tree, so screenshots and text can be several seconds old. PcGöz now works this out the same way Chromium does and adds a `[!]` line to the result. It also warns when the display is off, the session is locked or a secure desktop is showing.
- **`pc_focus` wakes such windows.** A covered Chromium window that was already in the foreground used to stay stale for seconds; it now repaints right away.
- **`pc_wait_for` no longer blocks other tools** while it waits, and can wait up to 600 s.
- **Sturdier focusing.** If the title bar is covered, `pc_focus` minimizes and restores the window instead of giving up.
- Smaller fixes: one retry on transient UI Automation errors, screenshots say when the target is covered, waits inside a batch are cut to the input-lock time, and typed text is no longer written to the log.

## Limitations

- **Elevated windows:** if an app runs as administrator and Claude Code doesn't, Windows blocks input to it (UIPI). Run Claude Code as administrator to automate something like Task Manager.
- **Apps without accessibility info** (Qt, canvas, games, remote desktop) don't show up in the tree. Use `pc_screenshot` and `pc_ocr` for them.
- **Hung apps** are probed with a timeout first, so a frozen window doesn't freeze the server.
- **Covered Chromium/Electron windows** return old content until they're brought to the front; such results carry a `[!]` line. Only Chromium-based windows are checked, not Firefox or WebView2 apps.

## Troubleshooting

- **Tools are missing after moving the folder:** run `--doctor`. It checks whether `~/.claude.json` still points at the right `python.exe`.
- **Errors from the accessibility layer (`comtypes`):** `python -m pcgoz --repair` regenerates the cached wrappers, then runs `--doctor`.
- **Fallback mode:** if the server can't import something, it still starts, offers the same tools and explains what broke. Without this, Claude would only show a bare `CONNECTION_CLOSED`.
- **A result has a `[!]` line:** the content may be old. Bring the window to the front with `pc_focus`; if the display is off, move the mouse (`pc_do action="hover"`) to wake it.

## Development

The tests are plain scripts, run with e.g. `uv run python tests/birim.py`. `birim.py` and `mcp_istemci.py` send no input and run in CI on Windows. The others (`smoke.py`, `kapsam.py`, `tus_probu.py`, ...) use the real mouse and keyboard, so run them when you're not using the machine. `ortulu_chromium.py` opens a temporary Brave window to check the stale-content warning, and `ekran_kapali.py` turns the display off.

Design notes and measurements, in Turkish: [docs/GELISTIRICI-NOTLARI.md](docs/GELISTIRICI-NOTLARI.md)

## License

[MIT](LICENSE)
