"""PixelcadeClient: one HTTP client for pixelweb, plus parsers for what it returns.

pixelweb's API is all GETs (``/arcade/stream/mame/pacman``, ``/text?t=...``).
Two details shape this module:

* **URLs are built by hand, not with httpx's ``params=``.** Several pixelweb
  options are bare flags (``?nogif``, ``?nointerrupt``, ``?cycle``), and the
  official Batocera hook scripts percent-encode spaces as ``%20``. Building the
  query ourselves sends exactly what the documentation and those scripts send.
  httpx keeps an already-encoded URL as-is (``tests/test_client.py`` checks).

* **Every failure becomes a PixelcadeError** with a message the model can act
  on ("pixelweb isn't answering at ..."), because only ToolError messages reach
  the model; anything else arrives as a bare "Error executing tool".
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.mcpserver.exceptions import ToolError
from typing_extensions import TypedDict

from .config import Settings

log = logging.getLogger(__name__)

# A query item: (key, value), or (key, None) for a bare flag like "nogif".
Query = Sequence[tuple[str, str | int | None]]


class PixelcadeError(ToolError):
    """An error the model should see verbatim.

    Subclassing the SDK's ToolError matters: only ToolError messages reach the
    model; other exceptions surface only as "Error executing tool <name>".
    """


def encode(value: str) -> str:
    # safe="" so "/" becomes %2F too: a value can never add a path segment.
    return quote(value, safe="")


def build_path(segments: Sequence[str], query: Query = ()) -> str:
    path = "/" + "/".join(encode(s) for s in segments)
    parts = []
    for key, value in query:
        parts.append(encode(key) if value is None else f"{encode(key)}={encode(str(value))}")
    return path + ("?" + "&".join(parts) if parts else "")


# --- Shapes returned to the model -----------------------------------------
class CurrentGame(TypedDict):
    system: str
    rom: str


class Pulse(TypedDict):
    supported: bool | None
    enabled: bool | None
    pixels: int | None
    brightness: int | None
    max_brightness: int


class Status(TypedDict):
    url: str
    reachable: bool
    error: str | None
    name: str | None
    type: str | None
    resolution: str | None
    v2_board: bool | None
    firmware: str | None
    software_version: str | None
    current_game: CurrentGame | None
    pulse: Pulse


def parse_current_game(body: str) -> CurrentGame | None:
    """``/currentgame`` -> {system, rom}.

    Batocera's own hook script reads it as "mame,digdug"; the API docs show
    "console%game-name". Accept either; an empty answer means nothing was
    selected since pixelweb started.
    """
    v = body.strip().strip('"')
    for sep in (",", "%"):
        if sep in v:
            system, rom = v.split(sep, 1)
            if system.strip() and rom.strip():
                return CurrentGame(system=system.strip(), rom=rom.strip())
    return None


def parse_name_list(body: str) -> list[str]:
    """The list endpoints' format isn't documented, so take what comes:
    a JSON array, a JSON object whose values (or keys) are names, or plain
    text with one name per line or comma-separated."""
    text = body.strip()
    if not text:
        return []
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        sep = "\n" if "\n" in text else ","
        return [s.strip() for s in text.split(sep) if s.strip()]
    names: list[str] = []
    if isinstance(data, list):
        names = [str(x) for x in data if isinstance(x, (str, int))]
    elif isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                names.extend(str(x) for x in value if isinstance(x, (str, int)))
        if not names:
            names = [str(k) for k in data]
    return [n.strip() for n in names if n.strip()]


def _str(info: dict[str, Any], key: str) -> str | None:
    v = info.get(key)
    return str(v) if v not in (None, "") else None


def _bool(info: dict[str, Any], key: str) -> bool | None:
    v = info.get(key)
    return v if isinstance(v, bool) else None


def _intval(info: dict[str, Any], key: str) -> int | None:
    v = info.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


# --- The client ------------------------------------------------------------
class PixelcadeClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.url,
            timeout=httpx.Timeout(settings.timeout_s, connect=min(settings.timeout_s, 5.0)),
            transport=transport,
            # A wrong redirect would mean a wrong host; pixelweb never redirects.
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, segments: Sequence[str], query: Query = ()) -> str:
        """GET a pixelweb endpoint and return the body, or raise PixelcadeError."""
        path = build_path(segments, query)
        url = self.settings.url
        log.info("GET %s", path)
        try:
            resp = await self._http.get(path)
        except httpx.ConnectError as exc:
            raise PixelcadeError(_connect_advice(url, exc)) from exc
        except httpx.TimeoutException as exc:
            raise PixelcadeError(
                f"pixelweb at {url} didn't answer within {self.settings.timeout_s:g}s. "
                "The arcade may be busy (a write to the marquee's SD card can take a while) or asleep."
            ) from exc
        except httpx.HTTPError as exc:
            raise PixelcadeError(f"Talking to pixelweb at {url} failed: {exc}") from exc
        body = resp.text
        if resp.status_code >= 400:
            snippet = body.strip()[:300] or resp.reason_phrase
            if resp.status_code == 404:
                raise PixelcadeError(
                    f"pixelweb at {url} has no {path.split('?')[0]} endpoint ({snippet}). "
                    "Pixelcade may need updating (re-run the Pixelcade installer on the arcade)."
                )
            raise PixelcadeError(f"pixelweb answered {resp.status_code}: {snippet}")
        return body

    async def get_json(self, segments: Sequence[str], query: Query = ()) -> dict[str, Any]:
        body = await self.get(segments, query)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise PixelcadeError(f"pixelweb sent something that isn't JSON: {body.strip()[:200]!r}") from exc
        if not isinstance(data, dict):
            raise PixelcadeError(f"pixelweb sent unexpected JSON: {body.strip()[:200]!r}")
        return data

    async def status(self) -> Status:
        """Everything get_status reports. Never raises for an unreachable pixelweb:
        "it's off" is itself the answer the model needs."""
        cap = self.settings.pulse_max_brightness
        try:
            info = await self.get_json(["info"])
        except PixelcadeError as exc:
            return Status(
                url=self.settings.url,
                reachable=False,
                error=str(exc),
                name=None,
                type=None,
                resolution=None,
                v2_board=None,
                firmware=None,
                software_version=None,
                current_game=None,
                pulse=Pulse(supported=None, enabled=None, pixels=None, brightness=None, max_brightness=cap),
            )

        current: CurrentGame | None = None
        try:
            current = parse_current_game(await self.get(["currentgame"]))
        except PixelcadeError as exc:
            log.warning("currentgame failed: %s", exc)

        enabled = _bool(info, "ledStrip")
        brightness = _intval(info, "ledStripBrightness")
        if enabled:
            # /info's brightness is pixelweb's setting; this one is read from the board.
            try:
                live = (await self.get_json(["ledstrip", "getbrightness"])).get("brightness")
                if isinstance(live, int):
                    brightness = live
            except PixelcadeError as exc:
                log.warning("getbrightness failed: %s", exc)

        return Status(
            url=self.settings.url,
            reachable=True,
            error=None,
            name=_str(info, "name"),
            type=_str(info, "type"),
            resolution=_str(info, "resolution"),
            v2_board=_bool(info, "v2board"),
            firmware=_str(info, "firmwareVersion"),
            software_version=_str(info, "softwareVersion"),
            current_game=current,
            pulse=Pulse(
                supported=_bool(info, "hasLEDStripSupport"),
                enabled=enabled,
                pixels=_intval(info, "ledStripNumPixels"),
                brightness=brightness,
                max_brightness=cap,
            ),
        )


def _connect_advice(url: str, exc: Exception) -> str:
    host = httpx.URL(url).host
    msg = str(exc).lower()
    if "name or service not known" in msg or "nodename nor servname" in msg or "getaddrinfo" in msg:
        return (
            f"Can't resolve {host}. mDNS (.local names) often doesn't cross VLANs or work under WSL2; "
            "set PIXELCADE_URL to the arcade's IP address, e.g. http://192.168.1.50:8080."
        )
    return (
        f"pixelweb isn't answering at {url}. Is the arcade powered on and on the network? "
        "On Batocera, pixelweb is started from /userdata/system/custom.sh; "
        "check it is running with `ssh root@batocera.local pgrep -a pixelweb`."
    )
