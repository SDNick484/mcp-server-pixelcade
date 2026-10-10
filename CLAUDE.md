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
- `src/pixelcade_mcp/server.py`: MCP tools (`MCPServer` from `mcp` 2.x); every action goes through `_send`,
  which honors dry run and returns an `ActionResult`
- `src/pixelcade_mcp/assumptions.py`: every pixelweb behavior relied on, by id, with source and status
- `src/pixelcade_mcp/doctor.py`: layer-by-layer checks; `STEP` maps layers to HARDWARE_VALIDATION.md steps
- `src/pixelcade_mcp/sim/fake_pixelweb.py`: wire-level fake pixelweb (real HTTP, raw paths recorded,
  `routes`/`slow_s`/`drop` faults); used by the tests and `simulate`
- `src/pixelcade_mcp/logsafe.py`: log redaction, **byte-identical** across the sibling servers
- `src/pixelcade_mcp/effects.py`: Pulse effect catalog (reference data, not an allow-list)
- `src/pixelcade_mcp/cli.py`: `serve` (default), `doctor` (`check`), `simulate`, `call`
- `tests/`: `conftest.py` (a `FakePixelweb` per test; `MockPixelweb` on `httpx.MockTransport` only for
  failures a socket can't produce, like DNS errors), `test_config`, `test_client`, `test_tools` (in-process
  MCP `Client`, dry run, faults), `test_cli`, `test_assumptions`, `test_logsafe`, `test_stdio`.
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
- **Actions go through `_send`** (dry run, then the GET, then an `ActionResult`: `pixelcade`, `outcome`,
  `detail`, `sent`, `warnings`). Don't call `client().get` from an action tool directly.
- **Don't invent pixelweb behavior.** Anything not seen on hardware goes in `assumptions.py`, is cited where
  the code relies on it (`# ASSUMPTION PX-...`), is implemented the same way in `fake_pixelweb.py`, gets a
  test, a HARDWARE_VALIDATION.md step and a README table row. `tests/test_assumptions.py` fails if they drift.
- `logsafe.py` is shared: change it in one sibling, copy it to the others.

## pixelweb facts the code depends on

The authoritative list, with ids and status, is `assumptions.py`; this is the readable summary.

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
mcp-server-pixelcade doctor [--json] | simulate [--write-config DIR] | call <tool> k=v [--dry-run] | (serve [--dry-run])
```

Real hardware: `doctor`, then HARDWARE_VALIDATION.md. Record results in `assumptions.py` and the README table.
