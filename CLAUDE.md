# CLAUDE.md

## What this is

`mcp-server-pixelcade`: an MCP server that drives a Pixelcade LED marquee (V2 board) and Pixelcade Pulse
lighting through **pixelweb**, Pixelcade's HTTP listener on the arcade host (Batocera, port 8080).
Sibling project to `mcp-server-onkyo` and `mcp-server-shieldtv`, and follows the same conventions.

**This is a learning project.** The owner wants to understand how MCP servers are built, so explain the
reasoning behind non-obvious changes instead of only making them.

## Layout

- `src/pixelcade_mcp/config.py`: settings (`PIXELCADE_URL`, brightness cap), vocabularies (colors, zones,
  clock styles, leagues) and the validators that keep arguments from becoming other URLs
- `src/pixelcade_mcp/client.py`: `PixelcadeClient` (one httpx client), hand-built URLs, response parsers,
  `PixelcadeError`
- `src/pixelcade_mcp/server.py`: MCP tools (`MCPServer` from `mcp` 2.x)
- `src/pixelcade_mcp/effects.py`: Pulse effect catalog (reference data, not an allow-list)
- `src/pixelcade_mcp/cli.py`: `serve` (default) and `check`
- `tests/`: `conftest.py` (`FakePixelweb` on `httpx.MockTransport`, records raw paths), `test_config`,
  `test_client`, `test_tools` (in-process MCP `Client`), `test_stdio` (installed entry point).
  Async tests use anyio's plugin, not pytest-asyncio.

## Rules for changes

- **Paths are built only in `server.py`, from constants plus validated segments.** Never let an argument
  choose an endpoint. `/quit`, `/reboot`, `/shutdown`, `/arcade/write`, `/upload*`, `/settings`,
  `/update/*`, `/ledstrip/numpixels`, OAuth and Vestaboard stay out unless the owner decides otherwise;
  update README and tests together if that changes.
- Every model-supplied path value goes through `config.path_segment` (or a stricter validator) and is
  percent-encoded with `quote(safe="")` by `client.build_path`.
- Pulse brightness and effect intensity respect `Settings.pulse_max_brightness` (default 128): the V2
  board can only power ~40 LEDs itself.
- Raise `PixelcadeError` (a `ToolError`) for anything the model or user can act on.
- Log to **stderr only**. stdout is the MCP stdio transport.
- Every tool has a `title`, explicit `ToolAnnotations`, and constrained args via
  `Literal`/`Annotated[..., Field(...)]`. `test_tools.py` enforces this.

## pixelweb facts the code depends on

From pixelcade.org/developers and Pixelcade's Batocera hook scripts (github.com/alinke/pixelcade-linux,
`batocera/scripts/`); **not yet verified on hardware**:
- All control is GET. Flags are bare (`?nogif`, `?nointerrupt`, `?cycle`); the hook scripts encode
  spaces as `%20` (RFC 3986 unreserved characters only), so URLs are built by hand, not with `params=`.
- `/currentgame` returns `mame,digdug` per the hook script (docs say `console%game-name`); both parse.
- Text line breaks are the literal two characters `\n` inside `t=`.
- Queueing: `l=N` (N >= 1) queues; a call without `l` interrupts and clears the queue. Scripted queue
  items carry `event=FEScroll`.
- Zone clear is `/ledstrip/{zone}/clear` (no `zone/` segment), unlike effect and flood.
- EmulationStation hooks call `/arcade/stream/...?event=FEScroll` on scroll and `/attract/stop` on every
  game launch, so anything we show is temporary.

Unknowns to confirm on the real board: the response format of `/arcade/list`, `/still/list` and
`/animations/list` (parsed tolerantly), status codes for missing artwork, and whether `/news` exists.

## Commands

```sh
pip install -e ".[dev]" && pytest && ruff check . && ruff format --check . && mypy
mcp-server-pixelcade check | (serve)
```
