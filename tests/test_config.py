"""Validators that keep model-supplied values from turning into other URLs, and settings loading."""

from __future__ import annotations

import json

import pytest

from pixelcade_mcp import config as cfg


@pytest.mark.parametrize(
    "value", ["pacman", "Super Mario World (USA)", "Pac-Man (Atari) (USA)", "Yars' Revenge", "1942"]
)
def test_path_segment_keeps_real_rom_names(value):
    assert cfg.path_segment(f"  {value} ", "rom") == value


@pytest.mark.parametrize("value", ["", "   ", "..", ".", "../quit", "a/b", "a\\b", "x\ny", "x\x00", "a" * 201])
def test_path_segment_refuses(value):
    with pytest.raises(cfg.InvalidValue):
        cfg.path_segment(value, "rom")


@pytest.mark.parametrize(
    ("value", "expected"), [("Red", "red"), ("#FF6D00", "ff6d00"), ("45b39d", "45b39d"), (" white ", "white")]
)
def test_text_color(value, expected):
    assert cfg.text_color(value) == expected


@pytest.mark.parametrize("value", ["purple", "fff", "#12345g", "ff6d00ff"])
def test_text_color_refuses(value):
    with pytest.raises(cfg.InvalidValue, match="Unknown color"):
        cfg.text_color(value)


def test_rgb():
    assert cfg.rgb("red") == (255, 0, 0)
    assert cfg.rgb("#0a141e") == (10, 20, 30)


@pytest.mark.parametrize(
    ("value", "expected"), [("left_strip", "left_strip"), ("LEFT_STRIP", "left_strip"), ("07", "7")]
)
def test_zone_id(value, expected):
    assert cfg.zone_id(value) == expected


@pytest.mark.parametrize("value", ["36", "-1", "top_strip", "left_strip/../x", ""])
def test_zone_id_refuses(value):
    with pytest.raises(cfg.InvalidValue):
        cfg.zone_id(value)


@pytest.mark.parametrize("value", ["rainbow", "matrix_8x32_mame_pacman", "atari2600_Yars' Revenge (USA)"])
def test_effect_name(value):
    assert cfg.effect_name(value) == value


@pytest.mark.parametrize("value", ["..", "a/b", "_x", "x?y=1", "x&y", "x%2Fy", "x" * 81])
def test_effect_name_refuses(value):
    with pytest.raises(cfg.InvalidValue):
        cfg.effect_name(value)


def test_tickers_and_teams():
    assert cfg.tickers("aapl, btc-usd ,^GSPC") == "AAPL,BTC-USD,^GSPC"
    assert cfg.teams("sf,gb") == "SF,GB"
    with pytest.raises(cfg.InvalidValue, match="Not valid"):
        cfg.tickers("AAPL,DROP TABLE")
    with pytest.raises(cfg.InvalidValue, match="At most 20"):
        cfg.teams(",".join(["SF"] * 21))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("batocera.local", "http://batocera.local:8080"),
        ("192.168.1.20", "http://192.168.1.20:8080"),
        ("http://192.168.1.20:8090/", "http://192.168.1.20:8090"),
    ],
)
def test_normalize_url(raw, expected):
    assert cfg.normalize_url(raw) == expected


def test_normalize_url_refuses_other_schemes():
    with pytest.raises(ValueError):
        cfg.normalize_url("ftp://batocera.local")


def test_defaults():
    s = cfg.load_settings()
    assert (s.url, s.pulse_max_brightness) == ("http://batocera.local:8080", 128)


def test_environment_wins_over_config_json(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(json.dumps({"url": "10.0.0.5", "pulse_max_brightness": 200}))
    assert cfg.load_settings().url == "http://10.0.0.5:8080"
    assert cfg.load_settings().pulse_max_brightness == 200
    monkeypatch.setenv("PIXELCADE_URL", "10.0.0.6")
    monkeypatch.setenv("PIXELCADE_PULSE_MAX_BRIGHTNESS", "64")
    s = cfg.load_settings()
    assert (s.url, s.pulse_max_brightness) == ("http://10.0.0.6:8080", 64)


def test_bad_cap_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("PIXELCADE_PULSE_MAX_BRIGHTNESS", "999")
    assert cfg.load_settings().pulse_max_brightness == cfg.DEFAULT_PULSE_MAX_BRIGHTNESS
