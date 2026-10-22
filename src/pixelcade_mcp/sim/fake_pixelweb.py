"""A wire-level fake pixelweb: Pixelcade's HTTP listener, over real HTTP.

Used by the tests and by ``mcp-server-pixelcade simulate``. It answers the
endpoints the server uses the way the documentation and Batocera's hook scripts
say pixelweb does, each behavior tagged with its assumption (assumptions.py):

  - every control is a GET; flags are bare query words (PX-GET-API)
  - ``/info`` reports a V2 LED marquee with Pulse strips (PX-INFO)
  - ``/currentgame`` answers ``mame%digdug``, as pixelweb 8.0.17 does (PX-CURRENTGAME)
  - the documented /arcade/list, /still/list and /animations/list answer 404, as on 8.0.17 (PX-LISTS)
  - ``POST /api/images/list`` {"name": <folder>} lists a folder's files; a missing folder answers 400,
    an empty name ``null`` (PX-IMAGES-LIST)
  - other controls answer 200 with a short body, unknown paths 404 (PX-REPLIES: a guess)
  - ``/ledstrip/brightness/<n>`` changes what ``/ledstrip/getbrightness`` reports (PX-BRIGHTNESS);
    ``hang`` makes a path never answer, as getbrightness does with no strips connected
  - ``?nointerrupt`` locks the marquee until ``/attract/stop``: other display calls answer 200 but
    change nothing, and Pulse calls aren't locked (PX-ATTRACT-LOCK). ``screensaver()`` does what
    Pixelcade's Batocera screensaver-start hook does.
  - ``/defaultmarquee`` exists, as in pixelweb 8.0.14+ (PX-DEFAULT-MARQUEE); route it to 404 for an older one

``showing`` is the display call the marquee is showing now (None after a stop), which is
how tests tell an ignored call from one that took effect.

It records each request's *raw* path, percent-encoding and all, because the
encoding is what pixelweb is most sensitive to. ``routes`` overrides any path's
answer; ``slow_s`` and ``drop`` simulate a busy or failing listener. Standard
library only, with threads, so it runs under any event loop or none.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote

INFO: dict[str, Any] = {
    "name": "Simulated Pixelcade",
    "type": "LED",
    "hostname": "batocera",
    "firmwareVersion": "PDXR0023",
    "hardwareID": "PIXL0020",
    "v2board": True,
    "softwareVersion": "8.0.17-sim",
    "noAnimatedMarquees": False,
    "resolution": "128x32",
    "bindPort": 8080,
    "hasLEDStripSupport": True,
    "ledStrip": True,
    "ledStripBrightness": 12,
    "ledStripNumPixels": 90,
}

CONTROL_PREFIXES = (
    "/arcade/stream/",
    "/console/stream/",
    "/text",
    "/animations/stream/",
    "/clock",
    "/weather",
    "/stocks",
    "/sports",
    "/attract",
    "/defaultmarquee",
    "/ledstrip/",
)
STOPS = ("/attract/stop", "/widgets/stop")
# ASSUMPTION PX-IMAGES-LIST: what /userdata/system/pixelcade/<folder> holds, as /api/images/list sees it.
# Mirrors what the real one showed: PNG and GIF side by side, full titles next to MAME names, _<n> variants.
ROOT = "/userdata/system/pixelcade"
FOLDERS: dict[str, list[str]] = {
    "mame": ["pacman.png", "pacman.gif", "mspacman.png", "galaga.png", "Galaga (Midway, Set 1).png", "digdug.png",
             "digdug.gif", "digdug1.png", "dkong.png", "sf2.png", "1942.png", "pacland.png", "pacland_2.png"],
    "snes": ["Super Mario World (USA).png", "Super Mario World 2 - Yoshi's Island (USA) (Rev 1).png"],
    "console": ["mame.png", "snes.png", "nes.png", "- Arcade.png"],
    "animations": ["0rain.png", "explosion.png", "fireworks.png", "pacghosts.png"],
    "user": ["custom-logo.gif"],
}  # fmt: skip
# ASSUMPTION PX-LISTS: the documented list endpoints. pixelweb 8.0.17 answers 404 to all three.
REMOVED_LISTS = ("/arcade/list", "/still/list", "/animations/list")


class _QuietHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # the drop fault closes sockets mid-request on purpose


class FakePixelweb:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.info = dict(INFO)
        self.current_game = "mame%digdug"
        self.brightness = 64
        self.routes: dict[str, tuple[int, str]] = {}  # raw path (no query) -> (status, body)
        self.slow_s = 0.0
        self.hang: set[str] = set()  # raw paths (no query) that never answer, e.g. Pulse with no strips
        self.drop = False
        self.requests: list[str] = []
        self.posts: list[tuple[str, str]] = []  # (raw path, body) of each POST
        self.folders = {k: list(v) for k, v in FOLDERS.items()}
        self.showing: str | None = None
        self.locked = False
        self.on_request: Callable[[str], None] | None = None  # simulate prints what the marquee shows
        self._lock = threading.Lock()
        self._http = _QuietHTTPServer((host, port), self._handler())
        self.host = host

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._http.server_address[1]}"

    @property
    def last(self) -> str:
        return self.requests[-1]

    def start(self) -> FakePixelweb:
        threading.Thread(target=self._http.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        return self

    def stop(self) -> None:
        self._http.shutdown()
        self._http.server_close()

    def __enter__(self) -> FakePixelweb:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def screensaver(self) -> None:
        """What Pixelcade's Batocera screensaver-start hook does: GET /attract?nointerrupt."""
        self.handle("/attract?nointerrupt")

    def _display(self, raw: str, path: str) -> None:
        """Track what the marquee shows, honoring the attract lock (PX-ATTRACT-LOCK)."""
        if path.startswith("/ledstrip/"):
            return
        if path in STOPS:
            self.showing, self.locked = None, False
        elif not self.locked:
            self.showing = raw
            self.locked = "nointerrupt" in raw.partition("?")[2].split("&")

    def handle(self, raw: str) -> tuple[int, str, str]:
        """(status, content type, body) for one GET, as pixelweb answers it."""
        with self._lock:
            self.requests.append(raw)
        if self.on_request:
            self.on_request(raw)
        path = raw.split("?", 1)[0]
        if path in self.routes:
            status, body = self.routes[path]
            return status, "text/plain", body
        if path == "/info":
            return 200, "application/json", json.dumps({**self.info, "ledStripBrightness": self.brightness})
        if path == "/currentgame":
            return 200, "text/plain", self.current_game
        if path == "/ledstrip/getbrightness":
            pct = round(self.brightness * 100 / 255)
            return (
                200,
                "application/json",
                json.dumps({"brightness": self.brightness, "percentage": pct, "success": True}),
            )
        if path.startswith("/ledstrip/brightness/"):
            try:
                self.brightness = max(0, min(255, int(unquote(path.rsplit("/", 1)[-1]))))
            except ValueError:
                return 400, "text/plain", "bad brightness"
            return 200, "text/plain", "OK"
        if path.startswith(CONTROL_PREFIXES) or path == "/widgets/stop":
            with self._lock:
                self._display(raw, path)
            return 200, "text/plain", "OK"
        return 404, "text/plain", "404 page not found"

    def handle_post(self, raw: str, body: bytes) -> tuple[int, str, str]:
        """(status, content type, body) for one POST: only the artwork browser's list."""
        with self._lock:
            self.requests.append(raw)
            self.posts.append((raw, body.decode(errors="replace")))
        if raw.split("?", 1)[0] != "/api/images/list":
            return 404, "text/plain", "404 page not found"
        try:
            name = json.loads(body or b"{}").get("name", "")
        except (json.JSONDecodeError, AttributeError):
            return 400, "text/plain; charset=utf-8", "bad request"
        if not name:
            return 200, "application/json", "null"
        if name not in self.folders:
            return 400, "text/plain; charset=utf-8", f"open {ROOT}/{name}: no such file or directory"
        entries = [
            {"name": f, "system": name, "urlPath": f"/images/{name}/{f}", "imagePath": f"{ROOT}/{name}/{f}"}
            for f in self.folders[name]
        ]
        return 200, "application/json", json.dumps(entries, indent=1)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
                self._answer(None)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                self._answer(self.rfile.read(length) if length else b"")

            def _answer(self, body: bytes | None) -> None:
                if fake.slow_s:
                    time.sleep(fake.slow_s)
                if self.path.split("?", 1)[0] in fake.hang:
                    # ASSUMPTION PX-BRIGHTNESS: seen on hardware, /ledstrip/getbrightness never answers when
                    # strips are configured but not connected. Hold the request until the client gives up.
                    time.sleep(30)
                    return
                if fake.drop:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                status, ctype, text = fake.handle(self.path) if body is None else fake.handle_post(self.path, body)
                data = text.encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler
