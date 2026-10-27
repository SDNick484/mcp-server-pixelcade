"""MCP tools, called through a real MCP client connected in-process.

This tests the contract the model actually sees: tools/list (names, titles,
annotations, input and output schemas), argument validation, and the exact
requests that reach pixelweb.
"""

from __future__ import annotations

import dataclasses

import pytest
from mcp import Client

from pixelcade_mcp import limits, server

pytestmark = pytest.mark.anyio

ALL_TOOLS = {
    "get_status",
    "list_artwork",
    "list_pulse_effects",
    "show_game_marquee",
    "show_console_marquee",
    "show_current_game",
    "show_default_marquee",
    "show_text",
    "play_animation",
    "show_widget",
    "stop_widgets",
    "blank_display",
    "pulse_effect",
    "pulse_fill",
    "pulse_clear",
    "set_pulse_brightness",
    "clear_queue",
}


@pytest.fixture
async def mcp_client(settings, fake, monkeypatch):
    # The lifespan builds the client from load_settings(); swap in the fake transport and test settings.
    monkeypatch.setattr(server, "load_settings", lambda: settings)
    async with Client(server.mcp) as c:
        yield c


async def tools(c: Client) -> dict:
    return {t.name: t for t in (await c.list_tools()).tools}


def text(result) -> str:
    return result.content[0].text


async def call(c: Client, tool: str, **args):
    return await c.call_tool(tool, args)


# --- tools/list ----------------------------------------------------------------
async def test_tool_names(mcp_client):
    assert set(await tools(mcp_client)) == ALL_TOOLS


async def test_every_tool_has_title_description_and_annotations(mcp_client):
    for t in (await tools(mcp_client)).values():
        assert t.title and t.description, t.name
        a = t.annotations
        assert a is not None and a.read_only_hint is not None and a.open_world_hint is False, t.name
        if not a.read_only_hint:
            # Every display change is undone by the next one, so nothing is destructive.
            assert a.destructive_hint is False and a.idempotent_hint is not None, t.name


async def test_read_only_tools(mcp_client):
    read_only = {n for n, t in (await tools(mcp_client)).items() if t.annotations.read_only_hint}
    assert read_only == {"get_status", "list_artwork", "list_pulse_effects"}


async def test_schemas_constrain_arguments(mcp_client):
    t = await tools(mcp_client)
    text_props = t["show_text"].input_schema["properties"]
    assert set(text_props["lines"]["enum"]) == {1, 2, 4}
    assert (text_props["text"]["maxLength"], text_props["speed"]["anyOf"][0]["maximum"]) == (500, 150)
    widget = t["show_widget"].input_schema["properties"]["widget"]
    assert set(widget["enum"]) == {"clock", "weather", "stocks", "sports", "attract"}
    level = t["set_pulse_brightness"].input_schema["properties"]["level"]
    assert (level["minimum"], level["maximum"]) == (0, 255)


async def test_get_status_publishes_output_schema(mcp_client):
    schema = (await tools(mcp_client))["get_status"].output_schema
    assert {"reachable", "error", "current_game", "pulse", "v2_board"} <= set(schema["required"])


# --- Reading -------------------------------------------------------------------
async def test_get_status(mcp_client):
    s = (await call(mcp_client, "get_status")).structured_content
    assert s["reachable"] is True and s["current_game"] == {"system": "mame", "rom": "digdug"}


async def test_list_artwork_filters_and_truncates(mcp_client, fake):
    # ASSUMPTION PX-IMAGES-LIST
    r = (await call(mcp_client, "list_artwork", folder="mame", contains="PAC", limit=2)).structured_content
    assert r == {
        "folder": "mame",
        "total": 10,
        "matches": 3,  # pacland (pacland.png and pacland_2.png), pacman, then mspacman: starts-with first
        "artwork": [
            {"name": "pacland", "still": True, "animated": False},
            {"name": "pacman", "still": True, "animated": True},
        ],
        "truncated": True,
    }
    assert fake.posts == [("/api/images/list", '{"name":"mame"}')]


async def test_list_artwork_ranks_best_matches_first(mcp_client, fake):
    fake.folders["mame"] = ["20pacgal.png", "pacman.png", "Pac-Man (Midway).png", "mspacman.png", "pac.png"]
    r = (await call(mcp_client, "list_artwork", folder="mame", contains="pac")).structured_content
    assert [a["name"] for a in r["artwork"]] == ["pac", "Pac-Man (Midway)", "pacman", "20pacgal", "mspacman"]


async def test_list_artwork_kind_filter(mcp_client, fake):
    r = (await call(mcp_client, "list_artwork", folder="mame", kind="animated")).structured_content
    assert [a["name"] for a in r["artwork"]] == ["digdug", "pacman"]


async def test_list_artwork_missing_folder(mcp_client, fake):
    r = await call(mcp_client, "list_artwork", folder="still")
    assert r.is_error and "no artwork folder named 'still'" in text(r)
    assert "/userdata" not in text(r)  # pixelweb's error names a path on the arcade; ours doesn't


@pytest.mark.parametrize("folder", ["..", "mame/../..", "a\\b"])
async def test_list_artwork_refuses_paths(mcp_client, fake, folder):
    # The folder name becomes a directory on the arcade, so it must be one plain name.
    r = await call(mcp_client, "list_artwork", folder=folder)
    assert r.is_error and fake.posts == []


async def test_documented_list_endpoints_are_gone(fake):
    # ASSUMPTION PX-LISTS: contradicted on pixelweb 8.0.17, and the fake answers the same way.
    from pixelcade_mcp.sim.fake_pixelweb import REMOVED_LISTS

    assert all(fake.handle(p)[0] == 404 for p in REMOVED_LISTS)


async def test_list_pulse_effects_needs_no_device(mcp_client, fake):
    r = (await call(mcp_client, "list_pulse_effects", category="holiday")).structured_content
    assert r["effects"] == {"holiday": ["christmas", "halloween", "valentine", "independence"]}
    assert "mame_<rom>" in r["patterns"] and "left_strip" in r["zones"]
    assert fake.requests == []


# --- The marquee ---------------------------------------------------------------
async def test_show_game_marquee(mcp_client, fake):
    r = await call(mcp_client, "show_game_marquee", system="snes", rom="Super Mario World (USA)", animation="still")
    assert not r.is_error
    assert fake.last == "/arcade/stream/snes/Super%20Mario%20World%20%28USA%29?nogif"


async def test_show_game_marquee_queued_with_fallback_and_strips_off(mcp_client, fake):
    await call(
        mcp_client, "show_game_marquee", system="mame", rom="pacman", loops=3, fallback_text="PAC-MAN", pulse="off"
    )
    assert fake.last == "/arcade/stream/mame/pacman?t=PAC-MAN&l=3&event=FEScroll&clearstrip"


@pytest.mark.parametrize("rom", ["../../quit", "a/b", ".."])
async def test_show_game_marquee_refuses_paths(mcp_client, fake, rom):
    r = await call(mcp_client, "show_game_marquee", system="mame", rom=rom)
    assert r.is_error and fake.requests == []


async def test_show_console_marquee(mcp_client, fake):
    await call(mcp_client, "show_console_marquee", system="Atari 2600")
    assert fake.last == "/console/stream/Atari%202600"


async def test_show_current_game(mcp_client, fake):
    r = await call(mcp_client, "show_current_game")
    assert r.structured_content["detail"] == "Showing mame/digdug"
    assert fake.requests == ["/currentgame", "/arcade/stream/mame/digdug"]


async def test_show_current_game_after_our_own_marquee(mcp_client, fake):
    # ASSUMPTION PX-CURRENTGAME: our marquee moves /currentgame to mame%galaga, so the server must
    # remember that the frontend's was mame%digdug.
    await call(mcp_client, "show_game_marquee", system="mame", rom="galaga")
    assert fake.current_game == "mame%galaga"
    r = await call(mcp_client, "show_current_game")
    assert r.structured_content["sent"] == ["GET /arcade/stream/mame/digdug"]


async def test_show_current_game_follows_the_frontend_after_us(mcp_client, fake):
    await call(mcp_client, "show_game_marquee", system="mame", rom="galaga")
    fake.handle("/arcade/stream/snes/Super%20Metroid?event=FEScroll")  # the user scrolls
    r = await call(mcp_client, "show_current_game")
    assert r.structured_content["sent"] == ["GET /arcade/stream/snes/Super%20Metroid"]


async def test_show_current_game_restores_a_system_marquee(mcp_client, fake):
    fake.current_game = "snes%"  # the frontend's system list
    await call(mcp_client, "show_text", text="HI")  # text doesn't move /currentgame
    r = await call(mcp_client, "show_current_game")
    assert r.structured_content["sent"] == ["GET /console/stream/snes"]


async def test_show_current_game_when_only_ours_is_known(mcp_client, fake):
    fake.current_game = ""
    await call(mcp_client, "show_game_marquee", system="mame", rom="galaga")
    r = await call(mcp_client, "show_current_game")
    assert r.is_error and "the frontend's game" not in text(r) and "Scrolling in the frontend" in text(r)


async def test_show_current_game_with_nothing_shown(mcp_client, fake):
    fake.routes["/currentgame"] = (200, "")
    r = await call(mcp_client, "show_current_game")
    assert r.is_error and "nothing to restore" in text(r) and "show_default_marquee" in text(r)


async def test_show_text(mcp_client, fake):
    r = await call(
        mcp_client, "show_text", text="HIGH SCORE\n999,990", color="#FF6D00", lines=2, scroll="fixed", loops=-1
    )
    assert not r.is_error
    # A real newline becomes pixelweb's literal backslash-n (encoded %5Cn).
    base = "/text?t=HIGH%20SCORE%5Cn999%2C990&c=ff6d00&scroll=false&lines=2"
    assert fake.requests == [base, base + "&l=99999"]  # loops=-1 is the hold pair (PX-HOLD)


async def test_show_text_forever_holds_it(mcp_client, fake):
    # ASSUMPTION PX-HOLD: l=-1 shows text once on hardware; a plain call then l=99999 holds it.
    r = (await call(mcp_client, "show_text", text="HELLO", loops=-1)).structured_content
    assert r["sent"] == ["GET /text?t=HELLO", "GET /text?t=HELLO&l=99999"]
    assert fake.requests == ["/text?t=HELLO", "/text?t=HELLO&l=99999"]
    assert "-1" not in "".join(fake.requests)


async def test_show_text_forever_in_a_dry_run(dry_client, fake):
    r = (await call(dry_client, "show_text", text="HELLO", loops=-1)).structured_content
    assert (r["outcome"], len(r["sent"]), fake.requests) == ("dry_run", 2, [])


async def test_show_text_blink_only_for_short_text(mcp_client, fake):
    assert not (await call(mcp_client, "show_text", text="PLAYER 1", blink_ms=500)).is_error
    r = await call(mcp_client, "show_text", text="INSERT COIN", blink_ms=500)
    assert r.is_error and "9 characters" in text(r)


@pytest.mark.parametrize("args", [{"text": "hi", "color": "chartreuse"}, {"text": "a\x07b"}, {"text": " \n "}])
async def test_show_text_refuses_bad_input(mcp_client, fake, args):
    r = await call(mcp_client, "show_text", **args)
    assert r.is_error and fake.requests == []


async def test_play_animation(mcp_client, fake):
    await call(mcp_client, "play_animation", name="0rain", loops=2)
    assert fake.last == "/animations/stream/0rain?l=2"


# --- Widgets -------------------------------------------------------------------
async def test_themed_clock(mcp_client, fake):
    await call(mcp_client, "show_widget", widget="clock", clock_style="pacman", lock=True)
    assert fake.last == "/clock?nointerrupt&clockType=pacman&12h=true"


async def test_sports_and_stocks(mcp_client, fake):
    await call(mcp_client, "show_widget", widget="sports", league="nfl", teams="sf, gb")
    assert fake.last == "/sports/nfl?teams=SF%2CGB"
    await call(mcp_client, "show_widget", widget="stocks", tickers="aapl,btc-usd")
    assert fake.last == "/stocks?tickers=AAPL%2CBTC-USD"


async def test_widget_rejects_parameters_for_another_widget(mcp_client, fake):
    r = await call(mcp_client, "show_widget", widget="clock", location="94520")
    assert r.is_error and "location doesn't apply to the clock widget" in text(r)
    assert fake.requests == []


async def test_stop_widgets_and_blank(mcp_client, fake):
    await call(mcp_client, "stop_widgets")
    await call(mcp_client, "blank_display")
    # A marquee reads /currentgame first, to remember the frontend's (PX-CURRENTGAME).
    assert fake.requests == ["/attract/stop", "/currentgame", "/arcade/stream/black/dummy"]


# --- Pulse ---------------------------------------------------------------------
async def test_pulse_effect(mcp_client, fake):
    r = await call(
        mcp_client, "pulse_effect", zone="Left_Strip", effect="fire", color="orange", speed=150, intensity=100
    )
    assert not r.is_error
    assert fake.last == "/ledstrip/zone/left_strip/effect/fire?r=255&g=109&b=0&speed=150&intensity=100"


async def test_pulse_effect_with_spaces_in_the_name(mcp_client, fake):
    await call(mcp_client, "pulse_effect", zone="3", effect="atari2600_Haunted House (USA)")
    assert fake.last == "/ledstrip/zone/3/effect/atari2600_Haunted%20House%20%28USA%29"


async def test_brightness_cap(mcp_client, fake):
    r = await call(mcp_client, "set_pulse_brightness", level=200)
    assert r.is_error and "limit of 128" in text(r) and "PIXELCADE_PULSE_MAX_BRIGHTNESS" in text(r)
    r = await call(mcp_client, "pulse_effect", zone="left_strip", effect="rainbow", intensity=255)
    assert r.is_error
    assert fake.requests == []
    await call(mcp_client, "set_pulse_brightness", level=128)
    assert fake.last == "/ledstrip/brightness/128"


async def test_pulse_fill_and_clear(mcp_client, fake):
    await call(mcp_client, "pulse_fill", zone="right_speaker_ring", color="blue")
    await call(mcp_client, "pulse_clear", zone="right_speaker_ring")
    await call(mcp_client, "pulse_clear")
    assert fake.requests == [
        "/ledstrip/zone/right_speaker_ring/flood?r=0&g=0&b=255",
        "/ledstrip/right_speaker_ring/clear",
        "/ledstrip/clear",
    ]


@pytest.mark.parametrize("zone", ["99", "../quit", "top"])
async def test_pulse_refuses_unknown_zones(mcp_client, fake, zone):
    r = await call(mcp_client, "pulse_fill", zone=zone, color="red")
    assert r.is_error and fake.requests == []


# --- Unreachable ---------------------------------------------------------------
async def test_unreachable_pixelweb_explains_itself(settings, monkeypatch):
    monkeypatch.setattr(server, "load_settings", lambda: dataclasses.replace(settings, url="http://127.0.0.1:9"))
    async with Client(server.mcp) as c:
        r = await c.call_tool("show_text", {"text": "hi"})
        assert r.is_error and "pixelweb isn't answering at http://127.0.0.1:9" in text(r)
        s = (await c.call_tool("get_status", {})).structured_content
        assert s["reachable"] is False


# --- ActionResult and dry run ------------------------------------------------------------------
ACTIONS = {
    "show_game_marquee", "show_console_marquee", "show_current_game", "show_text", "play_animation",
    "show_widget", "stop_widgets", "blank_display", "show_default_marquee", "pulse_effect", "pulse_fill",
    "pulse_clear", "set_pulse_brightness", "clear_queue",
}  # fmt: skip


async def test_every_action_returns_an_action_result(mcp_client):
    t = await tools(mcp_client)
    assert ACTIONS | {"get_status", "list_artwork", "list_pulse_effects"} == set(t)
    for name in ACTIONS:
        assert set(t[name].output_schema["required"]) == {"pixelcade", "outcome", "detail", "sent", "warnings"}, name


async def test_action_result(mcp_client, fake):
    r = (await call(mcp_client, "show_text", text="HI", color="green")).structured_content
    assert r == {
        "pixelcade": "Test marquee",
        "outcome": "done",
        "detail": "Showing the text",
        "sent": ["GET /text?t=HI&c=green"],
        "warnings": [],
    }


@pytest.fixture
async def dry_client(settings, monkeypatch):
    monkeypatch.setattr(server, "load_settings", lambda: dataclasses.replace(settings, dry_run=True))
    async with Client(server.mcp) as c:
        yield c


async def test_dry_run_reads_but_sends_nothing(dry_client, fake):
    assert (await call(dry_client, "get_status")).structured_content["dry_run"] is True
    r = (await call(dry_client, "show_game_marquee", system="mame", rom="galaga")).structured_content
    assert (r["outcome"], r["sent"]) == ("dry_run", ["GET /arcade/stream/mame/galaga"])
    assert r["detail"].startswith("DRY RUN, nothing sent:")
    for tool, args in [
        ("show_text", {"text": "x"}), ("show_console_marquee", {"system": "snes"}), ("show_current_game", {}),
        ("play_animation", {"name": "0rain"}), ("show_widget", {"widget": "clock"}), ("stop_widgets", {}),
        ("blank_display", {}), ("show_default_marquee", {}), ("pulse_effect", {"zone": "left_strip", "effect": "fire"}),
        ("pulse_fill", {"zone": "0", "color": "red"}), ("pulse_clear", {}), ("set_pulse_brightness", {"level": 10}),
        ("clear_queue", {}),
    ]:  # fmt: skip
        assert (await call(dry_client, tool, **args)).structured_content["outcome"] == "dry_run", tool
    reads = {"/info", "/currentgame", "/ledstrip/getbrightness"}
    assert [p for p in fake.requests if p.split("?")[0] not in reads] == []


# --- Faults ------------------------------------------------------------------------------------
async def test_slow_pixelweb_times_out_with_advice(mcp_client, fake):
    fake.slow_s = 2.5  # longer than the 2 s test timeout
    r = await call(mcp_client, "blank_display")
    assert r.is_error and "didn't answer within 2s" in text(r)


async def test_dropped_connection_is_an_error_not_a_crash(mcp_client, fake):
    fake.drop = True
    r = await call(mcp_client, "stop_widgets")
    assert r.is_error and "pixelweb" in text(r)


async def test_unknown_endpoint_suggests_updating(mcp_client, fake):
    # ASSUMPTION PX-REPLIES: an older pixelweb without an endpoint answers 404
    fake.routes["/attract/stop"] = (404, "404 page not found")
    r = await call(mcp_client, "stop_widgets")
    assert r.is_error and "updating" in text(r)


# --- Rate limits (limits.py, server._throttle) ---------------------------------------------------
@pytest.fixture
def sim_clock(mcp_client, monkeypatch):
    """Move the three buckets onto a clock the test advances by hand, starting full.

    Depends on mcp_client so the lifespan has already built the real buckets; these replace them for the test.
    The returned list holds the current time in seconds.
    """
    now = [0.0]

    def clock() -> float:
        return now[0]

    monkeypatch.setattr(server, "marquee_bucket", limits.bucket(limits.MARQUEE, clock=clock))
    monkeypatch.setattr(server, "pulse_bucket", limits.bucket(limits.PULSE, clock=clock))
    monkeypatch.setattr(server, "widget_bucket", limits.bucket(limits.WIDGET, clock=clock))
    return now


async def test_text_burst_is_capped_then_refused_before_any_request(mcp_client, sim_clock, fake):
    for _ in range(int(limits.MARQUEE[0])):
        assert not (await call(mcp_client, "show_text", text="HI")).is_error
    sent_so_far = list(fake.requests)
    r = await call(mcp_client, "show_text", text="HI")
    assert r.is_error and "Rate limit" in text(r) and "show text on the marquee" in text(r)
    assert fake.requests == sent_so_far  # the refused call reached pixelweb not at all


async def test_held_text_spends_one_token_not_two(mcp_client, sim_clock, fake):
    # loops=-1 sends two GETs (PX-HOLD), but it is one action, so one token.
    assert not (await call(mcp_client, "show_text", text="HOLD", loops=-1)).is_error
    assert len(fake.requests) == 2
    assert server.marquee_bucket.tokens == limits.MARQUEE[0] - 1


async def test_pulse_burst_is_capped_and_does_not_touch_the_marquee_bucket(mcp_client, sim_clock, fake):
    for _ in range(int(limits.PULSE[0])):
        assert not (await call(mcp_client, "pulse_effect", zone="left_strip", effect="fire")).is_error
    sent_so_far = list(fake.requests)
    r = await call(mcp_client, "pulse_effect", zone="left_strip", effect="fire")
    assert r.is_error and "run a Pulse effect" in text(r)
    assert fake.requests == sent_so_far
    # The LED burst left the marquee bucket alone: text still goes through.
    assert not (await call(mcp_client, "show_text", text="STILL FINE")).is_error


async def test_refill_follows_the_clock_and_never_exceeds_the_burst(mcp_client, sim_clock, fake):
    for _ in range(int(limits.MARQUEE[0])):
        await call(mcp_client, "show_text", text="HI")
    r = await call(mcp_client, "show_text", text="HI")
    # One token at 2 a second is 0.5 s away; the message rounds the wait up to a whole second.
    assert r.is_error and "try again in 1s" in text(r)

    sim_clock[0] += 0.5  # exactly one token refills
    assert not (await call(mcp_client, "show_text", text="HI")).is_error
    assert (await call(mcp_client, "show_text", text="HI")).is_error

    sim_clock[0] += 3600  # an hour idle refills to the burst, and no further
    for _ in range(int(limits.MARQUEE[0])):
        assert not (await call(mcp_client, "show_text", text="HI")).is_error
    assert (await call(mcp_client, "show_text", text="HI")).is_error


async def test_widget_bucket_is_shared_by_show_and_stop(mcp_client, sim_clock, fake):
    for _ in range(int(limits.WIDGET[0])):
        assert not (await call(mcp_client, "show_widget", widget="clock")).is_error
    r = await call(mcp_client, "stop_widgets")
    assert r.is_error and "stop widgets" in text(r)

    sim_clock[0] += 2.0  # 0.5 a second: one token
    before = list(fake.requests)
    assert not (await call(mcp_client, "stop_widgets")).is_error
    assert fake.requests[len(before) :] == ["/attract/stop"]


async def test_refused_restore_makes_no_currentgame_read(mcp_client, sim_clock, fake):
    # Each blank_display is one token (two GETs: the /currentgame read and the stream).
    for _ in range(int(limits.MARQUEE[0])):
        assert not (await call(mcp_client, "blank_display")).is_error
    sent_so_far = list(fake.requests)
    r = await call(mcp_client, "show_current_game")
    assert r.is_error and "Rate limit" in text(r)
    assert fake.requests == sent_so_far  # no /currentgame read either


async def test_every_session_starts_with_full_buckets(settings, fake, monkeypatch):
    monkeypatch.setattr(server, "load_settings", lambda: settings)
    async with server.lifespan(server.mcp):
        server.marquee_bucket.take(limits.MARQUEE[0])  # drain it
        assert server.marquee_bucket.tokens < 1
    async with server.lifespan(server.mcp):
        assert server.marquee_bucket.tokens == limits.MARQUEE[0]
        assert server.pulse_bucket.tokens == limits.PULSE[0]
        assert server.widget_bucket.tokens == limits.WIDGET[0]


# --- clear_queue ---------------------------------------------------------------------------------
async def test_clear_queue_stops_then_restores_the_frontend(mcp_client, fake):
    await call(mcp_client, "show_game_marquee", system="mame", rom="galaga")
    fake.handle("/arcade/stream/snes/Super%20Metroid?event=FEScroll")  # the user scrolls
    before = len(fake.requests)
    r = (await call(mcp_client, "clear_queue")).structured_content
    assert r["outcome"] == "done" and r["warnings"] == []
    assert r["sent"] == ["GET /attract/stop", "GET /arcade/stream/snes/Super%20Metroid"]
    assert fake.requests[before:] == ["/attract/stop", "/currentgame", "/arcade/stream/snes/Super%20Metroid"]


async def test_clear_queue_without_restore_only_stops(mcp_client, fake):
    r = (await call(mcp_client, "clear_queue", restore_current=False)).structured_content
    assert (r["sent"], fake.requests) == (["GET /attract/stop"], ["/attract/stop"])


async def test_clear_queue_with_no_frontend_game_still_clears(mcp_client, fake):
    fake.routes["/currentgame"] = (200, "")
    r = (await call(mcp_client, "clear_queue")).structured_content
    assert r["outcome"] == "done" and r["sent"] == ["GET /attract/stop"]
    assert len(r["warnings"]) == 1 and "nothing to restore" in r["warnings"][0]


async def test_clear_queue_spends_one_token_even_when_it_restores(mcp_client, sim_clock, fake):
    await call(mcp_client, "show_game_marquee", system="mame", rom="galaga")  # one token
    fake.handle("/arcade/stream/snes/Super%20Metroid?event=FEScroll")
    before = server.marquee_bucket.tokens
    assert not (await call(mcp_client, "clear_queue")).is_error
    assert server.marquee_bucket.tokens == before - 1  # not two: the restore doesn't throttle again


async def test_refused_clear_queue_sends_nothing(mcp_client, sim_clock, fake):
    for _ in range(int(limits.MARQUEE[0])):
        await call(mcp_client, "blank_display")
    before = list(fake.requests)
    r = await call(mcp_client, "clear_queue")
    assert r.is_error and "clear the queue" in text(r)
    assert fake.requests == before
