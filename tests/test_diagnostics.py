"""Diagnostics redact the client key, the host and the profile."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.core import HomeAssistant

from .conftest import TEST_KEY

REDACTED = "**REDACTED**"


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    init_integration: MockConfigEntry,
) -> None:
    """The key never appears; connection details and the last state do."""
    result = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)
    assert TEST_KEY not in str(result)
    assert result["entry"]["data"]["client_key"] == REDACTED
    assert result["entry"]["data"]["host"] == REDACTED
    assert result["entry"]["options"] == {"media_player": True}
    assert result["connection"]["connected"] is True
    assert result["connection"]["stats"]["connects"] == 1
    assert "colors" in result["connection"]["capabilities"]["features"]
    assert result["hello"]["type"] == "hello"
    assert result["state"]["profile"] == {"id": REDACTED, "name": REDACTED}
    assert "Alex" not in str(result)
    assert result["state"]["media"]["title"] == "Machine Learning"
