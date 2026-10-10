# mcp-server-pixelcade

An [MCP](https://modelcontextprotocol.io) server for **[Pixelcade](https://pixelcade.org) LED marquees**
and **Pixelcade Pulse** lighting (WS2812B strips, speaker rings and matrices on a V2 board). It lets an
MCP client such as Claude show game and system marquees, scroll text, run arcade-themed clocks and other
widgets, and drive Pulse lighting effects.

> **Status: early / untested on hardware.** Covered by unit tests against a fake pixelweb and an
> end-to-end stdio test, but not yet run against a real Pixelcade. Expect rough edges.

This is not an official Pixelcade project.

## How it works

The Pixelcade installer runs **pixelweb**, a small HTTP listener, on the arcade host (on Batocera:
`/userdata/system/pixelcade/pixelweb`, port 8080, started from `/userdata/system/custom.sh`). Pixelweb
drives the marquee over USB and exposes a [REST API](https://pixelcade.org/developers). This server is a
typed, allow-listed client for that API, so it can run anywhere that can reach the arcade on TCP 8080:
your desktop, a laptop, or a homelab VM.

```
MCP client ──stdio──▶ mcp-server-pixelcade ──HTTP :8080──▶ pixelweb (Batocera) ──USB──▶ V2 board ─▶ LED panels
                                                                                              └──▶ Pulse strips
```

## Install

```sh
git clone https://github.com/SDNick484/mcp-server-pixelcade.git
cd mcp-server-pixelcade
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

Requires Python 3.11+. Then check that pixelweb answers:

```sh
PIXELCADE_URL=batocera.local mcp-server-pixelcade check
```

```
pixelweb URL:   http://batocera.local:8080
Display:        My Pixelcade LED (LED, 128x32)
V2 board:       True   firmware PDXR0023
Pixelcade:      6.5.9
Selected game:  mame/digdug
Pulse strips:   enabled=False pixels=None brightness=None (server cap 128)
```

## Use it with an MCP client

```json
{
  "mcpServers": {
    "pixelcade": {
      "command": "/path/to/mcp-server-pixelcade/.venv/bin/mcp-server-pixelcade",
      "env": { "PIXELCADE_URL": "192.168.1.50" }
    }
  }
}
```

For Claude Code: `claude mcp add pixelcade -e PIXELCADE_URL=192.168.1.50 -- /path/to/.venv/bin/mcp-server-pixelcade`.

## Configuration

| Setting                          | Default                               | What it does                                                   |
| -------------------------------- | ------------------------------------- | -------------------------------------------------------------- |
| `PIXELCADE_URL`                  | `http://batocera.local:8080`          | Where pixelweb listens. A bare host or IP gets `http://` and `:8080` |
| `PIXELCADE_PULSE_MAX_BRIGHTNESS` | `128`                                 | Highest Pulse brightness/intensity the model may set (0-255)   |
| `PIXELCADE_TIMEOUT`              | `10`                                  | Seconds to wait for pixelweb (1-60)                            |
| `PIXELCADE_CONFIG_DIR`           | `$XDG_CONFIG_HOME/mcp-server-pixelcade` | Where an optional `config.json` lives                        |

`config.json` can hold the same settings as `url`, `pulse_max_brightness` and `timeout_s`; environment
variables win. `.local` names need mDNS, which usually doesn't cross VLANs or work in WSL2 without
mirrored networking. A DHCP reservation plus the IP address is the dependable choice.

## Tools

| Tool                   | What it does                                                                                   |
| ---------------------- | ---------------------------------------------------------------------------------------------- |
| `get_status`           | Reachability, hardware/firmware/software versions, the frontend's selected game, Pulse state   |
| `list_artwork`         | Arcade, still or animation names pixelweb has, with a substring filter                         |
| `list_pulse_effects`   | Pulse effect names by category, per-game/per-system name patterns, and zone names              |
| `show_game_marquee`    | A game's marquee by system + ROM name; still/animated choice, optional queueing and fallback text |
| `show_console_marquee` | A system marquee, e.g. `snes`                                                                   |
| `show_current_game`    | Put back the marquee for the game selected in EmulationStation                                  |
| `show_text`            | Scrolling or fixed text: color, speed, loops, 1/2/4 lines, blink, left-justify                  |
| `play_animation`       | One of Pixelcade's built-in animations                                                         |
| `show_widget`          | Clock (19 arcade themes), weather, stocks, sports scores, or the attract-mode playlist          |
| `stop_widgets`         | Stop widgets/attract mode and return to arcade mode                                            |
| `blank_display`        | Black screen                                                                                   |
| `pulse_effect`         | Run an effect on a Pulse zone (`rainbow`, `fire`, `mame_pacman`, …) with colors, speed, loops   |
| `pulse_fill`           | Solid color on a zone                                                                          |
| `pulse_clear`          | Turn off one zone, or all LEDs                                                                 |
| `set_pulse_brightness` | Overall Pulse brightness, up to the server's cap                                               |

**The frontend drives the marquee too.** EmulationStation's Pixelcade hooks call pixelweb whenever you
scroll to a system or game, and every game launch calls `/attract/stop`. So whatever the model shows
lasts until the next frontend event. `show_widget(lock=true)` keeps a widget up through scrolling (until
`stop_widgets` or a game launch), and `show_current_game` restores the frontend's marquee.

**Names:** `system` is the Batocera ROM folder name (`mame`, `fbneo`, `snes`, …) and `rom` is the file
name without its extension (`pacman`, `Super Mario World (USA)`), the same values Batocera's own hook
scripts send.

## Safety design

- **A fixed set of endpoints.** Paths are built in `server.py` from constants plus validated arguments.
  Pixelweb also serves `/quit`, `/reboot`, `/shutdown`, `/arcade/write` (writes the marquee's SD card),
  `/upload`, `/settings`, `/update/*` and `/ledstrip/numpixels`; none are reachable through this server.
- **Arguments can't become other URLs.** Each model-supplied path value must be a single segment (no
  `/`, `\`, `.`/`..` or control characters) and is then percent-encoded, `/` included. Zones, colors,
  tickers and team codes are validated; effect names must match a strict pattern.
- **A Pulse brightness cap**, 128 by default. Each WS2812B LED can draw 60 mA at full white, and Pixelcade
  warns that powering more than about 40 LEDs from the V2 board without an external 5V supply (with a
  common ground) can overload it. Raise `PIXELCADE_PULSE_MAX_BRIGHTNESS` once your strips have their own
  supply. The firmware's own thermal throttling (55/60/65 °C) is a backstop, not a plan.
- **No credentials to protect, and no new exposure.** Pixelweb has no authentication; anything on your
  LAN can already call every endpoint. Keep port 8080 off the internet.
- Tools carry titles and MCP annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`,
  `openWorldHint`). Nothing is marked destructive: every display change is undone by the next one.
- Errors are actionable ("pixelweb isn't answering at …", "Can't resolve batocera.local; set
  PIXELCADE_URL to the IP"), and logs go to stderr because stdout belongs to the MCP transport.

## Not yet included

- Pulse **batch zone control**, **MAX7219** 7-segment/dot-matrix displays, and **mini LCD** (`/ministats`)
  endpoints, which Pixelcade documents but this server doesn't wrap yet.
- Editing the attract-mode playlist, and OAuth-backed widgets (Spotify, Plex, YouTube, Google Calendar).
- Uploading custom artwork (`/upload`): useful, but it writes files on the arcade, so it should be opt-in.

## Development

```sh
pip install -e ".[dev]"
pytest              # config validators, HTTP client, in-process MCP contract, stdio end-to-end
ruff check . && ruff format --check .
mypy                # strict
```

Tests use an `httpx.MockTransport` fake pixelweb that records each request's raw path, so the exact
percent-encoding on the wire is asserted. To poke at the tools interactively:
`npx @modelcontextprotocol/inspector mcp-server-pixelcade`.

## License

MIT. See [LICENSE](LICENSE).
