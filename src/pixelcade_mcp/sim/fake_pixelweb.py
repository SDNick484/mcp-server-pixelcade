"""A wire-level fake pixelweb: Pixelcade's HTTP listener, over real HTTP.

Used by the tests and by ``mcp-server-pixelcade simulate``. It answers the
endpoints the server uses the way the documentation and Batocera's hook scripts
say pixelweb does, each behavior tagged with its assumption (assumptions.py):

  - every control is a GET; flags are bare query words (PX-GET-API)
  - ``/info`` reports a V2 LED marquee with Pulse strips (PX-INFO)
  - ``/currentgame`` answers ``mame,digdug`` (PX-CURRENTGAME)
  - list endpoints answer a JSON array (PX-LISTS: a guess; the client accepts several formats)
  - other controls answer 200 with a short body, unknown paths 404 (PX-REPLIES: a guess)
  - ``/ledstrip/brightness/<n>`` changes what ``/ledstrip/getbrightness`` reports (PX-BRIGHTNESS)

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
    "softwareVersion": "7.8.5-sim",
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
    "/ledstrip/",
)
LISTS = {
    "/arcade/list": ["pacman", "mspacman", "galaga", "digdug", "dkong", "sf2", "1942", "pacland"],
    "/still/list": ["pixelcade", "batocera", "default-marquee"],
    "/animations/list": ["0rain", "explosion", "fireworks", "pacghosts"],
}


class _QuietHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # the drop fault closes sockets mid-request on purpose


class FakePixelweb:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.info = dict(INFO)
        self.current_game = "mame,digdug"
        self.brightness = 64
        self.routes: dict[str, tuple[int, str]] = {}  # raw path (no query) -> (status, body)
        self.slow_s = 0.0
        self.drop = False
        self.requests: list[str] = []
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
        if path in LISTS:
            return 200, "application/json", json.dumps(LISTS[path])
        if path.startswith("/ledstrip/brightness/"):
            try:
                self.brightness = max(0, min(255, int(unquote(path.rsplit("/", 1)[-1]))))
            except ValueError:
                return 400, "text/plain", "bad brightness"
            return 200, "text/plain", "OK"
        if path.startswith(CONTROL_PREFIXES):
            return 200, "text/plain", "OK"
        return 404, "text/plain", "404 page not found"

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
                if fake.slow_s:
                    time.sleep(fake.slow_s)
                if fake.drop:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                status, ctype, text = fake.handle(self.path)
                data = text.encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler
