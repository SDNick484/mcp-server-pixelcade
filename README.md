# mcp-server-pixelcade

An [MCP](https://modelcontextprotocol.io) server for **[Pixelcade](https://pixelcade.org) LED marquees**
and **Pixelcade Pulse** lighting (WS2812B strips, speaker rings and matrices on a V2 board). It lets an
MCP client such as Claude show game and system marquees, scroll text, run arcade-themed clocks and other
widgets, and drive Pulse lighting effects.

> **Status: early, tested on hardware except Pulse.** On a real V2 board (pixelweb 8.0.17, Batocera 43)
> every marquee, text, widget and frontend tool has been checked, plus dry run; Pulse waits for strips. See
> [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) and the [verification table](#verification-status).

This is not an official Pixelcade project.

## How it works

The Pixelcade installer runs **pixelweb**, a small HTTP listener, on the arcade host (on Batocera:
`/userdata/system/pixelcade/pixelweb`, port 8080, started from `/userdata/system/custom.sh`). Pixelweb
drives the marquee over USB and exposes a [REST API](https://pixelcade.org/developers). This server is a
typed, allow-listed client for that API, so it can run anywhere that can reach the arcade on TCP 8080:
your desktop, a laptop, or a homelab VM. Clients reach it over stdio, or over Streamable HTTP as a
service behind Cloudflare Access ([Service and Cloudflare](#service-and-cloudflare)).

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
PIXELCADE_URL=192.168.1.20 mcp-server-pixelcade doctor
```

`doctor` checks each layer (TCP, `/info`, the frontend's current game, Pulse) and, when one fails, says
what to try and which [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) step covers it. It changes nothing
on the marquee. It ends with the [assumptions](#verification-status) that hardware contradicted (listed; the code
no longer relies on them) and a count of those not yet confirmed; `doctor --verbose` lists those too, and
`doctor --json` always includes both lists.

### Trying it without the arcade

```sh
mcp-server-pixelcade simulate --write-config /tmp/pxsim     # a fake pixelweb; prints every request it gets
export PIXELCADE_CONFIG_DIR=/tmp/pxsim                      # in another terminal
mcp-server-pixelcade doctor
mcp-server-pixelcade call show_text text="GAME NIGHT" color=cyan   # one tool call, as the model makes it
mcp-server-pixelcade call --dry-run blank_display                  # reports the GET it would send
```

### On Alpine (e.g. a Proxmox LXC)

`deploy/alpine/` installs it on an always-on Alpine machine on the same LAN as the arcade, in the
sibling servers' `deploy/alpine/` layout so they can share an LXC: a venv in `/opt/mcp-server-pixelcade`,
a `mcp-pixelcade` system user, every setting in `/etc/mcp-server-pixelcade/env`, an OpenRC service
(`/etc/init.d/mcp-server-pixelcade`), and `mcp-server-pixelcade` on `PATH`, which runs as that user with
those settings, for `doctor` and `call` from a shell.

```sh
git clone https://github.com/SDNick484/mcp-server-pixelcade.git && cd mcp-server-pixelcade
sh deploy/alpine/install.sh                                # as root; re-run to upgrade
vim /etc/mcp-server-pixelcade/env                          # PIXELCADE_URL=<the arcade's IP>
mcp-server-pixelcade doctor
mcp-server-pixelcade call show_text text="GAME NIGHT" color=cyan
rc-update add mcp-server-pixelcade default && rc-service mcp-server-pixelcade start
```

A variable set in your shell beats the env file, so one-off overrides work as usual
(`PIXELCADE_DRY_RUN=1 mcp-server-pixelcade call ...`). mcp-server-batocera's
`deploy/alpine/setup-arcade.sh` installs both arcade servers in a Proxmox container in one go, and
enables and restarts both services. CI runs `deploy/alpine/smoke-test.sh` in a stock
`python:3.12-alpine` container: install, upgrade, the test suite on musl, `doctor` and `call` through the
command on `PATH` against the simulator, and the OpenRC service (as its user, an MCP call over HTTP,
Access settings from the env file).

### Service and Cloudflare

The service runs `/opt/mcp-server-pixelcade/run.sh serve --http` under `supervise-daemon` (restarted if
it exits) as `mcp-pixelcade`, logging to `/var/log/mcp-server-pixelcade/server.log`. The HTTP transport
is `remote.py`, shared byte for byte with the sibling servers through
[mcp-server-shared](https://github.com/SDNick484/mcp-server-shared). Its settings are the `MCP_HTTP_*`,
`MCP_PUBLIC_HOSTS` and `CF_ACCESS_*` lines in `/etc/mcp-server-pixelcade/env`:

| Flag | Variable | Default |
| --- | --- | --- |
| `--bind` | `MCP_HTTP_BIND` | `127.0.0.1` |
| `--port` | `MCP_HTTP_PORT` | `8718` (the siblings use 8711 to 8716, Batocera 8717) |
| `--path` | `MCP_HTTP_PATH` | `/pixelcade/mcp` |
| `--public-host` | `MCP_PUBLIC_HOSTS` | none: only loopback Host headers pass |
| | `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` | none: serves only this machine |
| | `MCP_ALLOWED_EMAILS` | anyone Access lets in |

With Access configured, every request needs a valid Access assertion, checked here: signature (Access's
published keys), the application's audience tag, issuer, expiry, and the email allow-list if set; anything
else gets a plain 403. Without Access it serves only the machine it runs on: it won't listen on anything
but loopback, won't start with a `--public-host`, and answers 403 to anything a tunnel or proxy forwarded
(`--insecure-no-auth` turns that off for a short trusted-LAN test; don't run the service with it).

To use it as a connector, with cloudflared running on the same machine (keep the `127.0.0.1` bind):

1. **Tunnel route:** a public hostname, say `mcp.example.com`, path `pixelcade/*`, to
   `http://localhost:8718`. Several servers can share the hostname, one path each.
2. **Access application** (self-hosted) covering that hostname and path, with a policy for who may use it
   and Managed OAuth turned on, so Claude's connector can sign in through Access. Note its AUD tag.
3. **The env file:** `MCP_PUBLIC_HOSTS=mcp.example.com`, `CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com`,
   `CF_ACCESS_AUD=<the AUD tag>`, then `rc-service mcp-server-pixelcade restart`.
4. **The connector:** `https://mcp.example.com/pixelcade/mcp`.

`GET /healthz` answers without auth, for monitoring. Details are in `src/pixelcade_mcp/remote.py`.

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

Running as a service behind Cloudflare Access instead, add `https://<your host>/pixelcade/mcp` as a
connector ([Service and Cloudflare](#service-and-cloudflare)).

## Configuration

| Setting                          | Default                               | What it does                                                   |
| -------------------------------- | ------------------------------------- | -------------------------------------------------------------- |
| `PIXELCADE_URL`                  | `http://batocera.local:8080`          | Where pixelweb listens. A bare host or IP gets `http://` and `:8080` |
| `PIXELCADE_PULSE_MAX_BRIGHTNESS` | `128`                                 | Highest Pulse brightness/intensity the model may set (0-255)   |
| `PIXELCADE_TIMEOUT`              | `10`                                  | Seconds to wait for pixelweb (1-60)                            |
| `PIXELCADE_CONFIG_DIR`           | `$XDG_CONFIG_HOME/mcp-server-pixelcade` | Where an optional `config.json` lives                        |
| `PIXELCADE_NAME`                 | pixelweb's URL                        | How results name the marquee                                   |
| `PIXELCADE_DRY_RUN`              | off                                   | Read pixelweb, change nothing (`serve --dry-run`)              |
| `PIXELCADE_LOG_UNREDACTED`       | off                                   | Show LAN addresses in logs (`--no-redact`); secrets stay hidden |
| `PIXELCADE_DEBUG`                | off                                   | Debug logging (`--debug`)                                      |

`config.json` can hold the same settings as `url`, `pulse_max_brightness`, `timeout_s`, `name` and `dry_run`; environment
variables win. `.local` names need mDNS, which usually doesn't cross VLANs or work in WSL2 without
mirrored networking. A DHCP reservation plus the IP address is the dependable choice.

## Tools

| Tool                   | What it does                                                                                   |
| ---------------------- | ---------------------------------------------------------------------------------------------- |
| `get_status`           | Reachability, hardware/firmware/software versions, the frontend's selected game, Pulse state   |
| `list_artwork`         | The marquees in one folder (a system, `console`, `animations`, `user`): names, still/animated |
| `list_pulse_effects`   | Pulse effect names by category, per-game/per-system name patterns, and zone names              |
| `show_game_marquee`    | A game's marquee by system + ROM name; still/animated choice, optional queueing and fallback text |
| `show_console_marquee` | A system marquee, e.g. `snes`                                                                   |
| `show_current_game`    | Put back the marquee for the game selected in EmulationStation                                  |
| `show_default_marquee` | Pixelcade's default marquee (needs pixelweb 8.0.14+)                                            |
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
lasts until the next frontend event. `show_current_game` restores the frontend's marquee, and
`show_default_marquee` shows the default one.

**Locks.** `show_widget(lock=true)` sends `?nointerrupt`, which Pixelcade documents as preventing
interruption by *other API calls*, this server's included, until `stop_widgets` or a game launch.
Pixelcade's Batocera screensaver hooks do the same: the screensaver starts attract mode with
`?nointerrupt` and ends it with `/attract/stop`. So while the screensaver runs, marquee calls succeed
but change nothing (PX-ATTRACT-LOCK); `stop_widgets` releases the lock.

**Pixelcade 8.x.** Pixelweb 8 is mostly about Sidekick companion displays; the API this server uses is
unchanged. It adds `/defaultmarquee` (8.0.14, PX-DEFAULT-MARQUEE), and a companion-app setting to show
static PNG marquees only, which may override `show_game_marquee(animation="animated")`. `doctor` shows
the pixelweb version and notes when it's too old for `show_default_marquee`.

**Names:** `system` is the Batocera ROM folder name (`mame`, `fbneo`, `snes`, …) and `rom` is the file
name without its extension (`pacman`, `Super Mario World (USA)`), the same values Batocera's own hook
scripts send.

Every tool that changes the display returns the same structured result as the sibling servers:

```json
{ "pixelcade": "Arcade marquee", "outcome": "done", "detail": "Showing the text",
  "sent": ["GET /text?t=GAME%20NIGHT&c=cyan"], "warnings": [] }
```

`outcome` is `done` or `dry_run`; `sent` is the exact GET (or, in a dry run, the one it would send).

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
- **Pixelweb has no authentication**, so anything on your LAN can already call every endpoint. Keep port
  8080 off the internet. Over HTTP, this server is the gate: Access assertions are checked on every
  request, and without Access it serves only its own machine ([Service and Cloudflare](#service-and-cloudflare)).
- Tools carry titles and MCP annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`,
  `openWorldHint`). Nothing is marked destructive: every display change is undone by the next one.
- Errors are actionable ("pixelweb isn't answering at …", "Can't resolve batocera.local; set
  PIXELCADE_URL to the IP"), and logs go to stderr because stdout belongs to the MCP transport.

## Verification status

Every pixelweb behavior the code relies on is listed in `src/pixelcade_mcp/assumptions.py`, with its
source and how sure we are; the code cites each id where it depends on it, and
[HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) has the step that confirms it. *high* means documented
and used the same way by Pixelcade's own scripts; *medium* means documented only; *low* means our own
guess. Hardware results so far are from pixelweb 8.0.17 on a V2 board (2026-10-22).

| Assumption | Confidence | Status |
|---|---|---|
| `PX-GET-API` | high | hardware-verified |
| `PX-INFO` | medium | hardware-verified |
| `PX-CURRENTGAME` | medium | hardware-verified |
| `PX-LISTS` | low | hardware-contradicted |
| `PX-IMAGES-LIST` | high | hardware-verified |
| `PX-REPLIES` | low | hardware-verified |
| `PX-TEXT` | medium | hardware-verified |
| `PX-QUEUE` | medium | hardware-verified |
| `PX-HOLD` | medium | hardware-verified |
| `PX-FRONTEND` | high | hardware-verified |
| `PX-WIDGETS` | medium | hardware-verified |
| `PX-ATTRACT-LOCK` | medium | hardware-verified |
| `PX-DEFAULT-MARQUEE` | low | hardware-verified |
| `PX-ZONES` | medium | simulator-only |
| `PX-BRIGHTNESS` | medium | simulator-only |
| `PX-POWER` | high | simulator-only |

## Not yet included

- Pulse **batch zone control**, **MAX7219** 7-segment/dot-matrix displays, and **mini LCD** (`/ministats`)
  endpoints, which Pixelcade documents but this server doesn't wrap yet.
- Editing the attract-mode playlist, and OAuth-backed widgets (Spotify, Plex, YouTube, Google Calendar).
- Uploading custom artwork (`/upload`): useful, but it writes files on the arcade, so it should be opt-in.

## Development

```sh
pip install -e ".[dev]"
pytest              # validators, HTTP client, tools via an in-process MCP client, dry run, faults, CLI, stdio, HTTP + Access
ruff check . && ruff format --check .
mypy                # strict
```

Tests run against the wire-level fake pixelweb (`sim/fake_pixelweb.py`), which records each request's raw
path, so the exact percent-encoding on the wire is asserted. To poke at the tools interactively:
`npx @modelcontextprotocol/inspector mcp-server-pixelcade`.

## License

MIT. See [LICENSE](LICENSE).
