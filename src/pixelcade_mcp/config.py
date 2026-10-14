"""Settings, plus the vocabularies and validators that bound what the model can send.

Pixelcade is driven by ``pixelweb``, a small HTTP listener the Pixelcade installer
runs on the arcade host (on Batocera: ``/userdata/system/pixelcade/pixelweb``,
port 8080). This server is just a typed, allow-listed client for it, so the
only configuration is where pixelweb lives and a couple of safety limits.

Unlike the Shield there is no credential: pixelweb has no authentication at
all. Anything on the LAN can already call every endpoint (including ``/quit``
and ``/shutdown``). This server adds no exposure; it narrows what *the model*
can reach to the endpoints listed in ``server.py``.

Settings come from, most specific first: environment variables, then
``config.json`` in the config directory (default ``~/.config/mcp-server-pixelcade``),
then the defaults below.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args
from urllib.parse import urlparse

log = logging.getLogger(__name__)

DEFAULT_URL = "http://batocera.local:8080"
DEFAULT_TIMEOUT_S = 10.0

# WS2812B LEDs draw up to 60 mA each at full white. Pixelcade's guidance is
# that the V2 board itself can feed ~40 LEDs (up to ~90 at reduced brightness);
# beyond that an external 5 V supply with a common ground is required, or the
# board can overheat. So until you raise it, the model can't push the strips
# past half brightness. The firmware also throttles itself at 55/60/65 °C,
# but a cap that never gets there is better than a thermal cutoff that does.
DEFAULT_PULSE_MAX_BRIGHTNESS = 128

# --- Vocabularies ----------------------------------------------------------
# Named text colors pixelweb accepts; a 6-digit hex string works too.
ColorName = Literal[
    "red",
    "blue",
    "cyan",
    "gray",
    "darkgray",
    "green",
    "lightgray",
    "magenta",
    "orange",
    "yellow",
    "pink",
    "white",
]
COLOR_NAMES: frozenset[str] = frozenset(get_args(ColorName))

# The Pulse zone names pixelweb knows. Zones can also be addressed 0-35.
ZoneName = Literal[
    "left_strip",
    "right_strip",
    "left_speaker_ring",
    "right_speaker_ring",
    "center_matrix",
    "left_matrix",
    "right_matrix",
]
ZONE_NAMES: frozenset[str] = frozenset(get_args(ZoneName))
MAX_ZONE_ID = 35

# Themed clocks for show_widget(widget="clock").
ClockStyle = Literal[
    "digital",
    "pacman",
    "mspacman",
    "galaga",
    "arkanoid",
    "tron",
    "burgertime",
    "dkong",
    "invaders",
    "centipede",
    "asteroids",
    "digdug",
    "frogger",
    "tetris",
    "tempest",
    "retro",
    "sf2",
    "metroid",
    "castlevania",
    "puzzlebobble",
]

SportsLeague = Literal[
    "nfl",
    "nba",
    "nhl",
    "mlb",
    "wnba",
    "college-football",
    "mens-college-basketball",
    "womens-college-basketball",
    "college-baseball",
    "eng.1",
    "esp.1",
    "ger.1",
    "ita.1",
    "fra.1",
    "por.1",
    "ned.1",
    "mex.1",
    "usa.1",
    "uefa.champions",
]


# --- Validators ------------------------------------------------------------
# Every model-supplied value that lands in a URL *path* goes through
# path_segment(): it must be one segment (no "/" or "\"), not "." or "..",
# and free of control characters. It is then percent-encoded, so spaces,
# parentheses and apostrophes in ROM names ("Pac-Man (Atari) (USA)") survive
# and nothing can climb into another endpoint such as /quit.
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class InvalidValue(ValueError):
    """A model-supplied value we refuse to put in a URL. The message is shown to the model."""


def path_segment(value: str, what: str, max_len: int = 200) -> str:
    v = value.strip()
    if not v:
        raise InvalidValue(f"{what} is empty.")
    if len(v) > max_len:
        raise InvalidValue(f"{what} is longer than {max_len} characters.")
    if "/" in v or "\\" in v:
        raise InvalidValue(f"{what} can't contain '/' or '\\' (give just the name, not a path).")
    if v in (".", ".."):
        raise InvalidValue(f"{what} can't be '.' or '..'.")
    if CONTROL_CHARS.search(v):
        raise InvalidValue(f"{what} contains control characters.")
    return v


_HEX = re.compile(r"#?([0-9a-fA-F]{6})")


def text_color(value: str) -> str:
    """A named color or 6-digit hex (with or without '#') -> what pixelweb's ?c= wants."""
    v = value.strip().lower()
    if v in COLOR_NAMES:
        return v
    m = _HEX.fullmatch(v)
    if m:
        return m.group(1)
    names = ", ".join(sorted(COLOR_NAMES))
    raise InvalidValue(f"Unknown color {value!r}. Use a 6-digit hex like 'ff6d00' or one of: {names}.")


# Pulse effects take r/g/b, so named colors map to RGB here.
_NAMED_RGB: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "gray": (128, 128, 128),
    "darkgray": (64, 64, 64),
    "green": (0, 255, 0),
    "lightgray": (192, 192, 192),
    "magenta": (255, 0, 255),
    "orange": (255, 109, 0),
    "yellow": (255, 255, 0),
    "pink": (255, 105, 180),
    "white": (255, 255, 255),
}


def rgb(value: str) -> tuple[int, int, int]:
    v = text_color(value)
    if v in _NAMED_RGB:
        return _NAMED_RGB[v]
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def zone_id(value: str) -> str:
    v = value.strip().lower()
    if v in ZONE_NAMES:
        return v
    if v.isdigit() and 0 <= int(v) <= MAX_ZONE_ID:
        return str(int(v))
    names = ", ".join(sorted(ZONE_NAMES))
    raise InvalidValue(f"Unknown zone {value!r}. Use a zone number 0-{MAX_ZONE_ID} or one of: {names}.")


# Effect names are an open set (new firmware adds them, and per-game ones follow
# patterns like mame_<rom>), so they are not an enum. They still only ever
# become one percent-encoded path segment; this pattern also covers the
# Atari 2600 names with spaces, apostrophes and parentheses.
_EFFECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-.' ()]{0,79}")


def effect_name(value: str) -> str:
    v = value.strip()
    if not _EFFECT.fullmatch(v) or v in (".", ".."):
        raise InvalidValue(f"{value!r} isn't a valid effect name; see list_pulse_effects.")
    return v


_TICKER = re.compile(r"[A-Za-z0-9.^=\-]{1,15}")
_TEAM = re.compile(r"[A-Za-z0-9.]{1,12}")


def code_list(value: str, what: str, pattern: re.Pattern[str], max_items: int) -> str:
    """'aapl, GOOG' -> 'AAPL,GOOG', validating each code."""
    items = [p.strip().upper() for p in value.split(",") if p.strip()]
    if not items:
        raise InvalidValue(f"No {what} given.")
    if len(items) > max_items:
        raise InvalidValue(f"At most {max_items} {what} at a time.")
    bad = [p for p in items if not pattern.fullmatch(p)]
    if bad:
        raise InvalidValue(f"Not valid {what}: {', '.join(bad)}.")
    return ",".join(items)


def tickers(value: str) -> str:
    return code_list(value, "ticker symbols", _TICKER, 20)


def teams(value: str) -> str:
    return code_list(value, "team codes", _TEAM, 20)


# --- Settings --------------------------------------------------------------
def config_dir() -> Path:
    override = os.environ.get("PIXELCADE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "mcp-server-pixelcade"


@dataclass(frozen=True)
class Settings:
    url: str
    timeout_s: float = DEFAULT_TIMEOUT_S
    pulse_max_brightness: int = DEFAULT_PULSE_MAX_BRIGHTNESS
    # Read pixelweb, change nothing: actions report the GET they would send.
    dry_run: bool = False
    # How results name the marquee; defaults to pixelweb's URL.
    name: str | None = None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def normalize_url(raw: str) -> str:
    """'batocera.local' or '192.168.1.20:8080' -> 'http://...:8080' (no trailing slash)."""
    v = raw.strip().rstrip("/")
    if "://" not in v:
        v = "http://" + v
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"PIXELCADE_URL {raw!r} isn't an http URL or host name")
    if parsed.port is None:
        v = f"{parsed.scheme}://{parsed.hostname}:8080{parsed.path}"
    return v


def _int(value: Any, default: int, lo: int, hi: int, name: str) -> int:
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        log.warning("Ignoring %s=%r: not a whole number", name, value)
        return default
    if not lo <= n <= hi:
        log.warning("Ignoring %s=%r: must be %d-%d", name, value, lo, hi)
        return default
    return n


def flag(value: object) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_settings(dry_run: bool | None = None) -> Settings:
    data = _read_json(config_dir() / "config.json")
    url = os.environ.get("PIXELCADE_URL") or data.get("url") or DEFAULT_URL
    cap = _int(
        os.environ.get("PIXELCADE_PULSE_MAX_BRIGHTNESS", data.get("pulse_max_brightness")),
        DEFAULT_PULSE_MAX_BRIGHTNESS,
        0,
        255,
        "pulse_max_brightness",
    )
    timeout = DEFAULT_TIMEOUT_S
    raw_timeout = os.environ.get("PIXELCADE_TIMEOUT", data.get("timeout_s"))
    if raw_timeout is not None:
        try:
            timeout = min(max(float(raw_timeout), 1.0), 60.0)
        except (TypeError, ValueError):
            log.warning("Ignoring timeout %r: not a number", raw_timeout)
    if dry_run is None:
        dry_run = flag(os.environ.get("PIXELCADE_DRY_RUN", data.get("dry_run", False)))
    name = os.environ.get("PIXELCADE_NAME") or data.get("name") or None
    return Settings(
        url=normalize_url(str(url)),
        timeout_s=timeout,
        pulse_max_brightness=cap,
        dry_run=dry_run,
        name=str(name) if name else None,
    )
