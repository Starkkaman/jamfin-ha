# jamfin-ha

A Home Assistant custom integration (`jamfin`) for **Jamfin TV**, our own Jellyfin client for Android TV
(repo `Starkkaman/Jamfin`). Jamfin runs a small local server on the TV; this integration finds it on the LAN (or
takes its address), pairs with a PIN shown on the TV, and keeps one WebSocket open per TV. Home Assistant gets live
playback state, Jamfin's "moments" (pre-show, movie night, credits, next up, item finished), media segments and
chapters, the active profile, film art and colours, and can send commands (play, pause, seek, skip intro, change
tracks, play a title).

**Status: 0.1.0, untested against a real TV.** Jamfin's server side (beta 5) is not built yet; the integration is
tested against a fake Jamfin server that implements [docs/PROTOCOL.md](docs/PROTOCOL.md). Targets Home Assistant
2026.11; the test suite runs on Home Assistant 2026.10.4 (the newest `pytest-homeassistant-custom-component` build).

- [docs/PROTOCOL.md](docs/PROTOCOL.md): the wire contract (discovery, pairing, REST, WebSocket, state, events,
  commands, security).
- [docs/HA-INTEGRATION.md](docs/HA-INTEGRATION.md): the original plan for the Home Assistant side.

## Install

### HACS (custom repository)

1. HACS > three-dot menu > **Custom repositories** > add `https://github.com/Starkkaman/jamfin-ha`, type
   **Integration**. (The repo is local only for now; this works once it is on GitHub.)
2. Install **Jamfin**, then restart Home Assistant.

### By hand

Copy `custom_components/jamfin/` into Home Assistant's `config/custom_components/` and restart.

No extra Python packages: the integration uses only aiohttp and zeroconf, which ship with Home Assistant.

## Set up (pairing)

1. **Open Jamfin on the TV** and leave it on screen. Pairing needs it in the foreground, because the TV shows the PIN.
2. In Home Assistant: **Settings > Devices & services**.
   - If the TV was discovered (zeroconf `_jamfin._tcp`), click **Add** on the discovered "Living Room TV" card and
     confirm. Only then does the TV show a PIN.
   - Otherwise **Add integration > Jamfin** and enter the TV's address, for example `192.168.10.63`, port `8095`.
     **Discovery (mDNS) does not cross subnets**: at home Home Assistant (192.168.5.x) and the TV (192.168.10.x) are
     on different subnets, so manual entry is the normal path unless the router reflects mDNS. Give the TV a DHCP
     reservation so its address stays put.
3. Type the **6-digit PIN** the TV shows. It is valid for 2 minutes and allows 3 tries; after that ask for a new PIN.
   Three failed pairings lock pairing on the TV for 15 minutes.
4. Done. The TV keeps the pairing under Jamfin Settings > Home Assistant, where it can be revoked. If it is revoked
   (or Jamfin is reinstalled), Home Assistant asks to **re-authenticate**: open Jamfin and type the new PIN; the old
   key is replaced, not added.

Deleting the integration entry unpairs from the TV (best effort; the TV may be off).

**Options**: *Media player* on/off. It defaults to **off** when the core **Jellyfin** integration is set up (that one
already shows Jamfin's session as a media player), otherwise on. Everything else is there either way.

## Entities

One device per TV ("Living Room TV", model and Jamfin version from the TV).

| Entity | Example id | Notes |
|---|---|---|
| Media player (optional) | `media_player.living_room_tv` | `off` = TV unreachable, `on` = Jamfin in the background (or a disc before beta 5b), `idle`, `playing`, `paused`, `buffering`. Title, series, season/episode, position, duration, poster. Play/pause/stop/seek, next/previous = chapters, `play_media` with a Jellyfin item id. |
| Playback | `sensor.living_room_tv_playback` | `idle`, `playing`, `paused`, `buffering`, `stopped` (`unknown` for a disc before beta 5b). Attributes `private`, `title`, `kind`, `series`, `source`, `app_state`. |
| Profile | `sensor.living_room_tv_profile` | Active Jellyfin profile name, `private`, or unknown (profile picker). Attribute `profile_id`. |
| Media kind | `sensor.living_room_tv_media_kind` | `film`, `episode`, `trailer`, `preshow`, `none`, `private`. |
| Chapter | `sensor.living_room_tv_chapter` | Current chapter name; attributes `index`, `start`, `count`. |
| Segment | `sensor.living_room_tv_segment` | `intro`, `recap`, `credits`, `preview`, `commercial`, `none`; attributes `start`, `end`, `action`. |
| Film colour | `sensor.living_room_tv_film_colour` | State = dominant colour `#rrggbb`; attributes `accent`, `palette`, `dominant_rgb`, `accent_rgb`, `source`. Needs the TV's `colors` feature. |
| Poster, Backdrop | `image.living_room_tv_poster`, `image.living_room_tv_backdrop` | Art of the current item (during the pre-show: of the film). Loaded from Jellyfin directly, through the TV's art proxy when Home Assistant can't reach Jellyfin. |
| Thumbnail, Logo, Banner, Chapter image | `image.living_room_tv_…` | Disabled by default. |
| In disc menu | `binary_sensor.living_room_tv_in_disc_menu` | Appears once Jamfin offers the disc relay (beta 5b). |
| Jamfin moments | `event.living_room_tv_jamfin_moments` | Event types `preshow`, `movienight`, `movienight_winner`, `credits_started`, `next_up`, `item_finished`, with the event's data as attributes. |
| Skip segment, Next chapter, Previous chapter, Go home | `button.living_room_tv_…` | Same behaviour as the TV remote. |
| Audio track, Subtitles | `select.living_room_tv_audio_track`, `select.living_room_tv_subtitles` | Tracks of the open player ("Off" for subtitles); unavailable without a Jellyfin player. |

Entities other than the media player are **unavailable while the TV is unreachable**; the media player shows **off**.
When Android stops Jamfin in the background, that is what Home Assistant sees until Jamfin starts again (Jamfin has
no foreground service, by decision).

### Device triggers

In the automation editor, pick the TV device: *Pre-show started*, *Movie night vote opened*, *Movie night winner
picked*, *Credits started*, *Next up shown*, *Item finished*, *Media segment started* (optionally one kind),
*Chapter changed*, and the playback transitions *Started playing*, *Paused*, *Resumed*, *Stopped*, *Turned off
(unreachable)*.

All of these are also fired on the bus as `jamfin_event` with `device_id`, `jamfin_id`, `event`, `data`, `derived`
(true for the playback transitions) and, for segments, `kind`.

### Actions

- `jamfin.play_item`: `config_entry_id`, `jellyfin_item_id`, optional `start_position` (seconds; leave out to resume,
  `0` from the start). Needs Jamfin on screen; a disc item opens its film page.
- `jamfin.seek`: `config_entry_id`, `position` (seconds).

Errors come back as readable messages, for example "Jamfin has to be on screen on Living Room TV for this" when
`play_item` or *Go home* is used while Jamfin is in the background (pause, seek, skip and tracks work as long as a
player is open).

## Example automations

Tint the living room to the film's accent colour when a film starts:

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

Raise the lights when the credits start:

```yaml
alias: Lights up for the credits
triggers:
  - trigger: state
    entity_id: event.living_room_tv_jamfin_moments
    not_from: unavailable
conditions:
  - condition: state
    entity_id: event.living_room_tv_jamfin_moments
    attribute: event_type
    state: credits_started
actions:
  - action: light.turn_on
    target:
      area_id: living_room
    data:
      brightness_pct: 60
      transition: 8
mode: single
```

(The same works with the device trigger *Credits started* picked in the automation editor.)

## Privacy

Jamfin has a per-profile switch, **"Tell Home Assistant what's playing"**. Pairing Home Assistant does not switch it
on for anyone. With it **off** for the active profile, the TV only tells Home Assistant the playback state:

- Playback still shows `playing` / `paused` / `stopped` (with attribute `private: true`), so lights can follow it;
- Profile and Media kind show `private`; title, ids, chapters, segment, tracks, art and colours are cleared;
- no moments, segment or chapter events fire;
- commands still work.

With it **on**, everything above is sent. The connection is plain `ws://` on the LAN, protected by the pairing key
and a LAN-only check on the TV: the same exposure as Jellyfin's own HTTP on that network.

## Development

```bash
python3.14 -m venv .venv      # or: uv venv --python 3.14 .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff check custom_components tests
.venv/bin/pytest -q
```

The tests run a fake Jamfin TV (`tests/fake_jamfin.py`, aiohttp on 127.0.0.1) and use the JSON examples of
PROTOCOL.md as shared fixtures (`tests/fixtures/`), so Jamfin can test its server against the same data.
