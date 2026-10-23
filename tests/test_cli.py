"""The command line: doctor, call and simulate, against the fake pixelweb."""

from __future__ import annotations

import asyncio
import json

import pytest

from pixelcade_mcp import cli
from pixelcade_mcp.assumptions import unverified
from pixelcade_mcp.config import Settings
from pixelcade_mcp.doctor import STEP, run_doctor


def run(argv: list[str], capsys) -> tuple[int, str, str]:
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    out, err = capsys.readouterr()
    return int(exc.value.code or 0), out, err


@pytest.fixture
def env(fake, monkeypatch):
    monkeypatch.setenv("PIXELCADE_URL", fake.url)
    return fake


def test_doctor_ok(env, capsys):
    code, out, _ = run(["doctor"], capsys)
    assert code == 0 and out.rstrip().endswith("OK")
    assert "LED 128x32, V2 board True" in out and "last marquee shown: mame/digdug" in out
    assert "90 LEDs, brightness 64" in out
    pending = unverified()  # any still-unverified id, so recording results doesn't break this test
    assert not pending or ("Not yet confirmed on hardware" in out and pending[0].id in out)


def test_doctor_notes_a_pixelweb_too_old_for_the_default_marquee(env, capsys):
    # ASSUMPTION PX-DEFAULT-MARQUEE: read from the version, never probed (that would change the display).
    code, out, _ = run(["doctor"], capsys)
    assert "pixelweb 8.0.17-sim" in out and "needs 8.0.14" not in out
    env.info["softwareVersion"] = "7.9.9"
    code, out, _ = run(["doctor"], capsys)
    assert code == 0 and "pixelweb 7.9.9 (show_default_marquee needs 8.0.14+)" in out
    assert "/defaultmarquee" not in env.requests


def test_doctor_when_configured_strips_dont_answer(env, capsys):
    # ASSUMPTION PX-BRIGHTNESS: strips configured in pixelweb, none connected.
    env.hang.add("/ledstrip/getbrightness")
    code, out, _ = run(["doctor"], capsys)
    assert code == 1 and "x  pulse     Pulse strips configured (90 LEDs), but: pixelweb didn't answer" in out
    assert "If no strips are connected and powered yet, that's expected" in out


def test_doctor_flags_brightness_above_the_cap(env, capsys, monkeypatch):
    # ASSUMPTION PX-POWER
    env.brightness = 200
    code, out, _ = run(["doctor"], capsys)
    assert code == 0 and "brightness 200 (above this server's cap)" in out


def test_doctor_points_at_the_validation_step(env, capsys):
    env.routes["/info"] = (500, "boom")
    code, out, _ = run(["check"], capsys)  # `check` is an alias
    assert code == 1 and "PROBLEMS FOUND" in out and f"step {STEP['info']}" in out


def test_doctor_json(env, capsys):
    code, out, _ = run(["doctor", "--json"], capsys)
    report = json.loads(out)
    assert code == 0 and report["ok"] is True and {c["layer"] for c in report["checks"]} == set(STEP)


@pytest.mark.anyio
async def test_doctor_with_nothing_listening():
    report = await run_doctor(Settings(url="http://127.0.0.1:9"), timeout=1)
    assert not report.ok and report.checks[-1].layer == "tcp"


def test_call(env, capsys):
    code, out, _ = run(["call", "show_text", "text=HELLO", "color=cyan"], capsys)
    assert code == 0 and json.loads(out)["sent"] == ["GET /text?t=HELLO&c=cyan"]
    assert env.last == "/text?t=HELLO&c=cyan"


def test_call_dry_run_sends_nothing(env, capsys):
    code, out, _ = run(["call", "--dry-run", "blank_display"], capsys)
    assert code == 0 and json.loads(out)["outcome"] == "dry_run"
    assert env.requests == []


def test_call_reports_errors(env, capsys):
    code, _, err = run(["call", "set_pulse_brightness", "level=255"], capsys)
    assert code == 1 and "limit of 128" in err


@pytest.mark.anyio
async def test_simulate_writes_a_config_and_echoes_requests(tmp_path, capsys):
    args = cli.build_parser().parse_args(["simulate", "--write-config", str(tmp_path / "sim")])
    task = asyncio.create_task(cli._cmd_simulate(args))
    config = tmp_path / "sim" / "config.json"
    for _ in range(100):
        if config.exists():
            break
        await asyncio.sleep(0.02)
    url = json.loads(config.read_text())["url"]
    import httpx

    async with httpx.AsyncClient() as c:
        r = await c.get(f"{url}/text?t=HI")
        assert (r.status_code, r.text) == (200, "")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    out = capsys.readouterr().out
    assert "export PIXELCADE_CONFIG_DIR=" in out and "pixelweb <- GET /text?t=HI" in out
