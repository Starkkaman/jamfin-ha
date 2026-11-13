"""Config flow tests: manual host, zeroconf, PIN errors, lockout, reauth, options."""

from __future__ import annotations

from collections.abc import Generator
from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jamfin.const import (
    CONF_CLIENT_KEY,
    CONF_JAMFIN_ID,
    CONF_MEDIA_PLAYER,
    CONF_PIN,
    CONF_ZEROCONF_NAME,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .conftest import TEST_KEY, state_of, wait_for
from .fake_jamfin import DEVICE_ID, FakeJamfin

pytestmark = pytest.mark.usefixtures("fake_jamfin")


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Don't connect after the flow creates the entry."""
    with patch("custom_components.jamfin.async_setup_entry", return_value=True) as mock_setup:
        yield mock_setup


def _zeroconf(fake: FakeJamfin, **props: str) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address("127.0.0.1"),
        ip_addresses=[ip_address("127.0.0.1")],
        hostname="Android-3.local.",
        name="Living Room TV._jamfin._tcp.local.",
        port=fake.port,
        type="_jamfin._tcp.local.",
        properties={
            "id": DEVICE_ID,
            "api": "1",
            "name": "Living Room TV",
            "version": "0.5.0-beta5",
            **props,
        },
    )


async def _start_user_flow(hass: HomeAssistant, fake: FakeJamfin) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port}
    )


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """Manual host -> PIN -> entry; media player on without core Jellyfin."""
    result = await _start_user_flow(hass, fake_jamfin)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pin"
    assert fake_jamfin.pairing is not None
    assert fake_jamfin.pairing["name"].startswith("Home Assistant (")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: " 042 917 "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room TV"
    assert result["result"].unique_id == DEVICE_ID
    data = result["data"]
    assert data[CONF_HOST] == "127.0.0.1"
    assert data[CONF_PORT] == fake_jamfin.port
    assert data[CONF_JAMFIN_ID] == DEVICE_ID
    assert data[CONF_CLIENT_KEY] in fake_jamfin.clients
    assert result["options"] == {CONF_MEDIA_PLAYER: True}
    assert fake_jamfin.pairing is None
    assert fake_jamfin.cancelled == []


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow_media_player_off_with_core_jellyfin(
    hass: HomeAssistant, fake_jamfin: FakeJamfin
) -> None:
    """A core Jellyfin entry makes the Jamfin media player default to off."""
    MockConfigEntry(domain="jellyfin", title="Jellyfin").add_to_hass(hass)
    result = await _start_user_flow(hass, fake_jamfin)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: fake_jamfin.pin}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_MEDIA_PLAYER: False}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Nothing listens on the port."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow_not_in_foreground(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """Jamfin must be on screen to show the PIN; retrying works once it is."""
    fake_jamfin.foreground = False
    result = await _start_user_flow(hass, fake_jamfin)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "not_in_foreground"}

    fake_jamfin.foreground = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake_jamfin.port}
    )
    assert result["step_id"] == "pin"


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        (lambda f: setattr(f, "pairing", {"pairing_id": "p_other"}), "pairing_in_progress"),
        (lambda f: setattr(f, "max_clients", 0), "too_many_clients"),
        (lambda f: setattr(f, "lockout_until", 1e12), "rate_limited"),
    ],
)
async def test_user_flow_start_errors(
    hass: HomeAssistant, fake_jamfin: FakeJamfin, setup, error: str
) -> None:
    """pair/start refusals are shown on the step that asked."""
    setup(fake_jamfin)
    result = await _start_user_flow(hass, fake_jamfin)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    if error in ("pairing_in_progress", "rate_limited"):
        assert result["description_placeholders"]["retry_after"] in ("97", "900")


@pytest.mark.usefixtures("mock_setup_entry")
async def test_wrong_pin_then_right(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """A wrong PIN shows the tries left; the right one pairs."""
    result = await _start_user_flow(hass, fake_jamfin)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "000000"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pin"
    assert result["errors"] == {CONF_PIN: "invalid_pin"}
    assert result["description_placeholders"]["attempts_left"] == "2"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PIN: "12ab"})
    assert result["errors"] == {CONF_PIN: "invalid_pin_format"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: fake_jamfin.pin}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_setup_entry")
async def test_three_wrong_pins_then_new_pin(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """The third wrong PIN ends the pairing; a new PIN can be requested."""
    result = await _start_user_flow(hass, fake_jamfin)
    for _ in range(3):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PIN: "000000"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pairing_expired"
    assert fake_jamfin.pairing is None

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pin"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: fake_jamfin.pin}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_lockout_after_three_failed_pairings(
    hass: HomeAssistant, fake_jamfin: FakeJamfin
) -> None:
    """3 failed pairings lock pairing; the flow says so (rate_limited)."""
    result = await _start_user_flow(hass, fake_jamfin)
    for pairing in range(3):
        for _ in range(3):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_PIN: "999999"}
            )
        assert result["step_id"] == "pairing_expired"
        if pairing < 2:
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
            assert result["step_id"] == "pin"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pairing_expired"
    assert result["errors"] == {"base": "rate_limited"}
    assert result["description_placeholders"]["retry_after"] == "900"


async def test_pin_expired_on_the_tv(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """The PIN timed out / Back on the TV: pairing_expired step."""
    result = await _start_user_flow(hass, fake_jamfin)
    fake_jamfin._end_pairing()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: fake_jamfin.pin}
    )
    assert result["step_id"] == "pairing_expired"


async def test_closing_the_flow_cancels_the_pin(
    hass: HomeAssistant, fake_jamfin: FakeJamfin
) -> None:
    """Closing the dialog hides the PIN on the TV (pair/cancel)."""
    result = await _start_user_flow(hass, fake_jamfin)
    pairing_id = fake_jamfin.pairing["pairing_id"]
    hass.config_entries.flow.async_abort(result["flow_id"])
    await wait_for(hass, lambda: fake_jamfin.cancelled == [pairing_id])
    assert fake_jamfin.pairing is None


async def test_user_flow_already_configured(
    hass: HomeAssistant, fake_jamfin: FakeJamfin, mock_config_entry: MockConfigEntry
) -> None:
    """The same TV (by id) aborts and updates the address."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_HOST: "10.0.0.9"}
    )
    with patch("custom_components.jamfin.async_setup_entry", return_value=True):
        result = await _start_user_flow(hass, fake_jamfin)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == "127.0.0.1"


@pytest.mark.usefixtures("mock_setup_entry")
async def test_zeroconf_flow(hass: HomeAssistant, fake_jamfin: FakeJamfin) -> None:
    """Discovery -> confirm -> PIN -> entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf(fake_jamfin)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert fake_jamfin.pairing is None  # no PIN before someone confirms

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pin"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: fake_jamfin.pin}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room TV"
    assert result["data"][CONF_ZEROCONF_NAME] == "Living Room TV._jamfin._tcp.local."


async def test_zeroconf_updates_known_tv(
    hass: HomeAssistant, fake_jamfin: FakeJamfin, mock_config_entry: MockConfigEntry
) -> None:
    """A known id at a new address updates the entry without a reload."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_PORT: 1}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf(fake_jamfin)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_PORT] == fake_jamfin.port
    assert mock_config_entry.data[CONF_ZEROCONF_NAME] == "Living Room TV._jamfin._tcp.local."


async def test_zeroconf_reconnects_a_loaded_entry(
    hass: HomeAssistant, fake_jamfin: FakeJamfin, mock_config_entry: MockConfigEntry
) -> None:
    """A loaded entry that can't reach the TV gets the new address and retries now."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_PORT: 1}
    )
    with patch("custom_components.jamfin.client.BACKOFF_STEPS", (3600.0,)):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert state_of(hass, "media_player.living_room_tv") == "off"
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf(fake_jamfin)
        )
        await wait_for(hass, lambda: state_of(hass, "media_player.living_room_tv") == "playing")


@pytest.mark.parametrize(
    ("props", "reason"),
    [
        ({"id": ""}, "invalid_discovery_info"),
        ({"api": "x"}, "invalid_discovery_info"),
        ({"api": "0"}, "unsupported_api"),
    ],
)
async def test_zeroconf_rejects(
    hass: HomeAssistant, fake_jamfin: FakeJamfin, props: dict, reason: str
) -> None:
    """Bad TXT records abort."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf(fake_jamfin, **props)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


async def test_reauth(
    hass: HomeAssistant, fake_jamfin: FakeJamfin, mock_config_entry: MockConfigEntry
) -> None:
    """Reauth pairs again; the same instance replaces the old key on the TV."""
    mock_config_entry.add_to_hass(hass)
    fake_jamfin.clients.clear()  # the key was revoked on the TV
    with patch("custom_components.jamfin.async_setup_entry", return_value=True):
        result = await mock_config_entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "pin"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PIN: fake_jamfin.pin}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    new_key = mock_config_entry.data[CONF_CLIENT_KEY]
    assert new_key != TEST_KEY
    assert list(fake_jamfin.clients) == [new_key]

    # A second reauth from the same Home Assistant replaces, it doesn't add a client.
    with patch("custom_components.jamfin.async_setup_entry", return_value=True):
        result = await mock_config_entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PIN: fake_jamfin.pin}
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert len(fake_jamfin.clients) == 1


async def test_reauth_other_tv(
    hass: HomeAssistant, fake_jamfin: FakeJamfin, mock_config_entry: MockConfigEntry
) -> None:
    """A different TV at the old address is refused."""
    mock_config_entry.add_to_hass(hass)
    fake_jamfin.device["id"] = "00000000-0000-0000-0000-000000000000"
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: fake_jamfin.pin}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"


async def test_reauth_not_in_foreground(
    hass: HomeAssistant, fake_jamfin: FakeJamfin, mock_config_entry: MockConfigEntry
) -> None:
    """Reauth shows pair/start errors on its confirm step."""
    mock_config_entry.add_to_hass(hass)
    fake_jamfin.foreground = False
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "not_in_foreground"}


async def test_options_flow(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Turning the media player off reloads without it."""
    assert state_of(hass, "media_player.living_room_tv") == "playing"
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MEDIA_PLAYER: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert init_integration.options == {CONF_MEDIA_PLAYER: False}
    await wait_for(hass, lambda: state_of(hass, "sensor.living_room_tv_playback") == "playing")
    assert state_of(hass, "media_player.living_room_tv") in (None, "unavailable")
