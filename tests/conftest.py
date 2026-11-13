"""Fixtures for the Jamfin tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jamfin.const import (
    CONF_APP_VERSION,
    CONF_CLIENT_ID,
    CONF_CLIENT_KEY,
    CONF_JAMFIN_ID,
    CONF_MEDIA_PLAYER,
    CONF_MODEL,
    DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from .fake_jamfin import DEVICE_ID, FakeJamfin

TEST_KEY = "q9V7k0Jm1hS3b0x2c8yZ4tP6eR5uW1aN7dF3gH9jK2l"


async def wait_for(hass: HomeAssistant, predicate: Callable[[], bool], timeout: float = 5) -> None:
    """Poll until ``predicate()`` is true (real sockets: block_till_done is not enough)."""
    async with asyncio.timeout(timeout):
        while True:
            await hass.async_block_till_done()
            if predicate():
                return
            await asyncio.sleep(0.01)


def state_of(hass: HomeAssistant, entity_id: str) -> str | None:
    """The state string of an entity, or None if it does not exist."""
    state = hass.states.get(entity_id)
    return state.state if state else None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/jamfin in every test."""


@pytest.fixture(autouse=True)
def fast_timers() -> Generator[None]:
    """Make reconnect backoff fast and deterministic (jitter off)."""
    with (
        patch("custom_components.jamfin.client.BACKOFF_STEPS", (0.01, 0.02, 0.04, 0.08)),
        patch("custom_components.jamfin.client.random.random", return_value=0.5),
        patch("custom_components.jamfin.client.DIRECT_ART_TIMEOUT", 2.0),
    ):
        yield


@pytest.fixture
async def fake_jamfin(socket_enabled: None) -> AsyncGenerator[FakeJamfin]:
    """A running fake Jamfin TV on 127.0.0.1 (real sockets, localhost only)."""
    server = FakeJamfin()
    await server.start()
    yield server
    await server.close()


@pytest.fixture
def mock_config_entry(fake_jamfin: FakeJamfin) -> MockConfigEntry:
    """A paired config entry for the fake TV."""
    client_id = fake_jamfin.add_client(TEST_KEY)
    return MockConfigEntry(
        domain=DOMAIN,
        title="Living Room TV",
        unique_id=DEVICE_ID,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: fake_jamfin.port,
            CONF_JAMFIN_ID: DEVICE_ID,
            CONF_CLIENT_ID: client_id,
            CONF_CLIENT_KEY: TEST_KEY,
            CONF_MODEL: "SmartTV 4K FFM",
            CONF_APP_VERSION: "0.5.0-beta5",
        },
        options={CONF_MEDIA_PLAYER: True},
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, fake_jamfin: FakeJamfin
) -> MockConfigEntry:
    """Set up the integration against the fake TV."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
