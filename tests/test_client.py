"""PixelcadeClient: URL building, response parsing, and errors the model can act on."""

from __future__ import annotations

import httpx
import pytest

from pixelcade_mcp.client import (
    PixelcadeClient,
    PixelcadeError,
    build_path,
    parse_current_game,
    parse_name_list,
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
        ("mame,", None),
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


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('["pacman","galaga"]', ["pacman", "galaga"]),
        ('{"mame": ["pacman", "galaga"]}', ["pacman", "galaga"]),
        ('{"pacman": 1, "galaga": 2}', ["pacman", "galaga"]),
        ("pacman\ngalaga\n", ["pacman", "galaga"]),
        ("pacman,galaga", ["pacman", "galaga"]),
        ("", []),
    ],
)
def test_parse_name_list(body, expected):
    assert parse_name_list(body) == expected


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
    assert s["pulse"] == {"supported": True, "enabled": True, "pixels": 90, "brightness": 64, "max_brightness": 128}


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
