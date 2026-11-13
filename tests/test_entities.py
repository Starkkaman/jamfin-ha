"""Entity state mapping, images, colours, commands and their errors."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.jamfin.const import CONF_MEDIA_PLAYER, DOMAIN
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_SEEK_POSITION,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_ENTITY_ID,
    SERVICE_MEDIA_NEXT_TRACK,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_PLAY_PAUSE,
    SERVICE_MEDIA_PREVIOUS_TRACK,
    SERVICE_MEDIA_SEEK,
    SERVICE_MEDIA_STOP,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import state_of, wait_for
from .fake_jamfin import JPEG, PNG, FakeJamfin

PLAYER = "media_player.living_room_tv"
PLAYBACK = "sensor.living_room_tv_playback"
PROFILE = "sensor.living_room_tv_profile"
KIND = "sensor.living_room_tv_media_kind"
CHAPTER = "sensor.living_room_tv_chapter"
SEGMENT = "sensor.living_room_tv_segment"
COLOUR = "sensor.living_room_tv_film_colour"
POSTER = "image.living_room_tv_poster"
AUDIO = "select.living_room_tv_audio_track"
SUBS = "select.living_room_tv_subtitles"

pytestmark = pytest.mark.usefixtures("init_integration")


async def _push(hass: HomeAssistant, fake: FakeJamfin, state: dict[str, Any]) -> None:
    client_seq = None
    for info in fake.sockets.values():
        client_seq = info["seq"] + 1
    await fake.push_state(state)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await wait_for(hass, lambda: entry.runtime_data.last_seq == client_seq)


async def test_episode(hass: HomeAssistant) -> None:
    """The PROTOCOL.md 7.1 example."""
    playback = hass.states.get(PLAYBACK)
    assert playback.state == "playing"
    assert playback.attributes["private"] is False
    assert playback.attributes["title"] == "Machine Learning"
    assert playback.attributes["series"] == "Silo"
    assert playback.attributes["kind"] == "episode"
    assert playback.attributes["app_state"] == "foreground"

    profile = hass.states.get(PROFILE)
    assert profile.state == "Isaac"
    assert profile.attributes["profile_id"] == "a1b2c3d4e5f60718293a4b5c6d7e8f90"

    assert state_of(hass, KIND) == "episode"
    chapter = hass.states.get(CHAPTER)
    assert chapter.state == "The Door"
    assert chapter.attributes == chapter.attributes | {"index": 2, "start": 1790.0, "count": 3}
    assert state_of(hass, SEGMENT) == "none"

    colour = hass.states.get(COLOUR)
    assert colour.state == "#1d2a2f"
    assert colour.attributes["accent"] == "#c8a24a"
    assert colour.attributes["palette"] == ["#1d2a2f", "#c8a24a", "#6e7f7a"]
    assert colour.attributes["accent_rgb"] == [200, 162, 74]
    assert colour.attributes["dominant_rgb"] == [29, 42, 47]
    assert colour.attributes["source"] == "poster"

    player = hass.states.get(PLAYER)
    assert player.state == "playing"
    attrs = player.attributes
    assert attrs["media_title"] == "Machine Learning"
    assert attrs["media_series_title"] == "Silo"
    assert attrs["media_season"] == "1"
    assert attrs["media_episode"] == "2"
    assert attrs["media_duration"] == 3184
    assert attrs["media_position"] == 1834
    assert attrs["media_content_id"] == "4f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f"
    assert attrs["media_content_type"] == "episode"
    assert attrs["private"] is False
    assert "entity_picture" in attrs

    assert state_of(hass, AUDIO) == "English - Dolby Digital+ - 5.1 - Default"
    assert hass.states.get(AUDIO).attributes["options"] == [
        "English - Dolby Digital+ - 5.1 - Default",
        "Deutsch - Dolby Digital+ - 5.1",
    ]
    assert state_of(hass, SUBS) == "Off"


async def test_idle(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """Idle on home: no media."""
    await _push(hass, fake_jamfin, fake_jamfin.fixture_state("state_idle.json"))
    assert state_of(hass, PLAYBACK) == "idle"
    assert state_of(hass, PLAYER) == "idle"
    assert state_of(hass, KIND) == "none"
    assert state_of(hass, CHAPTER) == "unknown"
    assert state_of(hass, SEGMENT) == "none"
    assert state_of(hass, COLOUR) == "unknown"
    assert state_of(hass, POSTER) == "unknown"
    assert state_of(hass, AUDIO) == "unavailable"
    assert "media_title" not in hass.states.get(PLAYER).attributes


async def test_private_profile(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """Privacy: playback state stays, every media detail is cleared."""
    await _push(hass, fake_jamfin, fake_jamfin.fixture_state("state_private.json"))
    playback = hass.states.get(PLAYBACK)
    assert playback.state == "playing"
    assert playback.attributes["private"] is True
    assert playback.attributes["title"] is None
    assert state_of(hass, PROFILE) == "private"
    assert hass.states.get(PROFILE).attributes["profile_id"] is None
    assert state_of(hass, KIND) == "private"
    assert state_of(hass, SEGMENT) == "unknown"
    assert state_of(hass, CHAPTER) == "unknown"
    assert state_of(hass, COLOUR) == "unknown"
    assert state_of(hass, POSTER) == "unknown"
    player = hass.states.get(PLAYER)
    assert player.state == "playing"
    assert player.attributes["private"] is True
    assert "media_title" not in player.attributes
    assert "entity_picture" not in player.attributes


async def test_private_ignores_leaked_media(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """Even if a buggy server sent media with profile "private", nothing is shown."""
    state = fake_jamfin.fixture_state("state_episode.json")
    state["profile"] = "private"
    await _push(hass, fake_jamfin, state)
    assert state_of(hass, KIND) == "private"
    assert state_of(hass, COLOUR) == "unknown"
    assert "media_title" not in hass.states.get(PLAYER).attributes


async def test_disc_before_beta5b(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """player.state unknown (disc, no relay): HA unknown, media player on."""
    await _push(hass, fake_jamfin, fake_jamfin.fixture_state("state_disc_beta5.json"))
    assert state_of(hass, PLAYBACK) == "unknown"
    assert state_of(hass, PLAYER) == "on"
    assert state_of(hass, KIND) == "film"
    assert hass.states.get(PLAYER).attributes["media_title"] == "Army of Darkness"
    assert "media_position" not in hass.states.get(PLAYER).attributes


async def test_background_and_stopped(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """App in the background: on; stopped in the foreground: idle."""
    state = fake_jamfin.fixture_state("state_idle.json")
    state["app"] = {"state": "background", "screen": None}
    state["player"]["state"] = "stopped"
    await _push(hass, fake_jamfin, state)
    assert state_of(hass, PLAYER) == "on"
    assert state_of(hass, PLAYBACK) == "stopped"
    state["app"] = {"state": "foreground", "screen": "home"}
    await _push(hass, fake_jamfin, state)
    assert state_of(hass, PLAYER) == "idle"


async def test_preshow_uses_the_films_colours(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """Pre-show: kind preshow, colours of the film it leads into."""
    await _push(hass, fake_jamfin, fake_jamfin.fixture_state("state_preshow.json"))
    assert state_of(hass, KIND) == "preshow"
    assert state_of(hass, COLOUR) == "#0f1c3a"
    assert hass.states.get(COLOUR).attributes["accent_rgb"] == [232, 178, 58]
    assert hass.states.get(PLAYER).attributes["media_content_type"] == "video"


async def test_credits_segment(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """Segment sensor with its attributes."""
    await _push(hass, fake_jamfin, fake_jamfin.fixture_state("state_credits.json"))
    segment = hass.states.get(SEGMENT)
    assert segment.state == "credits"
    assert segment.attributes["start"] == 3101.0
    assert segment.attributes["end"] == 3184.5
    assert segment.attributes["action"] == "ask"
    assert state_of(hass, COLOUR) == "unknown"


async def test_disabled_image_kinds(hass: HomeAssistant) -> None:
    """Logo, thumb, banner and chapter images are disabled by default."""
    registry = er.async_get(hass)
    for entity_id in (
        "image.living_room_tv_thumbnail",
        "image.living_room_tv_logo",
        "image.living_room_tv_banner",
        "image.living_room_tv_chapter_image",
    ):
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(POSTER).disabled_by is None
    assert registry.async_get("image.living_room_tv_backdrop").disabled_by is None


async def test_image_direct_url(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, fake_jamfin: FakeJamfin
) -> None:
    """The direct Jellyfin URL is used when it is reachable."""
    client = await hass_client()
    resp = await client.get(hass.states.get(POSTER).attributes["entity_picture"])
    assert resp.status == 200
    assert await resp.read() == JPEG
    assert resp.content_type == "image/jpeg"
    assert len(fake_jamfin.jellyfin_requests) == 1
    assert fake_jamfin.art_requests == []

    # Cached until the reference changes.
    resp = await client.get(hass.states.get(POSTER).attributes["entity_picture"])
    assert await resp.read() == JPEG
    assert len(fake_jamfin.jellyfin_requests) == 1


async def test_image_proxy_fallback_and_update(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, fake_jamfin: FakeJamfin
) -> None:
    """An unreachable Jellyfin address falls back to the TV's art proxy."""
    before = state_of(hass, POSTER)
    state = fake_jamfin.fixture_state("state_preshow.json", jellyfin="http://127.0.0.1:1")
    await _push(hass, fake_jamfin, state)
    assert state_of(hass, POSTER) != before  # a new item is a new image

    client = await hass_client()
    resp = await client.get(hass.states.get(POSTER).attributes["entity_picture"])
    assert resp.status == 200
    assert await resp.read() == PNG
    assert fake_jamfin.art_requests == ["/api/v1/art/poster/5d6e7f8091a2b3c4d5e6f708192a3b4c"]


async def test_media_player_image(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, fake_jamfin: FakeJamfin
) -> None:
    """The media player's picture comes through Home Assistant's proxy."""
    client = await hass_client()
    resp = await client.get(hass.states.get(PLAYER).attributes["entity_picture"])
    assert resp.status == 200
    assert await resp.read() == JPEG


async def test_buttons(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """Each button sends its command."""
    for key in ("skip_segment", "next_chapter", "previous_chapter", "go_home"):
        await hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: f"button.living_room_tv_{key}"},
            blocking=True,
        )
        assert fake_jamfin.commands[-1]["command"] == key
        assert "args" not in fake_jamfin.commands[-1]


@pytest.mark.parametrize(
    ("entity_id", "option", "command", "args"),
    [
        (AUDIO, "Deutsch - Dolby Digital+ - 5.1", "set_audio_track", {"index": 2}),
        (SUBS, "English - SUBRIP", "set_subtitle_track", {"index": 3}),
        (SUBS, "Off", "set_subtitle_track", {"off": True}),
    ],
)
async def test_track_selects(
    hass: HomeAssistant,
    fake_jamfin: FakeJamfin,
    entity_id: str,
    option: str,
    command: str,
    args: dict,
) -> None:
    """Selects send the Jellyfin stream index (or off)."""
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: option},
        blocking=True,
    )
    assert fake_jamfin.commands[-1]["command"] == command
    assert fake_jamfin.commands[-1]["args"] == args


@pytest.mark.parametrize(
    ("service", "data", "command", "args"),
    [
        (SERVICE_MEDIA_PLAY, {}, "play", None),
        (SERVICE_MEDIA_PAUSE, {}, "pause", None),
        (SERVICE_MEDIA_PLAY_PAUSE, {}, "toggle", None),
        (SERVICE_MEDIA_STOP, {}, "stop", None),
        (SERVICE_MEDIA_SEEK, {ATTR_MEDIA_SEEK_POSITION: 1900}, "seek", {"position": 1900.0}),
        (SERVICE_MEDIA_NEXT_TRACK, {}, "next_chapter", None),
        (SERVICE_MEDIA_PREVIOUS_TRACK, {}, "previous_chapter", None),
        (
            SERVICE_PLAY_MEDIA,
            {
                ATTR_MEDIA_CONTENT_ID: "5d6e7f8091a2b3c4d5e6f708192a3b4c",
                ATTR_MEDIA_CONTENT_TYPE: "movie",
            },
            "play_media",
            {"jellyfin_item_id": "5d6e7f8091a2b3c4d5e6f708192a3b4c"},
        ),
    ],
)
async def test_media_player_commands(
    hass: HomeAssistant,
    fake_jamfin: FakeJamfin,
    service: str,
    data: dict,
    command: str,
    args: dict | None,
) -> None:
    """Media player services map onto Jamfin commands."""
    await hass.services.async_call(
        MP_DOMAIN, service, {ATTR_ENTITY_ID: PLAYER, **data}, blocking=True
    )
    sent = fake_jamfin.commands[-1]
    assert sent["command"] == command
    assert sent.get("args") == args


async def test_play_item_and_seek_actions(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """jamfin.play_item and jamfin.seek work without the media player."""
    await hass.services.async_call(
        DOMAIN,
        "play_item",
        {
            ATTR_CONFIG_ENTRY_ID: init_integration.entry_id,
            "jellyfin_item_id": "5d6e7f80-91a2-b3c4-d5e6-f708192a3b4c",
            "start_position": 0,
        },
        blocking=True,
    )
    assert fake_jamfin.commands[-1]["command"] == "play_media"
    assert fake_jamfin.commands[-1]["args"] == {
        "jellyfin_item_id": "5d6e7f80-91a2-b3c4-d5e6-f708192a3b4c",
        "start_position": 0.0,
    }
    await hass.services.async_call(
        DOMAIN,
        "seek",
        {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id, "position": 42},
        blocking=True,
    )
    assert fake_jamfin.commands[-1] == fake_jamfin.commands[-1] | {
        "command": "seek",
        "args": {"position": 42.0},
    }
    with pytest.raises(Exception, match="jellyfin_item_id"):
        await hass.services.async_call(
            DOMAIN,
            "play_item",
            {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id, "jellyfin_item_id": "../x"},
            blocking=True,
        )


async def test_play_item_not_in_foreground(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """Jamfin in the background: play_media fails with a translated error (Q4)."""
    state = fake_jamfin.fixture_state("state_idle.json")
    state["app"] = {"state": "background", "screen": None}
    await _push(hass, fake_jamfin, state)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "play_item",
            {
                ATTR_CONFIG_ENTRY_ID: init_integration.entry_id,
                "jellyfin_item_id": "5d6e7f8091a2b3c4d5e6f708192a3b4c",
            },
            blocking=True,
        )
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "command_not_in_foreground"
    assert not isinstance(err.value, ServiceValidationError)


@pytest.mark.parametrize(
    ("code", "key", "exc"),
    [
        ("not_foreground", "command_not_in_foreground", HomeAssistantError),
        ("not_playing", "command_not_playing", HomeAssistantError),
        ("not_available", "command_not_available", HomeAssistantError),
        ("busy", "command_busy", HomeAssistantError),
        ("invalid_args", "command_invalid_args", ServiceValidationError),
        ("something_new", "command_failed", HomeAssistantError),
    ],
)
async def test_command_errors(
    hass: HomeAssistant, fake_jamfin: FakeJamfin, code: str, key: str, exc: type
) -> None:
    """Command error codes become translated Home Assistant errors."""
    fake_jamfin.command_results["go_home"] = {
        "ok": False,
        "error": {"code": code, "message": "nope"},
    }
    with pytest.raises(exc) as err:
        await hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: "button.living_room_tv_go_home"},
            blocking=True,
        )
    assert err.value.translation_key == key
    assert err.value.translation_placeholders["command"] == "go_home"


async def test_command_timeout(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """No result within the timeout."""
    fake_jamfin.command_results["skip_segment"] = None
    from unittest.mock import patch

    with (
        patch("custom_components.jamfin.client.COMMAND_TIMEOUT", 0.1),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: "button.living_room_tv_skip_segment"},
            blocking=True,
        )
    assert err.value.translation_key == "command_timeout"


async def test_command_while_disconnected(
    hass: HomeAssistant, init_integration: MockConfigEntry, fake_jamfin: FakeJamfin
) -> None:
    """Actions fail with "not connected" while the TV is unreachable."""
    fake_jamfin.reject_ws_status = 503
    await fake_jamfin.close_sockets(1001)
    await wait_for(hass, lambda: state_of(hass, PLAYER) == "off")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "seek",
            {ATTR_CONFIG_ENTRY_ID: init_integration.entry_id, "position": 1},
            blocking=True,
        )
    assert err.value.translation_key == "not_connected"


@pytest.mark.parametrize("options", [{CONF_MEDIA_PLAYER: False}])
async def test_media_player_option_off(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, options: dict
) -> None:
    """With the option off there is no media player; everything else stays."""
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Bedroom TV",
        unique_id="11111111-2222-3333-4444-555555555555",
        data={**mock_config_entry.data},
        options=options,
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, second.entry_id)
    domains = {entity.domain for entity in entities}
    assert MP_DOMAIN not in domains
    assert {"sensor", "button", "select", "event", "image"} <= domains
