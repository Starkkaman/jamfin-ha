"""Constants for the Jamfin integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "jamfin"
LOGGER = logging.getLogger(__package__)

ZEROCONF_TYPE: Final = "_jamfin._tcp.local."
DEFAULT_PORT: Final = 8095
API_VERSION: Final = 1
SUPPORTED_API_VERSIONS: Final = frozenset({1})

# Config entry data keys
CONF_JAMFIN_ID: Final = "jamfin_id"
CONF_CLIENT_ID: Final = "client_id"
CONF_CLIENT_KEY: Final = "client_key"
CONF_ZEROCONF_NAME: Final = "zeroconf_name"
CONF_MODEL: Final = "model"
CONF_APP_VERSION: Final = "app_version"
CONF_CAPABILITIES: Final = "capabilities"
CONF_PIN: Final = "pin"

# Options
CONF_MEDIA_PLAYER: Final = "media_player"

# Home Assistant bus event fired for every Jamfin moment and derived transition
EVENT_JAMFIN: Final = "jamfin_event"

# Protocol events shown by the event entity (and offered as device triggers)
MOMENT_EVENTS: Final = (
    "preshow",
    "movienight",
    "movienight_winner",
    "credits_started",
    "next_up",
    "item_finished",
)
# Protocol events that are device triggers / bus events only (too frequent for the entity)
TRIGGER_ONLY_EVENTS: Final = ("segment_started", "chapter_changed")
PROTOCOL_EVENTS: Final = MOMENT_EVENTS + TRIGGER_ONLY_EVENTS
# Triggers derived in Home Assistant from player state transitions
DERIVED_TRIGGERS: Final = (
    "started_playing",
    "paused",
    "resumed",
    "stopped",
    "turned_off",
)

PLAYER_STATES: Final = ("idle", "playing", "paused", "buffering", "stopped", "unknown")
ACTIVE_PLAYER_STATES: Final = frozenset({"playing", "paused", "buffering"})
MEDIA_KINDS: Final = ("film", "episode", "trailer", "preshow")
SEGMENT_KINDS: Final = ("intro", "recap", "credits", "preview", "commercial")
APP_STATES: Final = ("foreground", "background")
PRIVATE: Final = "private"

FEATURE_IMAGES: Final = "images"
FEATURE_COLORS: Final = "colors"
FEATURE_DISC_RELAY: Final = "disc_relay"

IMAGE_KINDS: Final = ("poster", "backdrop", "thumb", "logo", "banner", "chapter")

# Connection behaviour (PROTOCOL.md sections 6.3 and 10.1)
BACKOFF_STEPS: Final = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 60.0)
BACKOFF_JITTER: Final = 0.2
BACKOFF_RESET_AFTER: Final = 60.0
RESOLVE_AFTER_FAILURES: Final = 3
CONNECT_TIMEOUT: Final = 10.0
HELLO_TIMEOUT: Final = 10.0
DEFAULT_PING_INTERVAL: Final = 20.0
DEFAULT_PONG_TIMEOUT: Final = 10.0
COMMAND_TIMEOUT: Final = 15.0
REST_TIMEOUT: Final = 10.0
SETUP_FIRST_CONNECT_WAIT: Final = 5.0
CLOCK_SKEW_IGNORE: Final = 1.0
ART_MAX_BYTES: Final = 5 * 1024 * 1024
DIRECT_ART_TIMEOUT: Final = 5.0
DIRECT_ART_RETRY_AFTER: Final = 600.0
