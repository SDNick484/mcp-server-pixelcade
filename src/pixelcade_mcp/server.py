"""MCP tool definitions: a typed, allow-listed slice of the pixelweb REST API.

How a decorated function becomes a tool the model can call:
  - ``@mcp.tool()`` registers it; it then appears in the client's ``tools/list``.
  - The function's *docstring* becomes the tool's ``description``. It is the
    model's only documentation, so it is written for the model, not for us.
  - The *signature* becomes the ``inputSchema`` (JSON Schema): ``Literal`` turns
    into an enum, ``Field(ge=..., le=...)`` into minimum/maximum, and defaults
    make arguments optional. The SDK validates every call against it.
  - The *return type* becomes the ``outputSchema`` when it is structured
    (a TypedDict, model or dict), and the value is sent as structured content.

What is deliberately NOT reachable from here, even though pixelweb serves it:
``/quit``, ``/reboot``, ``/shutdown`` (stop the listener or the arcade),
``/arcade/write`` (writes the marquee's SD card), ``/upload*``, ``/settings``,
``/update/*``, ``/ledstrip/numpixels`` (strip hardware config), and the OAuth
and Vestaboard endpoints. Every path is built in this file from constants plus
validated, percent-encoded segments, so no argument can name another endpoint.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Literal, TypeVar

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from typing_extensions import TypedDict

from . import config as cfg
from .client import PixelcadeClient, PixelcadeError, Query, Status, build_path, parse_current_game, parse_name_list
from .config import ClockStyle, SportsLeague, load_settings
from .effects import EFFECTS, PATTERNS, EffectCategory

log = logging.getLogger(__name__)

_client: PixelcadeClient | None = None


def client() -> PixelcadeClient:
    assert _client is not None, "server lifespan has not started"
    return _client


# pixelweb is plain stateless HTTP, so unlike the Shield there is no
# connection to hold open: the lifespan only creates (and later closes) the
# shared httpx client, which pools connections between calls.
@asynccontextmanager
async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
    global _client
    _client = PixelcadeClient(load_settings())
    try:
        yield
    finally:
        await _client.aclose()
        _client = None


mcp = MCPServer(
    "pixelcade",
    instructions=(
        "Controls a Pixelcade LED marquee (128x32 pixels) on an arcade cabinet, and optional Pixelcade Pulse "
        "LED strips, speaker rings and matrices, through Pixelcade's pixelweb listener. "
        "Call get_status first: it says whether the arcade is reachable, which game is selected, and whether "
        "Pulse strips are configured. The arcade frontend (EmulationStation on Batocera) also drives the marquee: "
        "scrolling to or launching a game replaces whatever you showed, and launching a game stops widgets. "
        "Use show_current_game to put the frontend's marquee back."  # ASSUMPTION PX-FRONTEND
    ),
    lifespan=lifespan,
)

# Annotations are hints clients use to decide what needs confirmation. Nothing
# here is destructive: every display change is undone by the next one (or by
# the frontend). open_world_hint=False: we only talk to one known device.
_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_ACT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
_SET = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

T = TypeVar("T")


def _check(fn: Callable[..., T], *args: object) -> T:
    """Run a validator from config.py, turning its complaint into a model-visible error."""
    try:
        return fn(*args)
    except cfg.InvalidValue as exc:
        raise PixelcadeError(str(exc)) from exc


# --- What an action returns ---------------------------------------------------------
# Every tool that changes something returns an ActionResult, the same shape as the
# sibling servers' (Shield, Onkyo, Batocera...): structured, so a client can read
# `outcome` and `sent` without parsing prose, with `detail` one sentence for the user.
# Failures aren't an outcome: they're isError results (PixelcadeError).
Outcome = Literal["done", "unchanged", "dry_run"]


class ActionResult(TypedDict):
    pixelcade: str  # PIXELCADE_NAME, or pixelweb's address
    outcome: Outcome
    detail: str
    sent: list[str]  # the GETs that went to pixelweb (or, in a dry run, would have)
    warnings: list[str]


def _result(detail: str, sent: list[str], outcome: Outcome = "done") -> ActionResult:
    settings = client().settings
    if outcome == "dry_run":
        detail = f"DRY RUN, nothing sent: {detail}"
    return ActionResult(
        pixelcade=settings.name or settings.url,
        outcome=outcome,
        detail=detail,
        sent=sent,
        warnings=[],
    )


async def _send(segments: list[str], query: Query, detail: str) -> ActionResult:
    """The one place an action reaches pixelweb: honors dry run, then appends pixelweb's reply if it's short."""
    sent = [f"GET {build_path(segments, query)}"]
    if client().settings.dry_run:
        return _result(detail, sent, "dry_run")
    reply = (await client().get(segments, query)).strip()
    if reply and len(reply) <= 200 and "\n" not in reply:
        detail = f"{detail} (pixelweb: {reply})"
    return _result(detail, sent)


# --- Reading ---------------------------------------------------------------
@mcp.tool(title="Get Pixelcade status", annotations=_READ)
async def get_status() -> Status:
    """Report whether the Pixelcade is reachable, its hardware and software versions, the game last
    selected in the arcade frontend, and Pulse LED strip state (whether configured, pixel count,
    brightness, and the brightness cap this server enforces).

    When unreachable, reachable is false and error says why; nothing else is known then.
    """
    return await client().status()


class ArtworkList(TypedDict):
    kind: str
    total: int
    matches: int
    names: list[str]
    truncated: bool


Contains = Annotated[
    str | None,
    Field(default=None, max_length=60, description="Case-insensitive substring filter, e.g. 'pac'."),
]


@mcp.tool(title="List marquee artwork", annotations=_READ)
async def list_artwork(
    kind: Annotated[
        Literal["arcade", "stills", "animations"],
        Field(description="arcade: MAME game names with artwork; stills: still images; animations: animation names."),
    ],
    contains: Contains = None,
    limit: Annotated[int, Field(ge=1, le=500, description="Maximum names to return.")] = 100,
) -> ArtworkList:
    """List artwork names pixelweb has, optionally filtered. Use it to check a name before showing it:
    arcade names work as the rom for show_game_marquee with system 'mame', animation names for play_animation.
    """
    path = {"arcade": ["arcade", "list"], "stills": ["still", "list"], "animations": ["animations", "list"]}[kind]
    names = parse_name_list(await client().get(path))
    total = len(names)
    if contains:
        needle = contains.strip().lower()
        names = [n for n in names if needle in n.lower()]
    return ArtworkList(kind=kind, total=total, matches=len(names), names=names[:limit], truncated=len(names) > limit)


class EffectCatalog(TypedDict):
    effects: dict[str, list[str]]
    patterns: dict[str, str]
    zones: list[str]


@mcp.tool(title="List Pulse effects", annotations=_READ)
def list_pulse_effects(
    category: Annotated[EffectCategory | None, Field(description="Only this category; omit for all.")] = None,
) -> EffectCatalog:
    """List Pixelcade Pulse effect names for pulse_effect, by category, plus name patterns for per-game
    and per-system effects (e.g. mame_<rom>), and the named zones. matrix_* effects are for the 8x8/8x32
    matrices; the rest suit strips and speaker rings.
    """
    chosen = {category: EFFECTS[category]} if category else EFFECTS
    return EffectCatalog(
        effects={k: list(v) for k, v in chosen.items()},
        patterns=dict(PATTERNS),
        zones=sorted(cfg.ZONE_NAMES),
    )


# --- The marquee -----------------------------------------------------------
System = Annotated[
    str,
    Field(
        min_length=1,
        max_length=60,
        description="Batocera system (ROM folder) name, e.g. 'mame', 'fbneo', 'snes', 'nes', 'genesis', 'n64'.",
    ),
]
Rom = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        description="ROM file name without its extension, e.g. 'pacman' or 'Super Mario World (USA)'.",
    ),
]
FallbackText = Annotated[
    str | None, Field(default=None, max_length=200, description="Text to scroll if there is no artwork.")
]


def _pulse_flags(pulse: str) -> Query:
    return {"follow": [], "skip": [("nostrip", None)], "off": [("clearstrip", None)]}[pulse]


@mcp.tool(title="Show a game marquee", annotations=_SET)
async def show_game_marquee(
    system: System,
    rom: Rom,
    animation: Annotated[
        Literal["auto", "still", "animated"],
        Field(description="auto: animated then still if both exist; still: PNG only; animated: GIF only."),
    ] = "auto",
    loops: Annotated[
        int | None,
        Field(
            default=None,
            ge=1,
            le=600,
            description="Queue instead of replacing: play a GIF this many times (or show a still this many "
            "seconds), then move to the next queued item. Omit to show it now and keep it up.",
        ),
    ] = None,
    fallback_text: FallbackText = None,
    pulse: Annotated[
        Literal["follow", "skip", "off"],
        Field(description="Pulse LED strips: follow the game's theme, leave them as they are, or turn them off."),
    ] = "follow",
) -> ActionResult:
    """Show a game's marquee artwork, the way the frontend does when you scroll to that game.

    If the game has no artwork, Pixelcade shows fallback_text if given, otherwise a default marquee.
    """
    s = _check(cfg.path_segment, system, "system", 60)
    r = _check(cfg.path_segment, rom, "rom")
    query: list[tuple[str, str | int | None]] = []
    if animation == "still":
        query.append(("nogif", None))
    elif animation == "animated":
        query.append(("gifonly", None))
    if fallback_text:
        query.append(("t", _check(_one_line, fallback_text)))
    if loops is not None:
        # Pixelcade's docs: queued items sent from scripts carry event=FEScroll.
        query += [("l", loops), ("event", "FEScroll")]  # ASSUMPTION PX-QUEUE
    query += _pulse_flags(pulse)
    verb = f"Queued {s}/{r} for {loops}" if loops else f"Showing {s}/{r}"
    return await _send(["arcade", "stream", s, r], query, verb)


@mcp.tool(title="Show a system marquee", annotations=_SET)
async def show_console_marquee(system: System, fallback_text: FallbackText = None) -> ActionResult:
    """Show a system/console marquee (e.g. 'snes', 'genesis', 'mame'), as when browsing the system list."""
    s = _check(cfg.path_segment, system, "system", 60)
    query: Query = [("t", _check(_one_line, fallback_text))] if fallback_text else []
    return await _send(["console", "stream", s], query, f"Showing the {s} system marquee")


@mcp.tool(title="Restore the frontend's marquee", annotations=_SET)
async def show_current_game() -> ActionResult:
    """Put back the marquee for the game currently selected in the arcade frontend, e.g. after showing text."""
    c = client()
    game = parse_current_game(await c.get(["currentgame"]))
    if game is None:
        raise PixelcadeError(
            "No game has been selected in the frontend since pixelweb started, so there is nothing to restore."
        )
    return await _send(["arcade", "stream", game["system"], game["rom"]], [], f"Showing {game['system']}/{game['rom']}")


def _one_line(text: str) -> str:
    if cfg.CONTROL_CHARS.search(text):
        raise cfg.InvalidValue("Text can't contain control characters.")
    return text.strip()


# ASSUMPTION PX-TEXT: line breaks are the literal two characters \\n.
def _marquee_text(text: str) -> str:
    """Real newlines -> the literal two characters backslash-n, which is how pixelweb takes line breaks."""
    lines = text.replace("\r\n", "\n").split("\n")
    return "\\n".join(_one_line(line) for line in lines)


Color = Annotated[
    str | None,
    Field(
        default=None,
        max_length=7,
        description="A color name (red, blue, cyan, gray, darkgray, green, lightgray, magenta, orange, yellow, "
        "pink, white) or 6-digit hex like 'ff6d00'.",
    ),
]


@mcp.tool(title="Show text on the marquee", annotations=_SET)
async def show_text(
    text: Annotated[
        str, Field(min_length=1, max_length=500, description="The message. Use newlines with lines=2 or 4.")
    ],
    color: Color = None,
    speed: Annotated[
        int | None, Field(default=None, ge=1, le=150, description="Scroll speed, 1-150; lower is faster.")
    ] = None,
    loops: Annotated[
        int | None,
        Field(
            default=None,
            ge=-1,
            le=100,
            description="Scroll this many times, then return to what was showing before; -1 keeps scrolling "
            "until something else is shown. Omit for Pixelcade's default.",
        ),
    ] = None,
    scroll: Annotated[
        Literal["auto", "scroll", "fixed"],
        Field(description="auto: scroll only if it doesn't fit; scroll: always; fixed: never (auto-sized)."),
    ] = "auto",
    lines: Annotated[Literal[1, 2, 4], Field(description="Text rows; 2 or 4 use smaller built-in fonts.")] = 1,
    blink_ms: Annotated[
        int | None,
        Field(default=None, ge=100, le=5000, description="Blink interval in ms. Only for 9 characters or fewer."),
    ] = None,
    left_justify: bool = False,
) -> ActionResult:
    """Show a message on the marquee, e.g. 'GAME NIGHT 7PM' or a high score."""
    t = _check(_marquee_text, text)
    if not t.replace("\\n", "").strip():
        raise PixelcadeError("The text is blank.")
    if blink_ms is not None and len(t) > 9:
        raise PixelcadeError("Blinking only works for text of 9 characters or fewer (text that doesn't scroll).")
    query: list[tuple[str, str | int | None]] = [("t", t)]
    if color:
        query.append(("c", _check(cfg.text_color, color)))
    if speed is not None:
        query.append(("s", speed))
    if loops is not None:
        query.append(("l", loops))
    if scroll != "auto":
        query.append(("scroll", "true" if scroll == "scroll" else "false"))
    if lines != 1:
        query.append(("lines", lines))
    if blink_ms is not None:
        query.append(("blink", blink_ms))
    if left_justify:
        query.append(("left", "true"))
    return await _send(["text"], query, "Showing the text")


@mcp.tool(title="Play an animation", annotations=_ACT)
async def play_animation(
    name: Annotated[str, Field(min_length=1, max_length=100, description="An animation name from list_artwork.")],
    loops: Annotated[
        int | None,
        Field(default=None, ge=1, le=50, description="Queue it and play this many times; omit to play now."),
    ] = None,
) -> ActionResult:
    """Play one of Pixelcade's built-in animations on the marquee (see list_artwork kind='animations')."""
    n = _check(cfg.path_segment, name, "animation name", 100)
    query: Query = [("l", loops)] if loops else []
    return await _send(["animations", "stream", n], query, f"Playing animation {n}")


@mcp.tool(title="Show a widget", annotations=_SET)
async def show_widget(
    widget: Annotated[
        Literal["clock", "weather", "stocks", "sports", "attract"],
        Field(description="attract runs the user's Pixelcade attract-mode playlist."),
    ],
    clock_style: Annotated[ClockStyle, Field(description="For clock: 'digital' or an arcade theme.")] = "digital",
    twelve_hour: Annotated[bool, Field(description="For clock: 12-hour format.")] = True,
    location: Annotated[
        str | None,
        Field(
            default=None,
            max_length=80,
            description="For weather: ZIP code or 'City,Country'. Omit for the location saved in Pixelcade.",
        ),
    ] = None,
    tickers: Annotated[
        str | None, Field(default=None, max_length=200, description="For stocks: comma-separated, e.g. 'AAPL,BTC-USD'.")
    ] = None,
    league: Annotated[SportsLeague | None, Field(description="For sports: omit for all leagues.")] = None,
    teams: Annotated[
        str | None,
        Field(default=None, max_length=200, description="For sports: comma-separated team codes, e.g. 'SF,GB'."),
    ] = None,
    lock: Annotated[
        bool,
        Field(description="Keep it up until stop_widgets (or a game launch), ignoring frontend scrolling."),
    ] = False,
) -> ActionResult:
    """Show a live widget on the marquee: a clock (optionally arcade-themed), weather, a stock ticker,
    sports scores, or the attract-mode playlist. Widgets keep running until replaced; stop_widgets ends them.
    """
    extras = {"location": location, "tickers": tickers, "league": league, "teams": teams}
    allowed = {"weather": {"location"}, "stocks": {"tickers"}, "sports": {"league", "teams"}}.get(widget, set())
    stray = sorted(k for k, v in extras.items() if v is not None and k not in allowed)
    if stray:
        raise PixelcadeError(f"{', '.join(stray)} doesn't apply to the {widget} widget.")

    # ASSUMPTION PX-WIDGETS
    query: list[tuple[str, str | int | None]] = [("nointerrupt", None)] if lock else []
    path: list[str] = [widget]
    if widget == "clock":
        if clock_style != "digital":
            query.append(("clockType", clock_style))
        query.append(("12h", "true" if twelve_hour else "false"))
    elif widget == "weather" and location:
        query.append(("location", _check(_one_line, location)))
    elif widget == "stocks" and tickers:
        query.append(("tickers", _check(cfg.tickers, tickers)))
    elif widget == "sports":
        if league:
            path.append(league)
        if teams:
            query.append(("teams", _check(cfg.teams, teams)))
    return await _send(path, query, f"Showing the {widget} widget")


@mcp.tool(title="Stop widgets", annotations=_SET)
async def stop_widgets() -> ActionResult:
    """Stop any running widget or attract mode and return the marquee to normal arcade mode."""
    return await _send(["attract", "stop"], [], "Stopped widgets")


@mcp.tool(title="Blank the marquee", annotations=_SET)
async def blank_display() -> ActionResult:
    """Turn the marquee black (it stays on; the next marquee or text replaces the black screen)."""
    return await _send(["arcade", "stream", "black", "dummy"], [], "The marquee is blank")


# --- Pulse LED strips, rings and matrices ------------------------------------
Zone = Annotated[
    str,
    Field(
        min_length=1,
        max_length=20,
        description="A zone name (left_strip, right_strip, left_speaker_ring, right_speaker_ring, center_matrix, "
        "left_matrix, right_matrix) or a zone number 0-35.",
    ),
]


# ASSUMPTION PX-POWER: why there's a cap at all.
def _within_cap(value: int, what: str) -> None:
    cap = client().settings.pulse_max_brightness
    if value > cap:
        raise PixelcadeError(
            f"{what} {value} is above this server's limit of {cap}, which protects the V2 board from powering "
            "too many LEDs. The user can raise it with PIXELCADE_PULSE_MAX_BRIGHTNESS once the strips have their "
            "own 5V supply."
        )


@mcp.tool(title="Run a Pulse effect", annotations=_ACT)
async def pulse_effect(
    zone: Zone,
    effect: Annotated[
        str, Field(min_length=1, max_length=80, description="Effect name from list_pulse_effects, e.g. 'rainbow'.")
    ],
    color: Color = None,
    color2: Color = None,
    speed: Annotated[int | None, Field(default=None, ge=1, le=255, description="Effect speed 1-255.")] = None,
    intensity: Annotated[
        int | None, Field(default=None, ge=0, le=255, description="Effect brightness 0-255 (capped by the server).")
    ] = None,
    loops: Annotated[int | None, Field(default=None, ge=0, le=100, description="0 repeats forever.")] = None,
    duration_s: Annotated[int | None, Field(default=None, ge=1, le=3600, description="Stop after this long.")] = None,
    reverse: bool = False,
) -> ActionResult:
    """Run a lighting effect on one Pixelcade Pulse zone, e.g. 'rainbow' on left_strip or 'fire' on center_matrix.
    Colors are optional; many effects have their own palette.
    """
    z = _check(cfg.zone_id, zone)
    e = _check(cfg.effect_name, effect)
    query: list[tuple[str, str | int | None]] = []
    for prefix, value in (("", color), ("2", color2)):
        if value:
            r, g, b = _check(cfg.rgb, value)
            query += [(f"r{prefix}", r), (f"g{prefix}", g), (f"b{prefix}", b)]
    if speed is not None:
        query.append(("speed", speed))
    if intensity is not None:
        _within_cap(intensity, "Intensity")
        query.append(("intensity", intensity))
    if loops is not None:
        query.append(("loops", loops))
    if duration_s is not None:
        query.append(("duration", duration_s))
    if reverse:
        query.append(("reverse", "true"))
    return await _send(["ledstrip", "zone", z, "effect", e], query, f"Running {e} on {z}")


@mcp.tool(title="Fill a Pulse zone with a color", annotations=_SET)
async def pulse_fill(
    zone: Zone,
    color: Annotated[str, Field(min_length=3, max_length=9, description="A color name or 6-digit hex.")],
) -> ActionResult:
    """Set every LED in a Pulse zone to one solid color (stops any effect running there)."""
    z = _check(cfg.zone_id, zone)
    r, g, b = _check(cfg.rgb, color)
    return await _send(["ledstrip", "zone", z, "flood"], [("r", r), ("g", g), ("b", b)], f"Filled {z}")


@mcp.tool(title="Turn Pulse LEDs off", annotations=_SET)
async def pulse_clear(
    zone: Annotated[str | None, Field(default=None, max_length=20, description="One zone; omit for all LEDs.")] = None,
) -> ActionResult:
    """Turn off Pulse LEDs and stop their effects: one zone, or everything if no zone is given."""
    if zone is None:
        return await _send(["ledstrip", "clear"], [], "All Pulse LEDs are off")
    z = _check(cfg.zone_id, zone)
    return await _send(["ledstrip", z, "clear"], [], f"{z} is off")  # ASSUMPTION PX-ZONES: no zone/ segment


@mcp.tool(title="Set Pulse brightness", annotations=_SET)
async def set_pulse_brightness(
    level: Annotated[int, Field(ge=0, le=255, description="0-255, limited by the server's cap (see get_status).")],
) -> ActionResult:
    """Set the overall brightness of all Pulse LEDs."""
    _within_cap(level, "Brightness")
    return await _send(["ledstrip", "brightness", str(level)], [], f"Pulse brightness set to {level}")
