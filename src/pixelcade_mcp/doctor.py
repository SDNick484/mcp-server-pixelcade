"""`mcp-server-pixelcade doctor`: check everything between this machine and the marquee, layer by layer.

  config    - PIXELCADE_URL parses; dry run on or off; the brightness cap
  tcp       - the arcade accepts TCP on pixelweb's port (8080)
  info      - /info answers and describes the hardware: LED, V2 board, firmware, pixelweb version (PX-INFO),
              noting when it's too old for show_default_marquee (PX-DEFAULT-MARQUEE)
  frontend  - /currentgame says which game the frontend last selected (PX-CURRENTGAME)
  pulse     - if strips are configured, the board reports their brightness (PX-BRIGHTNESS), and whether
              it's above this server's cap (PX-POWER)

Each failure says what to try and which HARDWARE_VALIDATION.md step covers it
(STEP). Nothing here changes what the marquee shows.
"""

from __future__ import annotations

import asyncio
import json
import platform
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlparse

from . import client as client_mod
from .assumptions import unverified
from .client import DEFAULT_MARQUEE_SINCE, PixelcadeClient, PixelcadeError, parse_current_game, parse_version
from .config import Settings
from .logsafe import redact

STEP = {"config": 1, "tcp": 1, "info": 1, "frontend": 5, "pulse": 6}


@dataclass
class Check:
    layer: str
    ok: bool
    detail: str
    hint: str = ""


@dataclass
class Report:
    versions: dict[str, str]
    url: str
    dry_run: bool
    checks: list[Check] = field(default_factory=list)
    unverified_assumptions: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)


def versions() -> dict[str, str]:
    out = {"python": platform.python_version()}
    for pkg in ("mcp-server-pixelcade", "mcp", "httpx"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = "not installed"
    return out


def _hint(layer: str, text: str) -> str:
    return f"{text} (HARDWARE_VALIDATION.md step {STEP[layer]})"


async def run_doctor(settings: Settings, timeout: float = 5.0) -> Report:
    report = Report(
        versions=versions(),
        url=settings.url,
        dry_run=settings.dry_run,
        unverified_assumptions=[f"{a.id}: {a.claim}" for a in unverified()],
    )
    add = report.checks.append
    note = " (dry run)" if settings.dry_run else ""
    add(
        Check("config", True, f"pixelweb at {settings.url}{note}; Pulse brightness cap {settings.pulse_max_brightness}")
    )

    parsed = urlparse(settings.url)
    host, port = parsed.hostname or "", parsed.port or 8080
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.close()
    except (OSError, TimeoutError) as exc:
        add(Check("tcp", False, f"no TCP connection to {host}:{port}: {exc or type(exc).__name__}", _hint(
            "tcp",
            "Is the arcade on? On Batocera, pixelweb starts from /userdata/system/custom.sh; "
            "check with `ssh root@<arcade> pgrep -a pixelweb`",
        )))  # fmt: skip
        return report
    add(Check("tcp", True, f"{host}:{port} accepts connections"))

    client = PixelcadeClient(settings)
    try:
        try:
            info = await client.get_json(["info"])
        except PixelcadeError as exc:
            add(Check("info", False, str(exc), _hint("info", "pixelweb answered TCP but not /info")))
            return report
        kind = info.get("type", "?")
        v2 = info.get("v2board")
        detail = f"{info.get('name', 'Pixelcade')}: {kind} {info.get('resolution', '?')}, V2 board {v2}, "
        detail += f"firmware {info.get('firmwareVersion', '?')}, pixelweb {info.get('softwareVersion', '?')}"
        # Read from the version rather than probed: calling /defaultmarquee would change the display.
        found = parse_version(str(info.get("softwareVersion", "")))
        if found is not None and found < DEFAULT_MARQUEE_SINCE:
            since = ".".join(map(str, DEFAULT_MARQUEE_SINCE))
            detail += f" (show_default_marquee needs {since}+)"
        add(Check("info", True, detail))

        try:
            game = parse_current_game(await client.get(["currentgame"]))
        except PixelcadeError as exc:
            add(Check("frontend", False, str(exc), _hint("frontend", "/currentgame didn't answer")))
            game = None
        else:
            if game is None:
                shown = "nothing since pixelweb started"
            else:
                shown = f"{game['system']}/{game['rom']}" if game["rom"] else f"the {game['system']} system marquee"
            add(Check("frontend", True, f"last marquee shown: {shown}"))

        if info.get("ledStrip"):
            try:
                live = (
                    await client.get_json(["ledstrip", "getbrightness"], timeout=client_mod.PULSE_STATUS_TIMEOUT_S)
                ).get("brightness")
            except PixelcadeError as exc:
                pixels = info.get("ledStripNumPixels", "?")
                add(Check("pulse", False, f"Pulse strips configured ({pixels} LEDs), but: {exc}", _hint(
                    "pulse",
                    "If no strips are connected and powered yet, that's expected; Pulse tools won't work until "
                    "they are. Or turn LED strip support off in Pixelcade's settings",
                )))  # fmt: skip
            else:
                pixels = info.get("ledStripNumPixels", "?")
                over = isinstance(live, int) and live > settings.pulse_max_brightness
                detail = f"{pixels} LEDs, brightness {live}" + (" (above this server's cap)" if over else "")
                add(Check("pulse", True, detail))
        else:
            add(Check("pulse", True, "no Pulse strips configured"))
    finally:
        await client.aclose()
    return report


def render(report: Report, redacted: bool = True) -> str:
    lines = ["mcp-server-pixelcade doctor", ""]
    lines += [f"  {k}: {v}" for k, v in report.versions.items()]
    if report.dry_run:
        lines += ["", "  note: dry run is on (PIXELCADE_DRY_RUN): actions are reported, not sent"]
    lines += ["", f"pixelweb {report.url}"]
    for c in report.checks:
        lines.append(f"   {'ok' if c.ok else 'x '} {c.layer:<9} {c.detail}")
        if c.hint:
            lines.append(f"      -> {c.hint}")
    lines += ["", f"Not yet confirmed on hardware ({len(report.unverified_assumptions)}):"]
    lines += [f"   {u}" for u in report.unverified_assumptions]
    lines += ["", "OK" if report.ok else "PROBLEMS FOUND"]
    text = "\n".join(lines)
    return redact(text) if redacted else text


def to_json(report: Report, redacted: bool = True) -> str:
    text = json.dumps({**asdict(report), "ok": report.ok}, indent=2)
    return redact(text) if redacted else text
