"""Tolerant parsing, colours, backoff and derived transitions (no Home Assistant)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.jamfin.client import backoff_delay
from custom_components.jamfin.const import BACKOFF_STEPS
from custom_components.jamfin.models import (
    Capabilities,
    FilmColors,
    Hello,
    JamfinState,
    Track,
    hex_to_rgb,
)
from custom_components.jamfin.select import track_options
from custom_components.jamfin.triggers import derive_transitions

from .fake_jamfin import load_json


def test_episode_parses() -> None:
    state = JamfinState.from_dict(load_json("state_episode.json"))
    assert state.player_state == "playing"
    assert state.profile_name == "Isaac"
    assert state.private is False
    assert state.media is not None
    assert state.media.kind == "episode"
    assert state.media.position == 1834.0
    assert state.media.images["poster"].proxy.startswith("/api/v1/art/poster/")
    assert state.media.colors.accent_rgb == [200, 162, 74]
    assert state.chapters.current_chapter.name == "The Door"
    assert [t.index for t in state.tracks.subtitle] == [3, 4]
    assert state.segment is None


def test_private_parses() -> None:
    state = JamfinState.from_dict(load_json("state_private.json"))
    assert state.private is True
    assert state.profile_name is None
    assert state.player_state == "playing"
    assert state.media is None


@pytest.mark.parametrize(
    "garbage",
    [
        {},
        {"player": "x", "media": [], "profile": 5, "chapters": {"list": "x", "current": 9}},
        {"player": {"state": 7, "playback_rate": "fast"}, "media": {"kind": 1, "images": []}},
        {"media": {"colors": {"dominant": "red", "palette": [1, "#ABCDEF"]}}},
    ],
)
def test_garbage_never_raises(garbage: dict) -> None:
    state = JamfinState.from_dict(garbage)
    assert state.player_state == "unknown"
    assert state.playback_rate == 1.0


def test_unknown_values() -> None:
    raw = load_json("state_episode.json")
    raw["player"]["state"] = "warp"
    raw["media"]["kind"] = "hologram"
    raw["segment"] = {"kind": "ad_break", "start": 1, "end": 2, "action": "ask"}
    raw["chapters"]["current"] = 99
    state = JamfinState.from_dict(raw)
    assert state.player_state == "unknown"
    assert state.media.kind is None
    assert state.segment.kind is None
    assert state.chapters.current is None


def test_colors() -> None:
    colors = FilmColors.from_dict(
        {
            "dominant": "#1D2A2F",
            "accent": "#c8a24a",
            "palette": ["#1d2a2f", "bad"],
            "source": "thumb",
        }
    )
    assert colors.dominant == "#1d2a2f"
    assert colors.palette == ("#1d2a2f",)
    assert colors.dominant_rgb == [29, 42, 47]
    assert FilmColors.from_dict(None) is None
    assert FilmColors.from_dict({"dominant": "nope"}) is None
    assert hex_to_rgb("#ffffff") == [255, 255, 255]
    assert hex_to_rgb("fff") is None


def test_hello_and_capabilities() -> None:
    hello = Hello.from_dict(load_json("hello.json"))
    assert hello.api == 1
    assert hello.ping_interval == 20
    assert hello.pong_timeout == 10
    assert "skip_segment" in hello.capabilities.commands
    assert Capabilities.from_dict(hello.capabilities.as_dict()) == hello.capabilities
    defaults = Hello.from_dict({"type": "hello"})
    assert defaults.ping_interval == 20
    assert defaults.capabilities == Capabilities()


def test_backoff() -> None:
    """1, 2, 4, 8, 16, 30, 60 s, then 60 s, each +-20 %."""
    # The autouse fixture shortens the steps for the other tests; use the real ones here.
    with patch("custom_components.jamfin.client.BACKOFF_STEPS", BACKOFF_STEPS):
        assert [backoff_delay(i, lambda: 0.5) for i in range(9)] == [
            1.0,
            2.0,
            4.0,
            8.0,
            16.0,
            30.0,
            60.0,
            60.0,
            60.0,
        ]
        assert backoff_delay(0, lambda: 0.0) == pytest.approx(0.8)
        assert backoff_delay(6, lambda: 1.0) == pytest.approx(72.0)


def test_track_labels_are_unique() -> None:
    tracks = (
        Track(index=3, language="eng", title="English - SUBRIP", default=False, forced=False),
        Track(index=5, language="eng", title="English - SUBRIP", default=False, forced=False),
        Track(index=6, language="fre", title=None, default=False, forced=False),
    )
    assert track_options(tracks) == {
        "English - SUBRIP (#3)": 3,
        "English - SUBRIP (#5)": 5,
        "fre": 6,
    }


def _state(player: str, item: str | None = "a" * 32) -> JamfinState:
    raw = load_json("state_episode.json")
    raw["player"]["state"] = player
    if item is None:
        raw["media"] = None
    else:
        raw["media"]["jellyfin_item_id"] = item
    return JamfinState.from_dict(raw)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (None, _state("playing"), []),
        (_state("idle", None), _state("buffering"), ["started_playing"]),
        (_state("stopped"), _state("playing"), ["started_playing"]),
        (_state("buffering"), _state("playing"), []),
        (_state("playing"), _state("paused"), ["paused"]),
        (_state("paused"), _state("buffering"), ["resumed"]),
        (_state("paused"), _state("playing"), ["resumed"]),
        (_state("playing"), _state("stopped"), ["stopped"]),
        (_state("unknown"), _state("stopped"), ["stopped"]),
        (_state("playing"), _state("buffering", "b" * 32), ["started_playing"]),
        (_state("idle", None), _state("idle", None), []),
    ],
)
def test_derive_transitions(before, after, expected) -> None:
    assert derive_transitions(before, after) == expected
