# Hardware validation (Pixelcade LED, V2 board)

Steps 1 to 4 have run on a real Pixelcade (V2 board, pixelweb 8.0.17, 2026-10-22); the rest hasn't yet.
The server was built from Pixelcade's API
reference and its Batocera hook scripts, and tested against a wire-level fake pixelweb
(`src/pixelcade_mcp/sim/fake_pixelweb.py`, real HTTP). Each step depends only on the ones before it and
names the assumptions it confirms (ids, claims and sources are in `src/pixelcade_mcp/assumptions.py`).

Plan on 30-40 minutes, plus step 6 once the Pulse strips and their power supply are in. `doctor` points
at steps 1, 5 and 6 by number, so keep the numbering if you edit this file.

## What's new and unconfirmed

| Feature | Status | Step |
| --- | --- | --- |
| `doctor` (TCP, /info, frontend game, Pulse) | verified on hardware (Pulse with strips not yet) | 1 |
| Game, system and animation marquees; `show_current_game` | verified on hardware | 2 |
| `show_text` options and queueing | verified on hardware | 3 |
| Widgets (themed clocks, weather, stocks, sports, attract) | verified on hardware (attract: step 5) | 4 |
| Interplay with the frontend's own marquee changes | verified against simulator only | 5 |
| The screensaver's attract lock; `stop_widgets` releasing it | verified against simulator only | 5 |
| `show_default_marquee` (pixelweb 8.0.14+) | verified on hardware | 2 |
| Pulse effects, fills, clearing, brightness cap | verified against simulator only | 6 |
| Dry run and ActionResult | verified against simulator only | 7 |
| Log redaction (shared logsafe.py) | verified on hardware | 1 |

## Before you start

- The arcade on, Batocera at the frontend, and its IP (192.168.1.20 at last check).
- A machine on the same LAN with this repo:

```sh
git clone https://github.com/SDNick484/mcp-server-pixelcade.git && cd mcp-server-pixelcade
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                          # expect: all passed
export PIXELCADE_URL=192.168.1.20
mkdir -p captures
```

**Or on an Alpine machine on the LAN** (e.g. a Proxmox LXC), installed with `deploy/alpine/` or
mcp-server-batocera's `setup-arcade.sh`: skip the clone, venv and `export`, set PIXELCADE_URL in
`/etc/mcp-server-pixelcade/env`, and run the `mcp-server-pixelcade ...` commands below as they are, as root over SSH.
Replace `curl` with `wget -qO-` there, or `apk add curl`.

**Tool calls** use `mcp-server-pixelcade call <tool> key=value ...`, through the same MCP layer the model
uses; `call tools` lists them. Every action's result includes `sent`, the exact GET that went to pixelweb,
which is what to compare when something looks wrong. Watch the marquee while you go.

To rehearse first: `mcp-server-pixelcade simulate --write-config /tmp/pxsim`, then in another terminal
`unset PIXELCADE_URL; export PIXELCADE_CONFIG_DIR=/tmp/pxsim`. The simulator prints every request it gets.

## 1. Connection and doctor

Confirms PX-INFO.

1. `mcp-server-pixelcade doctor`. Expect `ok` for config, tcp and info, with `LED 128x32, V2 board True`.
2. `curl -s http://192.168.1.20:8080/info > captures/info.json`. **Record** which of the fields in
   PX-INFO are present, and the pixelweb version. If doctor says it's older than 8.0.14, update
   Pixelcade before step 2.7, or skip that step.
3. `doctor --json > captures/doctor.json`: the address should read `x.x.x.20` (redaction).

## 2. Marquees

Confirms PX-GET-API, PX-CURRENTGAME, PX-REPLIES, PX-IMAGES-LIST, PX-DEFAULT-MARQUEE; PX-LISTS was
contradicted here (see 5).

1. `call show_game_marquee system=mame rom=galaga`: the Galaga marquee appears. **Record** the
   `(pixelweb: ...)` reply in `detail` (PX-REPLIES).
2. A ROM whose name has spaces and parentheses, from your SNES set:
   `call show_game_marquee system=snes rom="Super Mario World (USA)"`. The `sent` path encodes `(` as
   `%28`; **record** whether the right marquee appears (PX-GET-API).
3. `call show_game_marquee system=mame rom=nosuchgame fallback_text="NO ART"`: **record** what shows
   and the HTTP status (`curl -si ".../arcade/stream/mame/nosuchgame" | head -1`).
4. `call show_console_marquee system=snes`, then `call play_animation name=0rain` (or a name from step 5).
5. Artwork lists. The documented `/arcade/list`, `/still/list` and `/animations/list` answer 404 on
   pixelweb 8.0.17 (PX-LISTS, contradicted); pixelweb's own web page lists a folder with
   `POST /api/images/list` (PX-IMAGES-LIST):
   `curl -s -X POST -H Content-Type:application/json -d '{"name":"mame"}' http://192.168.1.20:8080/api/images/list | head -c 300`.
   Then `call list_artwork folder=mame contains=pac`, `folder=animations limit=5`, and `folder=still`
   (expect a refusal naming the folder, without the arcade's file path).
6. Scroll to a game in the frontend, then `curl -s http://192.168.1.20:8080/currentgame`. **Record** the
   exact format (PX-CURRENTGAME). `call show_text text=HI`, then `call show_current_game`: the frontend's
   marquee comes back. If you have a game whose ROM name has a comma (`..., The (USA)`), select it and
   check that `get_status` shows the whole name.
7. `call show_default_marquee`: **record** what appears (the default marquee, on the LED and on an LCD
   if you have one) and `curl -si http://192.168.1.20:8080/defaultmarquee | head -1` (PX-DEFAULT-MARQUEE).
   On a pixelweb older than 8.0.14, expect a refusal that names 8.0.14.

## 3. Text

Confirms PX-TEXT, PX-QUEUE.

1. `call show_text text="GAME NIGHT 7PM" color=cyan`.
2. Two lines: `call show_text text=$'HIGH SCORE\n999,990' lines=2 scroll=fixed color=yellow`. Expect two
   lines, not a literal `\n` (PX-TEXT).
3. `call show_text text="PLAYER 1" blink_ms=500`, and `left_justify=true` with `lines=4`.
4. Queue: `call show_game_marquee system=mame rom=pacman loops=5`, then right away
   `call show_text text=NEXT loops=1`: Pac-Man for ~5 s, then the text. Then `call blank_display`, which
   interrupts (PX-QUEUE).

## 4. Widgets

Confirms PX-WIDGETS.

1. `call show_widget widget=clock clock_style=galaga`, then `clock_style=pacman twelve_hour=false`.
2. `call show_widget widget=weather location=94520`, `widget=stocks tickers=AAPL,BTC-USD`,
   `widget=sports league=nfl teams=SF`. **Record** any that don't render (they may need settings in the
   Pixelcade companion app).
3. `call stop_widgets`.

## 5. The frontend

Confirms PX-FRONTEND.

1. `call show_text text="HELLO" loops=-1`, then scroll in the frontend: the game's marquee replaces the text.
2. `call show_widget widget=clock lock=true`, scroll again: the clock stays. Launch a game: the clock stops
   (the game-start hook calls `/attract/stop`). Quit the game.
3. `mcp-server-pixelcade doctor`: the `frontend` line names the last game you selected.
4. `call show_widget widget=clock lock=true`, then `call show_text text=HI`: the text call succeeds but the
   clock stays (PX-ATTRACT-LOCK). **Record** the `(pixelweb: ...)` reply. `call stop_widgets`, then
   `call show_text text=HI`: the text appears.
5. Set the screensaver to *random video* with a 1-minute delay, and wait: Pixelcade's attract mode starts.
   As the screensaver changes videos, the marquee should **not** follow, because attract mode is locked.
   `call show_text text=HI`: **record** whether the marquee changes (expected: no). `call stop_widgets`:
   attract mode ends while the screensaver keeps running; **record** whether the marquee then follows the
   screensaver's videos. Wake the cabinet, then `call get_status`: **record** whether `current_game` is
   the game you had selected or the last screensaver video's (PX-CURRENTGAME).

## 6. Pulse (once the strips are installed)

Confirms PX-ZONES, PX-BRIGHTNESS, PX-POWER. Do this only with the strips on their own 5 V supply and a
common ground to the V2 board, unless the total is under ~40 LEDs (PX-POWER).

1. `doctor`: the `pulse` line shows the LED count and brightness.
2. `call pulse_fill zone=left_strip color=red`, then `call pulse_clear zone=left_strip`. **Record** whether
   the zone clears: the documented path has no `zone/` segment (`/ledstrip/left_strip/clear`), which may
   be a documentation typo. If it doesn't clear, try
   `curl ".../ledstrip/zone/left_strip/clear"` (PX-ZONES).
3. `call pulse_effect zone=left_strip effect=rainbow speed=100`, then `effect=mame_pacman`.
4. `call set_pulse_brightness level=64`, then `curl -s .../ledstrip/getbrightness`: 64 (PX-BRIGHTNESS).
   `level=200` is refused with the cap message.
5. With the external supply in place, raise the cap: `PIXELCADE_PULSE_MAX_BRIGHTNESS=255` (or in
   config.json), and check the board's temperature behavior over a few minutes of full white.

## 7. Dry run

1. `export PIXELCADE_DRY_RUN=1`; `call show_text text=X`: `"outcome": "dry_run"` with the GET it would
   send, and the marquee doesn't change. `call get_status` shows `"dry_run": true`.
2. `unset PIXELCADE_DRY_RUN`.

## 8. Recording results

For each assumption a step confirmed, set `status="hardware-verified"` in `src/pixelcade_mcp/assumptions.py`
with what you saw in `note`, and update its row in the README's verification table (`pytest
tests/test_assumptions.py` checks they match). For anything that came out differently, use
`"hardware-contradicted"`, fix the code and the simulator, and add a test.
