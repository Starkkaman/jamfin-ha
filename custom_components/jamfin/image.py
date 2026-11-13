"""Film art of a Jamfin TV as image entities (PROTOCOL.md section 7.4)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import JamfinConfigEntry
from .const import FEATURE_IMAGES
from .entity import JamfinEntity, async_add_gated_entities
from .models import ImageRef

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class JamfinImageEntityDescription(ImageEntityDescription):
    """Describes one art kind."""

    required_feature: str | None = FEATURE_IMAGES


IMAGES = (
    JamfinImageEntityDescription(key="poster", translation_key="poster"),
    JamfinImageEntityDescription(key="backdrop", translation_key="backdrop"),
    JamfinImageEntityDescription(
        key="thumb", translation_key="thumb", entity_registry_enabled_default=False
    ),
    JamfinImageEntityDescription(
        key="logo", translation_key="logo", entity_registry_enabled_default=False
    ),
    JamfinImageEntityDescription(
        key="banner", translation_key="banner", entity_registry_enabled_default=False
    ),
    JamfinImageEntityDescription(
        key="chapter", translation_key="chapter_image", entity_registry_enabled_default=False
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JamfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the image entities."""
    async_add_gated_entities(
        entry, IMAGES, lambda d: JamfinImage(hass, entry, d), async_add_entities
    )


class JamfinImage(JamfinEntity, ImageEntity):
    """The current item's art of one kind: direct Jellyfin URL, the TV's proxy as fallback."""

    entity_description: JamfinImageEntityDescription

    def __init__(
        self,
        hass: HomeAssistant,
        entry: JamfinConfigEntry,
        description: JamfinImageEntityDescription,
    ) -> None:
        """Initialise."""
        JamfinEntity.__init__(self, entry, description)
        ImageEntity.__init__(self, hass)
        self._ref: ImageRef | None = None
        self._image: tuple[bytes, str] | None = None
        self._sync_ref()

    def _current_ref(self) -> ImageRef | None:
        state = self.jamfin_state
        if state is None or state.media is None:
            return None
        return state.media.images.get(self.entity_description.key)

    @callback
    def _sync_ref(self) -> None:
        """Track the art reference; a new reference is a new image."""
        ref = self._current_ref()
        if ref is None and not self.jamfin.connected:
            # Keep the last image while the TV is unreachable (the entity is unavailable).
            return
        if ref == self._ref:
            return
        self._ref = ref
        self._image = None
        self._attr_image_last_updated = dt_util.utcnow() if ref is not None else None

    @callback
    def _handle_client_update(self) -> None:
        self._sync_ref()
        super()._handle_client_update()

    async def async_image(self) -> bytes | None:
        """Return the image bytes."""
        ref = self._ref
        if ref is None:
            return None
        if self._image is None:
            fetched = await self.jamfin.async_fetch_image(ref)
            if fetched is None or ref != self._ref:
                return None
            self._image = fetched
        content, content_type = self._image
        self._attr_content_type = content_type
        return content
