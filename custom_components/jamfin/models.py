"""Tolerant models of the Jamfin local API v1 messages (docs/PROTOCOL.md).

Parsing never raises on unknown fields, unknown enum values or wrong types: unknown
fields are ignored, an unknown enum value becomes ``None`` (Home Assistant's "unknown").
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from typing import Any

from .const import (
    APP_STATES,
    DEFAULT_PING_INTERVAL,
    DEFAULT_PONG_TIMEOUT,
    MEDIA_KINDS,
    PLAYER_STATES,
    PRIVATE,
    SEGMENT_KINDS,
)

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _enum(value: Any, allowed: Iterable[str]) -> str | None:
    if isinstance(value, str) and value in allowed:
        return value
    return None


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp (``2026-11-12T20:15:03.120Z``)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def hex_to_rgb(value: str | None) -> list[int] | None:
    """Convert ``#rrggbb`` to ``[r, g, b]``."""
    if value is None or not _HEX_COLOR.match(value):
        return None
    return [int(value[i : i + 2], 16) for i in (1, 3, 5)]


def _color(value: Any) -> str | None:
    if isinstance(value, str) and _HEX_COLOR.match(value):
        return value.lower()
    return None


@dataclass(frozen=True, slots=True)
class JamfinDevice:
    """The TV as Jamfin describes it."""

    id: str | None
    name: str | None
    model: str | None
    app_version: str | None

    @classmethod
    def from_dict(cls, data: Any) -> JamfinDevice:
        """Parse a ``device`` object."""
        data = _dict(data)
        return cls(
            id=_str(data.get("id")),
            name=_str(data.get("name")),
            model=_str(data.get("model")),
            app_version=_str(data.get("app_version")),
        )


@dataclass(frozen=True, slots=True)
class Capabilities:
    """``hello.capabilities``."""

    commands: frozenset[str] = frozenset()
    events: frozenset[str] = frozenset()
    features: frozenset[str] = frozenset()

    @classmethod
    def from_dict(cls, data: Any) -> Capabilities:
        """Parse a ``capabilities`` object (also the cached copy in the entry)."""
        data = _dict(data)

        def _names(key: str) -> frozenset[str]:
            return frozenset(v for v in _list(data.get(key)) if isinstance(v, str))

        return cls(
            commands=_names("commands"),
            events=_names("events"),
            features=_names("features"),
        )

    def as_dict(self) -> dict[str, list[str]]:
        """Serialise for the config entry cache."""
        return {
            "commands": sorted(self.commands),
            "events": sorted(self.events),
            "features": sorted(self.features),
        }


@dataclass(frozen=True, slots=True)
class Hello:
    """The ``hello`` message."""

    api: int | None
    server_time: datetime | None
    client_id: str | None
    device: JamfinDevice
    capabilities: Capabilities
    ping_interval: float
    pong_timeout: float
    raw: Mapping[str, Any] = field(repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Hello:
        """Parse a ``hello`` message."""
        heartbeat = _dict(data.get("heartbeat"))
        ping_interval = _num(heartbeat.get("ping_interval"))
        pong_timeout = _num(heartbeat.get("pong_timeout"))
        return cls(
            api=_int(data.get("api")),
            server_time=parse_time(data.get("server_time")),
            client_id=_str(data.get("client_id")),
            device=JamfinDevice.from_dict(data.get("device")),
            capabilities=Capabilities.from_dict(data.get("capabilities")),
            ping_interval=ping_interval
            if ping_interval and ping_interval > 0
            else DEFAULT_PING_INTERVAL,
            pong_timeout=pong_timeout
            if pong_timeout and pong_timeout > 0
            else DEFAULT_PONG_TIMEOUT,
            raw=data,
        )


@dataclass(frozen=True, slots=True)
class ImageRef:
    """One entry of ``media.images``."""

    url: str | None
    proxy: str | None


@dataclass(frozen=True, slots=True)
class FilmColors:
    """``media.colors``."""

    dominant: str | None
    accent: str | None
    palette: tuple[str, ...]
    source: str | None

    @classmethod
    def from_dict(cls, data: Any) -> FilmColors | None:
        """Parse ``media.colors``; ``None`` when absent or unusable."""
        if not isinstance(data, Mapping):
            return None
        dominant = _color(data.get("dominant"))
        accent = _color(data.get("accent"))
        palette = tuple(c for v in _list(data.get("palette")) if (c := _color(v)))
        if dominant is None and accent is None and not palette:
            return None
        return cls(
            dominant=dominant,
            accent=accent,
            palette=palette,
            source=_str(data.get("source")),
        )

    @property
    def dominant_rgb(self) -> list[int] | None:
        """Dominant colour as ``[r, g, b]``."""
        return hex_to_rgb(self.dominant)

    @property
    def accent_rgb(self) -> list[int] | None:
        """Accent colour as ``[r, g, b]``."""
        return hex_to_rgb(self.accent)


@dataclass(frozen=True, slots=True)
class Media:
    """``media``."""

    kind: str | None
    title: str | None
    series: str | None
    season: int | None
    episode: int | None
    year: int | None
    duration: float | None
    position: float | None
    position_updated_at: datetime | None
    jellyfin_item_id: str | None
    jellyfin_series_id: str | None
    jellyfin_server_id: str | None
    is_disc: bool
    preshow: Mapping[str, Any] | None
    images: Mapping[str, ImageRef]
    colors: FilmColors | None

    @classmethod
    def from_dict(cls, data: Any) -> Media | None:
        """Parse ``media``; ``None`` when absent."""
        if not isinstance(data, Mapping):
            return None
        images: dict[str, ImageRef] = {}
        for kind, ref in _dict(data.get("images")).items():
            if not isinstance(kind, str) or not isinstance(ref, Mapping):
                continue
            url = _str(ref.get("url"))
            proxy = _str(ref.get("proxy"))
            if url or proxy:
                images[kind] = ImageRef(url=url, proxy=proxy)
        preshow = data.get("preshow")
        return cls(
            kind=_enum(data.get("kind"), MEDIA_KINDS),
            title=_str(data.get("title")),
            series=_str(data.get("series")),
            season=_int(data.get("season")),
            episode=_int(data.get("episode")),
            year=_int(data.get("year")),
            duration=_num(data.get("duration")),
            position=_num(data.get("position")),
            position_updated_at=parse_time(data.get("position_updated_at")),
            jellyfin_item_id=_str(data.get("jellyfin_item_id")),
            jellyfin_series_id=_str(data.get("jellyfin_series_id")),
            jellyfin_server_id=_str(data.get("jellyfin_server_id")),
            is_disc=data.get("is_disc") is True,
            preshow=preshow if isinstance(preshow, Mapping) else None,
            images=images,
            colors=FilmColors.from_dict(data.get("colors")),
        )


@dataclass(frozen=True, slots=True)
class Track:
    """One audio or subtitle track."""

    index: int
    language: str | None
    title: str | None
    default: bool
    forced: bool

    @classmethod
    def from_dict(cls, data: Any) -> Track | None:
        """Parse a track; ``None`` without a usable index."""
        data = _dict(data)
        if (index := _int(data.get("index"))) is None:
            return None
        return cls(
            index=index,
            language=_str(data.get("language")),
            title=_str(data.get("title")),
            default=data.get("default") is True,
            forced=data.get("forced") is True,
        )


@dataclass(frozen=True, slots=True)
class Tracks:
    """``tracks``."""

    audio: tuple[Track, ...]
    subtitle: tuple[Track, ...]
    audio_selected: int | None
    subtitle_selected: int | None

    @classmethod
    def from_dict(cls, data: Any) -> Tracks | None:
        """Parse ``tracks``; ``None`` when absent."""
        if not isinstance(data, Mapping):
            return None
        return cls(
            audio=tuple(t for v in _list(data.get("audio")) if (t := Track.from_dict(v))),
            subtitle=tuple(t for v in _list(data.get("subtitle")) if (t := Track.from_dict(v))),
            audio_selected=_int(data.get("audio_selected")),
            subtitle_selected=_int(data.get("subtitle_selected")),
        )


@dataclass(frozen=True, slots=True)
class Chapter:
    """One chapter."""

    start: float
    name: str | None


@dataclass(frozen=True, slots=True)
class Chapters:
    """``chapters``."""

    items: tuple[Chapter, ...]
    current: int | None

    @classmethod
    def from_dict(cls, data: Any) -> Chapters | None:
        """Parse ``chapters``; ``None`` when absent."""
        if not isinstance(data, Mapping):
            return None
        items = []
        for value in _list(data.get("list")):
            chapter = _dict(value)
            if (start := _num(chapter.get("start"))) is None:
                continue
            items.append(Chapter(start=start, name=_str(chapter.get("name"))))
        current = _int(data.get("current"))
        if current is not None and not 0 <= current < len(items):
            current = None
        return cls(items=tuple(items), current=current)

    @property
    def current_chapter(self) -> Chapter | None:
        """The chapter the position is in."""
        return self.items[self.current] if self.current is not None else None


@dataclass(frozen=True, slots=True)
class Segment:
    """``segment``."""

    kind: str | None
    start: float | None
    end: float | None
    action: str | None

    @classmethod
    def from_dict(cls, data: Any) -> Segment | None:
        """Parse ``segment``; ``None`` when absent."""
        if not isinstance(data, Mapping):
            return None
        return cls(
            kind=_enum(data.get("kind"), SEGMENT_KINDS),
            start=_num(data.get("start")),
            end=_num(data.get("end")),
            action=_str(data.get("action")),
        )


@dataclass(frozen=True, slots=True)
class JamfinState:
    """A full ``state`` object (section 7)."""

    server_time: datetime | None
    device: JamfinDevice
    app_state: str | None
    screen: str | None
    profile_id: str | None
    profile_name: str | None
    private: bool
    player_state: str
    source: str | None
    playback_rate: float
    in_disc_menu: bool | None
    media: Media | None
    tracks: Tracks | None
    chapters: Chapters | None
    segment: Segment | None
    raw: Mapping[str, Any] = field(repr=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> JamfinState:
        """Parse a state object."""
        app = _dict(data.get("app"))
        player = _dict(data.get("player"))
        profile = data.get("profile")
        private = profile == PRIVATE
        profile_data = _dict(profile)
        player_state = _enum(player.get("state"), PLAYER_STATES) or "unknown"
        rate = _num(player.get("playback_rate"))
        return cls(
            server_time=parse_time(data.get("server_time")),
            device=JamfinDevice.from_dict(data.get("device")),
            app_state=_enum(app.get("state"), APP_STATES),
            screen=_str(app.get("screen")),
            profile_id=None if private else _str(profile_data.get("id")),
            profile_name=None if private else _str(profile_data.get("name")),
            private=private,
            player_state=player_state,
            source=_str(player.get("source")),
            playback_rate=rate if rate and rate > 0 else 1.0,
            in_disc_menu=_bool(player.get("in_disc_menu")),
            # A private profile never exposes media details, whatever the server sends.
            media=None if private else Media.from_dict(data.get("media")),
            tracks=None if private else Tracks.from_dict(data.get("tracks")),
            chapters=None if private else Chapters.from_dict(data.get("chapters")),
            segment=None if private else Segment.from_dict(data.get("segment")),
            raw=data,
        )

    def adjusted_position_updated_at(self, skew: timedelta) -> datetime | None:
        """``media.position_updated_at`` moved from TV time to local time."""
        if self.media is None or self.media.position_updated_at is None:
            return None
        return self.media.position_updated_at + skew
