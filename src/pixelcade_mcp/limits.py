"""Rate limits for the tools that drive the marquee, the Pulse LEDs and the widgets.

Why limits at all: an LLM tool loop can call these tools many times a second. pixelweb
takes every call and drives the LED controller and the marquee's SD card, so a runaway
loop can freeze the controller or wear the card. A token bucket lets a short burst through
(a model setting up a scene) and then holds the rate to a steady pace.

Each spec is ``(burst, rate_per_second)``, passed to ``ratelimit.TokenBucket``. The
server keeps one bucket per group of tools (see server.py), so a slow marquee loop
doesn't starve the LED strips, and the reverse.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .ratelimit import TokenBucket

# Marquee actions: game, system and text marquees, animations, blanking. Up to 10 at once, then 2 a second.
MARQUEE: tuple[float, float] = (10.0, 2.0)
# Pulse LED actions: effects, fills, clears and brightness. Up to 15 at once, then 5 a second.
PULSE: tuple[float, float] = (15.0, 5.0)
# Widget start and stop. These are slow to change the screen, so 4 at once, then one every 2 seconds.
WIDGET: tuple[float, float] = (4.0, 0.5)


def bucket(spec: tuple[float, float], clock: Callable[[], float] = time.monotonic) -> TokenBucket:
    """A full bucket for ``spec``. ``clock`` is injectable so tests can move time instead of sleeping."""
    return TokenBucket(*spec, clock=clock)
