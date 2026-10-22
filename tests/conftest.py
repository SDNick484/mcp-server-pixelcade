"""Shared fixtures. Tests talk to the wire-level fake pixelweb (sim/fake_pixelweb.py) over real HTTP.

A few client tests need failures a real socket can't produce on demand (DNS errors); those use
`mock`, an httpx.MockTransport stand-in.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from pixelcade_mcp import client as client_mod
from pixelcade_mcp.config import Settings
from pixelcade_mcp.sim.fake_pixelweb import FakePixelweb

# What a real V2 LED marquee's /info looks like (trimmed from Pixelcade's API reference).
INFO = {
    "name": "My Pixelcade LED",
    "type": "LED",
    "hostname": "batocera",
    "firmwareVersion": "PDXR0023",
    "hardwareID": "PIXL0020",
    "v2board": True,
    "softwareVersion": "6.5.9",
    "resolution": "128x32",
    "bindPort": 8080,
    "hasLEDStripSupport": True,
    "ledStrip": True,
    "ledStripBrightness": 12,
    "ledStripNumPixels": 90,
}


# Async tests use anyio's pytest plugin (marked with pytest.mark.anyio) rather
# than pytest-asyncio: the MCP SDK's in-process Client is built on anyio.
@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class MockPixelweb:
    """Stands in for pixelweb behind an httpx.MockTransport, for failures that need injecting.

    It records each request's *raw* path (exactly what went on the wire, with
    its percent-encoding), because the encoding is the part most likely to be
    wrong and the part pixelweb is most sensitive to.
    """

    def __init__(self) -> None:
        self.requests: list[str] = []
        # Exact path (no query) -> (status, body). Unlisted paths answer 200 "OK".
        self.routes: dict[str, tuple[int, str]] = {
            "/info": (200, json.dumps(INFO)),
            "/currentgame": (200, "mame,digdug"),
            "/ledstrip/getbrightness": (200, json.dumps({"brightness": 64, "percentage": 25, "success": True})),
        }
        self.error: Callable[[httpx.Request], Exception] | None = None
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.error is not None:
            raise self.error(request)
        raw = request.url.raw_path.decode()
        self.requests.append(raw)
        status, body = self.routes.get(raw.split("?")[0], (200, "OK"))
        return httpx.Response(status, text=body)

    @property
    def last(self) -> str:
        return self.requests[-1]


@pytest.fixture
def mock() -> MockPixelweb:
    return MockPixelweb()


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(url="http://arcade.test:8080", timeout_s=2.0, pulse_max_brightness=128)


@pytest.fixture
def fake():
    with FakePixelweb() as f:
        yield f


@pytest.fixture
def settings(fake) -> Settings:
    return Settings(url=fake.url, timeout_s=2.0, pulse_max_brightness=128, name="Test marquee")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Never read the user's real config or environment; keep the Pulse wait short."""
    monkeypatch.setattr(client_mod, "PULSE_STATUS_TIMEOUT_S", 0.3)
    monkeypatch.setenv("PIXELCADE_CONFIG_DIR", str(tmp_path))
    for var in (
        "PIXELCADE_URL",
        "PIXELCADE_PULSE_MAX_BRIGHTNESS",
        "PIXELCADE_TIMEOUT",
        "PIXELCADE_DRY_RUN",
        "PIXELCADE_NAME",
    ):
        monkeypatch.delenv(var, raising=False)
