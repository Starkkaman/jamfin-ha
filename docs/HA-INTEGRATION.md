# jamfin-ha: Home Assistant side (plan)

Custom integration, domain **`jamfin`**, repo **`Starkkaman/jamfin-ha`** (local only for now). Implements the client
of [PROTOCOL.md](PROTOCOL.md). Status: plan, 2026-11-12. Nothing installed in Home Assistant.

## Layout

```
custom_components/jamfin/
  __init__.py          setup/unload entry, runtime_data = JamfinClient + coordinator
  manifest.json        zeroconf ["_jamfin._tcp.local."], iot_class local_push, config_flow true
  config_flow.py       zeroconf, user (manual host), pin, reauth, options
  api.py               JamfinClient: REST (aiohttp), WebSocket loop, heartbeat, backoff, command futures
  coordinator.py       JamfinCoordinator (push-only DataUpdateCoordinator, no update_interval)
  models.py            dataclasses for state/events (tolerant parsing: unknown fields/enums ignored)
  entity.py            JamfinEntity base (device_info, availability)
  media_player.py  sensor.py  binary_sensor.py  event.py  image.py  button.py  select.py
  device_trigger.py    triggers from events + state transitions
  diagnostics.py       redacted dump
  services.yaml        jamfin.play_item, jamfin.seek
  strings.json  translations/en.json  icons.json
tests/                 pytest-homeassistant-custom-component + fake Jamfin server
hacs.json  README.md  docs/
```

## Config flow

- **Zeroconf** (`async_step_zeroconf`): unique id = TXT `id`; already configured → update host/port and abort.
  Check TXT `api` (must include 1). Confirm step "Add Living Room TV?" → `pair/start` only after the user confirms,
  so the TV shows a PIN only when someone is at HA.
- **Manual** (`async_step_user`): host + port (default 8095) → `GET /api/v1/info` → unique id from `id`. Needed
  because zeroconf doesn't cross subnets (HA 192.168.5.x, TV 192.168.10.x).
- **PIN step** (`async_step_pin`): `pair/start` with `client_name` = "Home Assistant (<location name>)" and
  `client_instance` = HA's instance id; show errors from the error codes (`not_in_foreground` → "Open Jamfin on the
  TV", `pairing_in_progress`, `too_many_clients`, `invalid_pin` with tries left, `pairing_expired` → start again,
  `rate_limited`). Abort/close calls `pair/cancel`. Entry data: host, port, device id, client_id, client_key.
- **Reauth** (`async_step_reauth`): on 401 / close 4401 → `ConfigEntryAuthFailed` → same PIN step; the same
  `client_instance` replaces the old key on the TV.
- **Options**: media_player on/off (default off when a core `jellyfin` entry exists), image entities for logo/thumb.
- `async_remove_entry`: best-effort `DELETE /api/v1/pair`.

## Coordinator / client

- `iot_class: local_push`; one `JamfinClient` and **one WebSocket per config entry (TV)**.
- Background task: connect (Bearer header) → `hello` → `state` snapshot → loop; JSON `ping` every 20 s, reconnect
  if no `pong` in 10 s; backoff 1..60 s with jitter, reset after 60 s up; re-resolve via zeroconf after 3 failures
  and on any zeroconf update for this id.
- Each `state` → `coordinator.async_set_updated_data(state)` (full object, replace). Disconnect →
  `last_update_success = False` → entities unavailable, media_player `off`.
- Clock skew from `hello`/`pong` `server_time`, applied to `position_updated_at`.
- Events → fire bus event `jamfin_event` (`{device_id, event, data}`), feed the event entity and device triggers.
- Commands: `async_command(name, args)` with an id → future resolved by `result`; 15 s timeout; error codes mapped
  to `HomeAssistantError` with translated messages. REST `POST /api/v1/command` if the socket is down? No: raise
  "TV not connected" (the socket is the source of truth; REST mirror is for tests).
- Gate entities on `hello.capabilities` (e.g. `binary_sensor.in_disc_menu` only with `disc_relay`).

## Entities (one device per TV: name, model, sw_version = app_version)

| Platform | Entity | Notes |
|---|---|---|
| media_player | `<TV> Jamfin` (optional) | off (unreachable), on (app background), idle (idle/stopped), playing, paused, buffering; title, series, season, episode, position + updated_at, duration, `entity_picture` = poster (proxy via `async_get_media_image` when the Jellyfin URL isn't reachable); features: play, pause, stop, seek, next/previous track (= chapters), play_media (`media_content_id` = Jellyfin item id) |
| sensor | Playback | enum idle/playing/paused/buffering/stopped/unknown (+ unavailable when off); attrs title/kind/series. Exists so users without the media_player still have state |
| sensor | Profile | display name; attr `profile_id` |
| sensor | Media kind | enum film/episode/trailer/preshow/none |
| sensor | Chapter | chapter name; attrs `index`, `start`, `count` |
| sensor | Segment | enum intro/recap/credits/preview/commercial/none; attrs `start`, `end`, `action` |
| sensor | Film colour | state = `colors.dominant` hex; attrs `accent`, `palette`, `accent_rgb` `[r,g,b]`, `dominant_rgb`, `source` |
| binary_sensor | In disc menu | from 5b (`disc_relay`) only |
| event | Jamfin moments | event_types: preshow, movienight, movienight_winner, credits_started, next_up, item_finished |
| image | Poster, Backdrop | HA image platform; `image_url` = direct URL, fallback to fetching `proxy` with the key; `image_last_updated` when the URL changes |
| image | Logo, Thumb | disabled by default |
| button | Skip segment, Next chapter, Previous chapter, Go home | entity-native commands without the media_player |
| select | Audio track, Subtitles | options from `tracks` (label = title), "Off" for subtitles; unavailable without tracks |

`accent_rgb` / `dominant_rgb` attributes exist so automations can use `light.turn_on` `rgb_color` with a plain
attribute reference instead of a hex-parsing template.

Device triggers: the event entity's types plus `segment_started` (with kind), `chapter_changed`, and derived
transitions `started_playing`, `paused`, `resumed`, `stopped`, `turned_off` (unreachable).

Services (target: device or entity): `jamfin.play_item` (`jellyfin_item_id`, `start_position`), `jamfin.seek`
(`position`), for setups without the media_player.

### Example: tint the lights to the film

```yaml
alias: Living room takes on the film's colour
triggers:
  - trigger: state
    entity_id: sensor.living_room_tv_playback
    to: playing
conditions:
  - condition: state
    entity_id: sensor.living_room_tv_media_kind
    state: film
  - condition: template
    value_template: "{{ state_attr('sensor.living_room_tv_film_colour', 'accent_rgb') is not none }}"
actions:
  - action: light.turn_on
    target:
      area_id: living_room
    data:
      rgb_color: "{{ state_attr('sensor.living_room_tv_film_colour', 'accent_rgb') }}"
      brightness_pct: 15
      transition: 3
mode: restart
```

(The condition guards films without art; a blueprint can wrap this with a light/area selector.)

## Diagnostics

`async_get_config_entry_diagnostics`: entry data with `client_key` and host redacted, last `hello`, last state
(profile name/id redacted), connection stats (connects, last close code, backoff), API version.

## Translations

`strings.json` + `translations/en.json`: config/reauth/options steps, every protocol error code, entity names,
enum state names (segment kinds, media kinds, playback states), event types, device trigger names, service fields.

## Tests

- `pytest-homeassistant-custom-component`; a **fake Jamfin server** (aiohttp test server) implementing PROTOCOL.md:
  info, pairing (PIN, tries, lockout, too many clients, replace by instance), Bearer check, WS hello/state/events/
  results, heartbeat, close codes, art proxy.
- Config flow: zeroconf (new, already configured + address update, api mismatch), manual, PIN errors, reauth,
  options, remove (unpair called).
- Client: snapshot replaces state, seq handling, coalesced states, command futures + timeouts, backoff and jitter
  (frozen time), pong timeout, 4401 → reauth, 4000 → stop, unknown fields/events/enums tolerated.
- Entities: each platform's state from fixture states (film, episode with credits, pre-show, disc before/after 5b,
  idle, background, privacy-off profile); image entities update on URL change; colour attrs; buttons/selects send the
  right commands; device triggers fire.
- Fixtures shared with Jamfin: the JSON examples of PROTOCOL.md as files, so both sides test against the same data.

## HACS

`hacs.json` (`name: "Jamfin"`, `homeassistant` minimum version, `render_readme: true`), `manifest.json` with
`version`, `codeowners`, `documentation`/`issue_tracker` pointing at `Starkkaman/jamfin-ha`, `requirements: []`
(aiohttp comes with HA). GitHub Releases for versions; hassfest + HACS validation actions in CI.
