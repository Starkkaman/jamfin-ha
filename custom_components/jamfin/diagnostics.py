"""Diagnostics for Jamfin (client key, host and profile redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import JamfinConfigEntry
from .const import CONF_CLIENT_KEY

TO_REDACT_ENTRY = {CONF_CLIENT_KEY, CONF_HOST}


def _redact_state(raw: Any) -> Any:
    """Redact the active profile (id and name) from a state object."""
    if not isinstance(raw, dict):
        return raw
    state = dict(raw)
    if isinstance(state.get("profile"), dict):
        state["profile"] = {key: REDACTED for key in state["profile"]}
    return state


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: JamfinConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    client = entry.runtime_data
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT_ENTRY),
            "options": dict(entry.options),
        },
        "connection": client.diagnostics(),
        "hello": dict(client.hello.raw) if client.hello else None,
        "state": _redact_state(dict(client.state.raw)) if client.state else None,
    }
