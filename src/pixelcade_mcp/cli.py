"""Entry point: `mcp-server-pixelcade` (serve over stdio) and `mcp-server-pixelcade check`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__
from .client import PixelcadeClient
from .config import load_settings


def _setup_logging() -> None:
    # stdout belongs to the MCP stdio transport; logs must go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs every request at INFO, duplicating our own "GET /path" lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _cmd_check() -> int:
    """Ask pixelweb for /info, so setup problems show up before an MCP client is involved."""
    settings = load_settings()
    client = PixelcadeClient(settings)
    try:
        status = await client.status()
    finally:
        await client.aclose()
    print(f"pixelweb URL:   {status['url']}")
    if not status["reachable"]:
        print(f"Not reachable:  {status['error']}")
        return 1
    print(f"Display:        {status['name'] or '?'} ({status['type'] or '?'}, {status['resolution'] or '?'})")
    print(f"V2 board:       {status['v2_board']}   firmware {status['firmware']}")
    print(f"Pixelcade:      {status['software_version']}")
    game = status["current_game"]
    print(f"Selected game:  {game['system'] + '/' + game['rom'] if game else '(none yet)'}")
    pulse = status["pulse"]
    print(
        f"Pulse strips:   enabled={pulse['enabled']} pixels={pulse['pixels']} "
        f"brightness={pulse['brightness']} (server cap {pulse['max_brightness']})"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp-server-pixelcade", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the MCP server over stdio (default)")
    sub.add_parser("check", help="check that pixelweb answers, and print what it reports")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    if args.cmd == "check":
        sys.exit(asyncio.run(_cmd_check()))

    _setup_logging()
    from .server import mcp  # imported late: `check` doesn't need the server code

    mcp.run()
