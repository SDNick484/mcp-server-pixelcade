"""Every pixelweb behavior this server relies on, and whether hardware has confirmed it.

The same scheme as the sibling servers: each claim has an id that the code cites
where it depends on it (``# ASSUMPTION PX-QUEUE``), that a test cites, that
HARDWARE_VALIDATION.md cites in the step confirming it, that the README's
verification table lists, and that ``mcp-server-pixelcade doctor`` prints while
it's unconfirmed. tests/test_assumptions.py fails if they drift apart.

Sources, read 2026-10: Pixelcade's API reference (pixelcade.org/developers), the
Pulse page (pixelcade.org/pulse), the release notes (pixelcade.org/release-notes,
through pixelweb 8.0.17), and Pixelcade's own Batocera hook scripts
(github.com/alinke/pixelcade-linux, batocera/scripts/). Nothing has been seen on
the owner's marquee yet. To record a result, set ``status`` to
"hardware-verified" (or "hardware-contradicted"), say what you saw in ``note``.

Confidence is about the *claim*, judged from its source:
  high   - documented, and used the same way by Pixelcade's own scripts
  medium - documented, but not used by anything we can read
  low    - our own guess; the simulator implements it but nothing confirms it
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["high", "medium", "low"]
Status = Literal["simulator-only", "hardware-verified", "hardware-contradicted"]

DOCS = "pixelcade.org/developers"
SCRIPTS = "pixelcade-linux batocera/scripts"
RELEASES = "pixelcade.org/release-notes"
SEEN = "Seen 2026-10-22 on a V2 LED (firmware PIXR0023), pixelweb 8.0.17 on Batocera 43, from a Proxmox LXC: "


@dataclass(frozen=True)
class Assumption:
    id: str
    claim: str
    source: str
    confidence: Confidence
    status: Status = "simulator-only"
    note: str = ""


ASSUMPTIONS: tuple[Assumption, ...] = (
    Assumption(
        "PX-GET-API",
        "Every control is an HTTP GET on port 8080; options are query parameters, some of them bare flags "
        "(?nogif, ?nointerrupt), and spaces in ROM names are sent as %20.",
        f"{DOCS}; {SCRIPTS} (rawurlencode, curl)",
        "high",
        note="Exactly how the hook scripts call it; our encoding of other characters (%28, %2C) isn't seen yet.",
    ),
    Assumption(
        "PX-INFO",
        "/info returns JSON with type, resolution, v2board, firmwareVersion, softwareVersion, hasLEDStripSupport, "
        "ledStrip, ledStripBrightness and ledStripNumPixels.",
        f"{DOCS} (Device Information example)",
        "medium",
        status="hardware-verified",
        note=SEEN + "every field present, plus many more: platform, arcadeFrontend 'Batocera', ledActive, "
        "noAnimatedMarquees (the 8.x 'static PNG marquees only' setting, now in get_status), sidekick, "
        "sidekick-ids, sidekick-models, sidekickOnly, centerMatrixResolution, isMiSTer, vestaboard*.",
    ),
    Assumption(
        "PX-CURRENTGAME",
        "/currentgame returns the last game or system marquee shown, by anyone (the frontend's hooks, the "
        "screensaver, this server), as 'system%rom', or 'system%' after a system marquee; text and animations "
        "don't change it. (The game-selected hook's comment says 'system,rom'.)",
        f"{SCRIPTS} game-selected; {DOCS}; hardware",
        "medium",
        status="hardware-verified",
        note=SEEN + "'neogeo%eightman' ('%', not a comma). It followed every marquee: scrolling gave "
        "genesis%Old-Towers; show_text and play_animation left it; show_console_marquee snes made it 'snes%'; "
        "show_game_marquee galaga made it mame%galaga. The claim used to say it was the frontend's selection; "
        "it isn't, so the server remembers the frontend's itself (server._Marquees). During the random-video "
        "screensaver it changed with each video (mystwaru, eightman, circusc), so "
        "the screensaver's game-selected events update it even while attract mode holds the marquee: after the "
        "screensaver, it names the last video's game, not the user's selection.",
    ),
    Assumption(
        "PX-LISTS",
        "/arcade/list, /still/list and /animations/list return a list of names.",
        DOCS,
        "low",
        status="hardware-contradicted",
        note=SEEN + "all three answer 404 (so do the documented /list/image/<system>, /list/image/all and "
        "/list/video, which may be LCD-only). list_artwork uses PX-IMAGES-LIST instead.",
    ),
    Assumption(
        "PX-IMAGES-LIST",
        'POST /api/images/list with the JSON body {"name": "<folder>"} lists /userdata/system/pixelcade/<folder> '
        "as [{name, system, urlPath, imagePath}], name being a file name; a missing folder answers 400 (plain text "
        "'open <path>: no such file or directory'), and an empty name answers null.",
        "pixelweb's own web page (its artwork browser's script); checked on hardware",
        "high",
        status="hardware-verified",
        note=SEEN + "mame: 12,415 files (10,291 PNG, 2,124 GIF; 2 MB of JSON in 0.07 s); animations: 702 PNG; "
        "console, user and system folders too; 'still' has no folder. The one POST this server sends: a read. "
        "Undocumented, so a pixelweb update could change it.",
    ),
    Assumption(
        "PX-REPLIES",
        'Controls answer 200 with an empty body (/defaultmarquee: {"status": "ok"}), even when the '
        "artwork is missing; an endpoint this pixelweb doesn't have answers 404 '404 page not found'.",
        "pixelweb is a Go net/http server; hardware",
        "low",
        status="hardware-verified",
        note=SEEN + "every control call answered 200 with nothing in the body; /arcade/stream/mame/nosuchgame "
        "answered 200 too, so a reply can't say whether artwork exists (list_artwork can). The claim used to "
        "say 'a short body'.",
    ),
    Assumption(
        "PX-TEXT",
        "/text takes the message in t=, with line breaks as the literal two characters \\n, plus c=, s=, l=, "
        "scroll=, lines=, blink= and left=.",
        DOCS,
        "medium",
        note="Documented; blink's 9-character limit is from the docs too.",
    ),
    Assumption(
        "PX-QUEUE",
        "A call with l=N (N >= 1) joins a queue; a call without l interrupts and clears it. Queued calls from "
        "scripts carry event=FEScroll.",
        DOCS,
        "medium",
        note="Documented; not exercised by the hook scripts except l=99999 on game start.",
    ),
    Assumption(
        "PX-FRONTEND",
        "EmulationStation's hooks call /console/stream/<system>/?event=FEScroll and "
        "/arcade/stream/<system>/<rom>?event=FEScroll as you scroll (game-selected skips the call when the game "
        "matches /currentgame), and /attract/stop then a queued marquee (l=99999&event=GameStart) on every game "
        "launch, so whatever this server shows lasts until the next frontend event.",
        f"{SCRIPTS} system-selected, game-selected, game-start",
        "high",
        note="Read in the scripts the installer puts on Batocera (pixelcade-linux, last changed 2026-06).",
    ),
    Assumption(
        "PX-WIDGETS",
        "/clock (clockType=, 12h=), /weather (location=), /stocks (tickers=), /sports[/<league>] (teams=) and "
        "/attract show widgets; ?nointerrupt locks one in place (PX-ATTRACT-LOCK); /attract/stop ends them.",
        DOCS,
        "medium",
        note="Documented; the themed clock types depend on the pixelweb version.",
    ),
    Assumption(
        "PX-ATTRACT-LOCK",
        "?nointerrupt locks the marquee: other API calls, this server's included, don't change it until "
        "/attract/stop. Pixelcade's Batocera screensaver-start hook calls /attract?nointerrupt and "
        "screensaver-stop calls /attract/stop, so while the cabinet's screensaver runs, the marquee shows the "
        "attract playlist and ignores both the screensaver's game-selected events and this server.",
        f"{DOCS} (attract mode: 'prevents interruption by other API calls'); {SCRIPTS} screensaver-start, "
        "screensaver-stop",
        "medium",
        note="The hook calls are read in the scripts. Unknown: whether ignored calls still answer 200 (the "
        "simulator assumes so), whether queued (l=N) calls wait for the lock, and whether Pulse calls are locked "
        "too (the simulator doesn't lock them).",
    ),
    Assumption(
        "PX-DEFAULT-MARQUEE",
        "pixelweb 8.0.14 and later have GET /defaultmarquee, which shows the default marquee "
        "(console/default-marquee.png) on the LED and LCD when called with no parameters; older versions answer 404.",
        f"{RELEASES} (8.0.14); {DOCS} (default marquee path)",
        "low",
        note="Only in the release notes, not the API reference: parameters, reply and how it interacts with "
        "the queue and attract lock are unknown.",
    ),
    Assumption(
        "PX-ZONES",
        "Pulse zones are named (left_strip, right_strip, ...) or numbered 0-35; effects are "
        "/ledstrip/zone/<z>/effect/<name>, fills /ledstrip/zone/<z>/flood, but clearing one zone is "
        "/ledstrip/<z>/clear (no zone/ segment).",
        f"{DOCS} (Pulse API)",
        "medium",
        note="The clear path's missing zone/ segment may be a documentation typo; check it first.",
    ),
    Assumption(
        "PX-BRIGHTNESS",
        "/ledstrip/brightness/<0-255> sets the strips' brightness and /ledstrip/getbrightness reads it back "
        "from the board.",
        f"{DOCS} (Pulse API)",
        "medium",
        note="Documented; needs strips connected to check. Seen 2026-10-22 with strips configured in pixelweb "
        "(ledStrip true, 50 LEDs) but none connected: /ledstrip/getbrightness never answered (30 s), so get_status "
        "and doctor wait only PULSE_STATUS_TIMEOUT_S (2 s) and report pulse.responding false.",
    ),
    Assumption(
        "PX-POWER",
        "Each WS2812B LED draws up to 60 mA at full white, and the V2 board can power only about 40 LEDs "
        "itself; more needs an external 5 V supply with a common ground. Hence the default brightness cap of 128.",
        "pixelcade.org/pulse (Powering and Safety)",
        "high",
        note="Pixelcade's own guidance. The cap is a safety margin, not a measured limit.",
    ),
)

BY_ID = {a.id: a for a in ASSUMPTIONS}


def unverified() -> list[Assumption]:
    return [a for a in ASSUMPTIONS if a.status != "hardware-verified"]
