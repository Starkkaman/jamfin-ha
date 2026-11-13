"""Base entity and shared helpers for Jamfin entities."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import JamfinCommandError, JamfinNotConnectedError
from .const import DOMAIN
from .models import Capabilities, JamfinState

if TYPE_CHECKING:
    from . import JamfinConfigEntry
    from .client import JamfinClient

# Command error codes with their own translated message (PROTOCOL.md section 9.2)
COMMAND_ERROR_CODES = frozenset(
    {
        "unknown_command",
        "invalid_args",
        "not_playing",
        "unsupported",
        "not_available",
        "not_found",
        "no_profile",
        "not_in_foreground",
        "busy",
        "rate_limited",
        "timeout",
        "internal",
    }
)
# The JamFin Agent's review wrote the foreground error as "not_foreground"; treat both alike.
_CODE_ALIASES = {"not_foreground": "not_in_foreground"}
_VALIDATION_CODES = frozenset({"invalid_args", "unknown_command"})


async def async_send_command(
    client: JamfinClient, command: str, args: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Send a command; turn protocol errors into translated Home Assistant errors."""
    try:
        return await client.async_command(command, args)
    except JamfinNotConnectedError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="not_connected",
            translation_placeholders={"name": client.name},
        ) from err
    except JamfinCommandError as err:
        code = _CODE_ALIASES.get(err.code, err.code)
        key = f"command_{code}" if code in COMMAND_ERROR_CODES else "command_failed"
        error_cls = ServiceValidationError if code in _VALIDATION_CODES else HomeAssistantError
        raise error_cls(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders={
                "name": client.name,
                "command": command,
                "code": err.code,
                "message": err.message or "",
            },
        ) from err


def requirements_met(capabilities: Capabilities | None, description: EntityDescription) -> bool:
    """Whether the TV offers what an entity description needs."""
    feature: str | None = getattr(description, "required_feature", None)
    command: str | None = getattr(description, "required_command", None)
    if feature is None and command is None:
        return True
    if capabilities is None:
        return False
    if feature is not None and feature not in capabilities.features:
        return False
    return command is None or command in capabilities.commands


@callback
def async_add_gated_entities[D: EntityDescription](
    entry: JamfinConfigEntry,
    descriptions: Iterable[D],
    factory: Callable[[D], Entity],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities whose requirements are met now, and the rest once a hello offers them.

    Entities are never removed when a capability disappears; they turn unavailable.
    """
    client = entry.runtime_data
    descriptions = tuple(descriptions)
    added: set[str] = set()

    @callback
    def _async_add_new() -> None:
        new = []
        for description in descriptions:
            if description.key in added:
                continue
            if not requirements_met(client.capabilities, description):
                continue
            added.add(description.key)
            new.append(factory(description))
        if new:
            async_add_entities(new)

    _async_add_new()
    entry.async_on_unload(client.async_add_capabilities_listener(_async_add_new))


class JamfinEntity(Entity):
    """An entity of one Jamfin TV."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, entry: JamfinConfigEntry, description: EntityDescription | None = None
    ) -> None:
        """Initialise."""
        self.jamfin = entry.runtime_data
        if description is not None:
            self.entity_description = description
            self._attr_unique_id = f"{self.jamfin.jamfin_id}_{description.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, self.jamfin.jamfin_id)})

    @property
    def available(self) -> bool:
        """Available while the socket is up and the TV offers what this entity needs."""
        if not self.jamfin.connected:
            return False
        description = getattr(self, "entity_description", None)
        return description is None or requirements_met(self.jamfin.capabilities, description)

    @property
    def jamfin_state(self) -> JamfinState | None:
        """The current state, ``None`` while disconnected."""
        return self.jamfin.state if self.jamfin.connected else None

    async def async_added_to_hass(self) -> None:
        """Follow the client."""
        await super().async_added_to_hass()
        self.async_on_remove(self.jamfin.async_add_listener(self._handle_client_update))

    @callback
    def _handle_client_update(self) -> None:
        self.async_write_ha_state()
