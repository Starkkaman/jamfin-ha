"""Optional media player of a Jamfin TV (off by default next to the core Jellyfin one)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import JamfinConfigEntry
from .const import CONF_MEDIA_PLAYER
from .entity import JamfinEntity, async_send_command
from .models import ImageRef, Media

PARALLEL_UPDATES = 1

_FEATURES_BY_COMMAND = {
    "play": MediaPlayerEntityFeature.PLAY,
    "pause": MediaPlayerEntityFeature.PAUSE,
    "stop": MediaPlayerEntityFeature.STOP,
    "seek": MediaPlayerEntityFeature.SEEK,
    "next_chapter": MediaPlayerEntityFeature.NEXT_TRACK,
    "previous_chapter": MediaPlayerEntityFeature.PREVIOUS_TRACK,
    "play_media": MediaPlayerEntityFeature.PLAY_MEDIA,
}
_STATES = {
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "buffering": MediaPlayerState.BUFFERING,
}
_CONTENT_TYPES = {
    "film": MediaType.MOVIE,
    "episode": MediaType.EPISODE,
    "trailer": MediaType.VIDEO,
    "preshow": MediaType.VIDEO,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JamfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the media player when the option is on."""
    if entry.options.get(CONF_MEDIA_PLAYER, True):
        async_add_entities([JamfinMediaPlayer(entry)])


class JamfinMediaPlayer(JamfinEntity, MediaPlayerEntity):
    """Jamfin on the TV as a media player."""

    _attr_name = None
    _attr_translation_key = "jamfin"
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_media_image_remotely_accessible = False

    def __init__(self, entry: JamfinConfigEntry) -> None:
        """Initialise."""
        super().__init__(entry)
        self._attr_unique_id = f"{self.jamfin.jamfin_id}_media_player"

    @property
    def available(self) -> bool:
        """Always available: an unreachable TV is "off" (PROTOCOL.md section 10.1)."""
        return True

    @property
    def state(self) -> MediaPlayerState:
        """Off (unreachable), on (background / disc before 5b), idle, playing, ..."""
        if (state := self.jamfin_state) is None:
            return MediaPlayerState.OFF
        if (mapped := _STATES.get(state.player_state)) is not None:
            return mapped
        if state.app_state == "background" or state.player_state == "unknown":
            return MediaPlayerState.ON
        return MediaPlayerState.IDLE

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Features the TV offers."""
        features = MediaPlayerEntityFeature(0)
        capabilities = self.jamfin.capabilities
        for command, feature in _FEATURES_BY_COMMAND.items():
            if capabilities is None or command in capabilities.commands:
                features |= feature
        return features

    @property
    def _media(self) -> Media | None:
        state = self.jamfin_state
        return state.media if state is not None else None

    @property
    def media_content_id(self) -> str | None:
        """Jellyfin item id."""
        return media.jellyfin_item_id if (media := self._media) else None

    @property
    def media_content_type(self) -> str | None:
        """Movie, episode or video."""
        return _CONTENT_TYPES.get(media.kind or "") if (media := self._media) else None

    @property
    def media_title(self) -> str | None:
        """Title (the episode title for episodes)."""
        return media.title if (media := self._media) else None

    @property
    def media_series_title(self) -> str | None:
        """Series."""
        return media.series if (media := self._media) else None

    @property
    def media_season(self) -> str | None:
        """Season number."""
        media = self._media
        return str(media.season) if media and media.season is not None else None

    @property
    def media_episode(self) -> str | None:
        """Episode number."""
        media = self._media
        return str(media.episode) if media and media.episode is not None else None

    @property
    def media_duration(self) -> int | None:
        """Duration in seconds."""
        media = self._media
        return int(media.duration) if media and media.duration is not None else None

    @property
    def media_position(self) -> int | None:
        """Position in seconds at ``media_position_updated_at``."""
        media = self._media
        return int(media.position) if media and media.position is not None else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When the position was valid (TV time corrected by the clock skew)."""
        state = self.jamfin_state
        return state.adjusted_position_updated_at(self.jamfin.skew) if state else None

    def _poster(self) -> ImageRef | None:
        media = self._media
        return media.images.get("poster") if media else None

    @property
    def media_image_url(self) -> str | None:
        """Poster URL (also the cache key; the bytes come from async_get_media_image)."""
        if (poster := self._poster()) is None:
            return None
        return poster.url or poster.proxy

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Poster bytes: direct Jellyfin URL, the TV's proxy as fallback."""
        if (poster := self._poster()) is None:
            return None, None
        if (fetched := await self.jamfin.async_fetch_image(poster)) is None:
            return None, None
        return fetched

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Privacy flag and the Jamfin kind."""
        if (state := self.jamfin_state) is None:
            return None
        media = state.media
        return {
            "private": state.private,
            "kind": media.kind if media else None,
            "source": state.source,
        }

    async def async_media_play(self) -> None:
        """Play."""
        await async_send_command(self.jamfin, "play")

    async def async_media_pause(self) -> None:
        """Pause."""
        await async_send_command(self.jamfin, "pause")

    async def async_media_play_pause(self) -> None:
        """Toggle on the TV (it knows its state best)."""
        await async_send_command(self.jamfin, "toggle")

    async def async_media_stop(self) -> None:
        """Stop (what EXIT does)."""
        await async_send_command(self.jamfin, "stop")

    async def async_media_seek(self, position: float) -> None:
        """Seek to ``position`` seconds."""
        await async_send_command(self.jamfin, "seek", {"position": float(position)})

    async def async_media_next_track(self) -> None:
        """Next chapter (+30 s without chapters, like the remote)."""
        await async_send_command(self.jamfin, "next_chapter")

    async def async_media_previous_track(self) -> None:
        """Previous chapter (-10 s without chapters, like the remote)."""
        await async_send_command(self.jamfin, "previous_chapter")

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a Jellyfin item; ``media_id`` is its id."""
        await async_send_command(self.jamfin, "play_media", {"jellyfin_item_id": media_id.strip()})
