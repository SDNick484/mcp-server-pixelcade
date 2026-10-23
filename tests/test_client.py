"""PixelcadeClient: URL building, response parsing, and errors the model can act on."""

from __future__ import annotations

import time

import httpx
import pytest

from pixelcade_mcp.client import (
    PixelcadeClient,
    PixelcadeError,
    build_path,
    parse_artwork,
    parse_current_game,
    parse_version,
)

pytestmark = pytest.mark.anyio


# --- URL building --------------------------------------------------------------
def test_build_path_matches_the_batocera_scripts():
    # Batocera's hook scripts send spaces as %20 and flags with no "=".
    assert (
        build_path(["arcade", "stream", "snes", "Super Mario World (USA)"], [("l", 99999), ("nogif", None)])
        == "/arcade/stream/snes/Super%20Mario%20World%20%28USA%29?l=99999&nogif"
    )


def test_build_path_cannot_add_segments():
    assert build_path(["arcade", "stream", "mame", "a/../../quit"]) == "/arcade/stream/mame/a%2F..%2F..%2Fquit"


def test_build_path_encodes_query_values():
    assert build_path(["text"], [("t", "A&B=C #1\\n2")]) == "/text?t=A%26B%3DC%20%231%5Cn2"


async def test_httpx_sends_the_path_unchanged(settings, fake):
    c = PixelcadeClient(settings)
    try:
        await c.get(["arcade", "stream", "mame", "a/b c"], [("t", "x y")])
    finally:
        await c.aclose()
    assert fake.last == "/arcade/stream/mame/a%2Fb%20c?t=x%20y"


# --- Parsing -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("mame,digdug", {"system": "mame", "rom": "digdug"}),
        ("snes,Super Mario World (USA)\n", {"system": "snes", "rom": "Super Mario World (USA)"}),
        ("mame%pacman", {"system": "mame", "rom": "pacman"}),
        # Whichever separator comes first wins, so commas and % inside ROM names survive.
        ("snes%Legend of Zelda, The (USA)", {"system": "snes", "rom": "Legend of Zelda, The (USA)"}),
        ("pc,100% Orange Juice", {"system": "pc", "rom": "100% Orange Juice"}),
        ("", None),
        ("mame,", {"system": "mame", "rom": None}),  # a system marquee
        ("snes%", {"system": "snes", "rom": None}),
        (",pacman", None),
    ],
)
def test_parse_current_game(body, expected):
    # ASSUMPTION PX-CURRENTGAME
    assert parse_current_game(body) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("8.0.17", (8, 0, 17)), ("7.8.5-sim", (7, 8, 5)), ("8", (8,)), ("", None), ("dev", None), (None, None)],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


def _entries(*files):
    return [{"name": f, "system": "mame", "urlPath": f"/images/mame/{f}", "imagePath": f"/x/mame/{f}"} for f in files]


def test_parse_artwork_merges_stills_animations_and_variants():
    # ASSUMPTION PX-IMAGES-LIST: one row per name, as pixelweb's own artwork browser shows them.
    rows = parse_artwork(_entries("digdug.png", "digdug.gif", "digdug1.png", "pacland_2.png", "Pacland.PNG",
                                  "readme.txt", "noext", "Galaga (Midway, Set 1).png"))  # fmt: skip
    assert rows == [
        {"name": "digdug", "still": True, "animated": True},
        {"name": "digdug1", "still": True, "animated": False},  # a different ROM, not a variant
        {"name": "Galaga (Midway, Set 1)", "still": True, "animated": False},
        {"name": "pacland", "still": True, "animated": False},  # pacland_2 and Pacland.PNG: one name
    ]


@pytest.mark.parametrize("data", [None, "null", {"error": "x"}, [1, "x", {"nope": 1}]])
def test_parse_artwork_tolerates_odd_answers(data):
    assert parse_artwork(data) == []


# --- Errors ------------------------------------------------------------------
async def _get(settings, transport=None):
    c = PixelcadeClient(settings, transport=transport)
    try:
        return await c.get(["text"], [("t", "hi")])
    finally:
        await c.aclose()


async def test_connection_refused_says_what_to_check(mock_settings, mock):
    mock.error = lambda r: httpx.ConnectError("[Errno 111] Connection refused", request=r)
    with pytest.raises(PixelcadeError, match="pixelweb isn't answering at http://arcade.test:8080"):
        await _get(mock_settings, mock.transport)


async def test_unresolvable_host_suggests_an_ip(mock_settings, mock):
    mock.error = lambda r: httpx.ConnectError("[Errno -2] Name or service not known", request=r)
    with pytest.raises(PixelcadeError, match="Can't resolve arcade.test.*PIXELCADE_URL"):
        await _get(mock_settings, mock.transport)


async def test_timeout(mock_settings, mock):
    mock.error = lambda r: httpx.ReadTimeout("timed out", request=r)
    with pytest.raises(PixelcadeError, match="didn't answer within 2s"):
        await _get(mock_settings, mock.transport)


async def test_missing_endpoint_suggests_updating(settings, fake):
    fake.routes["/text"] = (404, "404 page not found")
    with pytest.raises(PixelcadeError, match="no /text endpoint.*updating"):
        await _get(settings)


async def test_server_error_passes_the_reason_on(settings, fake):
    fake.routes["/text"] = (500, "Write in progress")
    with pytest.raises(PixelcadeError, match="500: Write in progress"):
        await _get(settings)


# --- status() ------------------------------------------------------------------
async def test_status(settings, fake):
    c = PixelcadeClient(settings)
    try:
        s = await c.status()
    finally:
        await c.aclose()
    assert s["reachable"] and s["v2_board"] and s["resolution"] == "128x32"
    assert s["current_game"] == {"system": "mame", "rom": "digdug"}
    # The board's live value beats /info's stored setting.
    assert s["pulse"] == {
        "supported": True, "enabled": True, "pixels": 90, "brightness": 64, "max_brightness": 128, "responding": True
    }  # fmt: skip
    assert s["animated_marquees"] is True


async def test_status_when_configured_strips_dont_answer(settings, fake):
    # ASSUMPTION PX-BRIGHTNESS: seen on hardware with strips configured but not connected: getbrightness
    # never answers. Status must not wait the full timeout, and falls back to /info's brightness setting.
    fake.hang.add("/ledstrip/getbrightness")
    fake.info["ledStripBrightness"] = 28
    fake.brightness = 28
    fake.info["noAnimatedMarquees"] = True
    c = PixelcadeClient(settings)
    try:
        start = time.monotonic()
        s = await c.status()
        elapsed = time.monotonic() - start
    finally:
        await c.aclose()
    assert elapsed < 1.5, elapsed  # the 0.3 s Pulse wait, not the 2 s client timeout
    assert (s["pulse"]["responding"], s["pulse"]["brightness"]) == (False, 28)
    assert s["animated_marquees"] is False


async def test_a_pulse_request_that_hangs_says_why(settings, fake):
    fake.hang.add("/ledstrip/zone/left_strip/flood")
    c = PixelcadeClient(settings)
    try:
        with pytest.raises(PixelcadeError, match="Pulse strips configured, but they may not be connected"):
            await c.get(["ledstrip", "zone", "left_strip", "flood"], timeout=0.3)
    finally:
        await c.aclose()


async def test_status_without_strips_skips_the_board_query(settings, fake):
    fake.routes["/info"] = (200, '{"type": "LED", "v2board": true, "ledStrip": false}')
    c = PixelcadeClient(settings)
    try:
        s = await c.status()
    finally:
        await c.aclose()
    assert s["pulse"]["enabled"] is False
    assert "/ledstrip/getbrightness" not in fake.requests


async def test_status_when_unreachable_reports_instead_of_raising(mock_settings, mock):
    mock.error = lambda r: httpx.ConnectError("Connection refused", request=r)
    c = PixelcadeClient(mock_settings, transport=mock.transport)
    try:
        s = await c.status()
    finally:
        await c.aclose()
    assert s["reachable"] is False
    assert "isn't answering" in (s["error"] or "")
    assert s["pulse"]["max_brightness"] == 128
