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
import re
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
    ``status`` is pixelweb's HTTP status when it answered with an error, so a
    tool can add advice for one case (a 404 from an endpoint newer than the arcade's pixelweb).
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def encode(value: str) -> str:
    # safe="" so "/" becomes %2F too: a value can never add a path segment.
    return quote(value, safe="")


# ASSUMPTION PX-GET-API: GETs, bare-flag options, %20 for spaces (as the hook scripts send).
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
    # False: strips are configured in pixelweb but the board didn't answer (none connected, or no power);
    # brightness is then pixelweb's setting. None: not asked (strips not configured, or pixelweb unreachable).
    responding: bool | None


class Status(TypedDict):
    url: str
    dry_run: bool
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
    animated_marquees: bool | None  # False: pixelweb shows still (PNG) marquees only; None: not reported


# ASSUMPTION PX-CURRENTGAME
def parse_current_game(body: str) -> CurrentGame | None:
    """``/currentgame`` -> {system, rom}.

    Batocera's game-selected hook reads it as "mame,digdug"; the API docs and
    the achievements hook show "mame%digdug". Accept either, splitting at
    whichever separator comes first: system names have neither, but ROM names
    can have commas ("Zelda, The"). An empty answer means nothing was selected
    since pixelweb started.
    """
    v = body.strip().strip('"')
    cuts = [i for i in (v.find(","), v.find("%")) if i > 0]
    if not cuts:
        return None
    system, rom = v[: min(cuts)].strip(), v[min(cuts) + 1 :].strip()
    return CurrentGame(system=system, rom=rom) if system and rom else None


# How long get_status and doctor wait for the Pulse board: it doesn't answer when strips are configured
# but not connected (PX-BRIGHTNESS), so waiting the full PIXELCADE_TIMEOUT would stall every status call.
PULSE_STATUS_TIMEOUT_S = 2.0

# ASSUMPTION PX-DEFAULT-MARQUEE: new in pixelweb 8.0.14, and only in the release notes.
DEFAULT_MARQUEE_SINCE = (8, 0, 14)


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """pixelweb's softwareVersion ("8.0.17", "7.8.5-sim") -> (8, 0, 17); None if it doesn't start with one."""
    m = re.match(r"\s*(\d+(?:\.\d+)*)", text or "")
    return tuple(int(n) for n in m.group(1).split(".")) if m else None


# ASSUMPTION PX-LISTS: the format isn't documented, so this is deliberately tolerant.
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

    async def get(self, segments: Sequence[str], query: Query = (), timeout: float | None = None) -> str:
        """GET a pixelweb endpoint and return the body, or raise PixelcadeError.

        ``timeout`` shortens the wait for one request (the client's default is PIXELCADE_TIMEOUT).
        """
        path = build_path(segments, query)
        url = self.settings.url
        wait = timeout if timeout is not None else self.settings.timeout_s
        log.info("GET %s", path)
        try:
            resp = await self._http.get(path, timeout=httpx.Timeout(wait, connect=min(wait, 5.0)))
        except httpx.ConnectError as exc:
            raise PixelcadeError(_connect_advice(url, exc)) from exc
        except httpx.TimeoutException as exc:
            if path.startswith("/ledstrip/"):
                # ASSUMPTION PX-BRIGHTNESS: seen on hardware, a Pulse request hangs when pixelweb has strips
                # configured but none are connected.
                raise PixelcadeError(
                    f"pixelweb didn't answer a Pulse request within {wait:g}s. It has Pulse strips configured, "
                    "but they may not be connected or powered (get_status: pulse.responding)."
                ) from exc
            raise PixelcadeError(
                f"pixelweb at {url} didn't answer within {wait:g}s. "
                "The arcade may be busy (a write to the marquee's SD card can take a while) or asleep."
            ) from exc
        except httpx.HTTPError as exc:
            raise PixelcadeError(f"Talking to pixelweb at {url} failed: {exc}") from exc
        body = resp.text
        if resp.status_code >= 400:
            snippet = body.strip()[:300] or resp.reason_phrase
            if resp.status_code == 404:  # ASSUMPTION PX-REPLIES
                raise PixelcadeError(
                    f"pixelweb at {url} has no {path.split('?')[0]} endpoint ({snippet}). "
                    "Pixelcade may need updating (re-run the Pixelcade installer on the arcade).",
                    status=404,
                )
            raise PixelcadeError(f"pixelweb answered {resp.status_code}: {snippet}", status=resp.status_code)
        return body

    async def get_json(
        self, segments: Sequence[str], query: Query = (), timeout: float | None = None
    ) -> dict[str, Any]:
        body = await self.get(segments, query, timeout)
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
            info = await self.get_json(["info"])  # ASSUMPTION PX-INFO
        except PixelcadeError as exc:
            return Status(
                url=self.settings.url,
                dry_run=self.settings.dry_run,
                reachable=False,
                error=str(exc),
                name=None,
                type=None,
                resolution=None,
                v2_board=None,
                firmware=None,
                software_version=None,
                current_game=None,
                pulse=Pulse(
                    supported=None, enabled=None, pixels=None, brightness=None, max_brightness=cap, responding=None
                ),
                animated_marquees=None,
            )

        current: CurrentGame | None = None
        try:
            current = parse_current_game(await self.get(["currentgame"]))
        except PixelcadeError as exc:
            log.warning("currentgame failed: %s", exc)

        enabled = _bool(info, "ledStrip")
        brightness = _intval(info, "ledStripBrightness")
        responding: bool | None = None
        if enabled:
            # /info's brightness is pixelweb's setting; this one is read from the board. A short wait:
            # with strips configured but not connected the board never answers (PX-BRIGHTNESS), and
            # get_status is the first call a session makes.
            try:
                # ASSUMPTION PX-BRIGHTNESS
                live = (await self.get_json(["ledstrip", "getbrightness"], timeout=PULSE_STATUS_TIMEOUT_S)).get(
                    "brightness"
                )
                responding = True
                if isinstance(live, int):
                    brightness = live
            except PixelcadeError as exc:
                responding = False
                log.warning("getbrightness failed: %s", exc)
        # pixelweb 8's "static PNG marquees only" setting (seen in /info on 8.0.17); absent on older versions.
        no_anim = info.get("noAnimatedMarquees")

        return Status(
            url=self.settings.url,
            dry_run=self.settings.dry_run,
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
                responding=responding,
            ),
            animated_marquees=(not no_anim) if isinstance(no_anim, bool) else None,
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
