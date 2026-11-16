# Pellet Stove for Home Assistant

A HACS custom integration (`pstove`) for local Wi-Fi pellet stoves using the
Micronova-style HTTP register protocol. Requires Home Assistant **2025.4.4+**.
The typed async protocol lives in the separate `aiopelletstove` package.

## Installation

After the library is published as `aiopelletstove==1.0.0`, add
`https://github.com/voluzi/ha-pstove` as a HACS custom repository, category
**Integration**, install it, and restart Home Assistant. Alternatively copy
`custom_components/pstove` into your configuration's `custom_components` directory.
Add **Pellet Stove** in Settings → Devices & services and enter the stove's host
and an optional name. No login or additional authentication is used by this protocol.

The host identifies each entry. Multiple stoves are supported; changing a host
requires removing and re-adding its entry. Use a stable hostname or address.
Communication is plain local HTTP with a five-second request timeout.

## Entities

| Platform | Controls or readings |
| --- | --- |
| Climate | Thermostat: off/heat, 7–40 °C in whole degrees, Level 1–5 presets |
| Switch | Standby, buzzer, fan enabled |
| Select | Fan 2 and Fan 3 regulation: off, 1–5, auto |
| Sensor | Power state, air/flue temperature, current power level, fan speed (rpm) |
| Diagnostic sensor | Last alarm, total hours, partial hours, ignition count |

Preferences and fan selects are configuration entities. All entities share one
device. Climate HVAC mode (OFF/HEAT) and turn on/off control power through the
library: the current state is read before a toggle; final cleaning blocks toggles.
Fan controls preserve the other channel of the packed register. OFF zeroes stale
current power, flue temperature, and fan speed in the library.

Common status and preferences poll every **10 seconds** by default. Integration
options allow **10–300 seconds** and reload the entry. Diagnostics are requested
at most once per **300 seconds** within a loaded entry, including failed attempts;
prior successful counter values remain between requests. Commands request a
coordinator refresh. Failed polling marks entities unavailable and a successful
poll restores them. No retries occur within a cycle.

Power state text preserves legacy values: `off`, `awaiting_flame`, `lighting`,
`on` (running or ashpan cleaning), `final_cleaning`, `standby`, `alarm`,
`alarm_memory`. Climate shows heating while starting/running, idle when running
with zero current power, and off in other states.

## Migration from an MQTT bridge

1. Install and configure the native integration alongside the bridge; compare
   readings and controls through a full heating cycle.
2. Stop the bridge and clear its retained discovery configurations. Remove its
   old device from Home Assistant to release its entity IDs.
3. Rename each native entity ID to its previous ID in Settings → Entities. Match
   sensors/switches by name; match fan selects by channel and climate by thermostat.
4. Review automations: the native climate entity becomes unavailable during
   outages, unlike an always-online bridge. Use climate HVAC mode or turn on/off
   for power commands.

Default entity IDs use the device name (for example,
`climate.pellet_stove_thermostat` and `select.pellet_stove_fan_2_regulation`).
No automatic entity migration is performed. For rollback, remove the native
integration and restart the old bridge; it can recreate its discovery entities.

## Local development

Use a separate virtual environment. Install the library from the sibling checkout
while leaving the manifest requirement pinned for release:

```sh
python3.13 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pip install -e ../aiopelletstove
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy --strict custom_components
.venv/bin/pytest -q
```

Tests mock `aiopelletstove`, use reserved example hostnames, and block network
sockets through the HA harness. The registry contract and all four platforms are
covered. CI targets HA 2025.4.4 and 2026.2.3 (Python 3.13); its package install
requires the library release first. Local verification uses the editable sibling.

## Releases and branding

Set up the library's PyPI trusted publisher and release it before tagging this
integration `v1.0.0`. HACS consumes integration tags/GitHub releases. The manifest
and HACS metadata retain the repository/documentation URLs for future publication.
Nothing is published by local development.

Placeholder `brands/pstove/icon.png` (256×256) and `logo.png` (512×256) are included.
Frontend branding requires a separate submission to `home-assistant/brands`;
these local files alone do not register a frontend brand. HACS validation skips
the central brands check until that submission. MIT © Voluzi.
