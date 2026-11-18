# Pellet Stove for Home Assistant

[![Marketplace and HACS: custom repository](https://img.shields.io/badge/Marketplace%20%2F%20HACS-custom%20repository-41BDF5)](#installation)
[![Latest GitHub release](https://img.shields.io/github/v/release/voluzi/ha-pstove)](https://github.com/voluzi/ha-pstove/releases/latest)
[![Tests](https://github.com/voluzi/ha-pstove/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/voluzi/ha-pstove/actions/workflows/test.yml)
[![Validation](https://github.com/voluzi/ha-pstove/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/voluzi/ha-pstove/actions/workflows/validate.yml)
[![MIT license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Control your Wi-Fi pellet stove from Home Assistant over your local network.
Use the thermostat to set temperature and power level, adjust stove
preferences, and monitor heating and maintenance.

## Features

- On and off control, a temperature setpoint, and five power presets.
- Standby, buzzer, and fan settings, including two independent regulation controls.
- Room and flue temperatures, current power, fan speed, alarms, and usage counters.

## Compatibility

Use a Wi-Fi pellet stove or module that implements the Micronova-style local
HTTP RAM and EEPROM register API with the
[`aiopelletstove` register layout](https://github.com/voluzi/aiopelletstove/blob/main/src/aiopelletstove/registers.py).
Compatibility depends on the Wi-Fi module and firmware as well as the stove
model. There is no verified model list; a Micronova controller alone does not
guarantee compatibility. The integration requires Home Assistant **2025.4.4
or later**.

The integration uses [`aiopelletstove`](https://github.com/voluzi/aiopelletstove)
with a five-second timeout per HTTP request. The local API uses plain HTTP
without a login handshake. Keep it on your local network.

## Installation

**Marketplace (Home Assistant 2026.11 or later)**

1. In **Settings → Marketplace**, select **Add from link** in the upper right.
   If prompted, connect your GitHub account. This adds the project as a
   [custom repository](https://www.home-assistant.io/integrations/marketplace/#custom-repositories).
2. Enter `https://github.com/voluzi/ha-pstove`, select **Add**, and choose
   **Integration** if asked for a type.
3. Open **Pellet Stove** and select **Install**. If setup does not open, go to
   **Settings → Devices & services → Add integration** and search for **Pellet Stove**.
   Restart Home Assistant if prompted.

On an earlier supported Home Assistant version, add the same URL in
**HACS → Custom repositories** as an **Integration**, install it, then restart.

**Manual install**

1. Download `pstove.zip` from the [latest release](https://github.com/voluzi/ha-pstove/releases/latest)
   and extract it into `custom_components/pstove/` under your Home Assistant
   configuration directory. If using the release source archive, copy its
   `custom_components/pstove/` directory instead. The manifest should end up
   at `custom_components/pstove/manifest.json`.
2. Restart Home Assistant and add **Pellet Stove** in **Settings → Devices & services**.
   Home Assistant installs the Python dependency automatically.

## Configuration

Enter the stove's hostname or IP address without a URL scheme or path. The
optional name defaults to **Pellet Stove**. Add a separate entry for each stove.

A module that reports its MAC address through `/api/id` keeps its identity
when its IP changes. Use **Reconfigure** on the integration entry to enter
its new address. Adding the same stove at its new address also updates the
existing entry. If the module cannot report a MAC, Home Assistant uses the
configured host as its identity. For these modules, use a stable DNS name or
a DHCP reservation. If the configured host itself must change, remove the
entry and add it again.

Under **Configure**, the polling interval defaults to 10 seconds and accepts
10–300 seconds. Diagnostic registers are requested at most every five minutes;
the last successful values remain visible between reads.

## Entities

Names below are shown without the device name prefix.

| Platform | Name | Description | Unit |
| --- | --- | --- | --- |
| Climate | Thermostat | On and off, target temperature (7–40 in whole degrees), and Level 1–5 power presets | °C |
| Switch | Standby | Enable the stove's standby mode | — |
| Switch | Buzzer | Enable the audible buzzer | — |
| Switch | Fan | Enable the fan | — |
| Select | Fan 2 regulation | Fan 2 setting: off, levels 1–5, or auto | — |
| Select | Fan 3 regulation | Fan 3 setting: off, levels 1–5, or auto | — |
| Sensor | Power state | Operating state, such as lighting, on, standby, or alarm | — |
| Sensor | Air temperature | Temperature measured at the stove's room sensor | °C |
| Sensor | Flue temperature | Exhaust temperature reported by the stove | °C |
| Sensor | Current power | Active power level reported by the controller | — |
| Sensor | Fan speed | Reported fan rotation speed | rpm |
| Sensor | Last alarm | Last alarm code reported by the controller | — |
| Sensor | Ignition count | Number of ignitions | count |
| Sensor | Total hours | Total operating time | h |
| Sensor | Partial hours | Operating time on the stove's partial counter | h |

## Actions

Use the standard Home Assistant actions; there are no integration-specific
actions. Target the thermostat with `climate.turn_on`, `climate.turn_off`,
`climate.set_hvac_mode` (`heat` or `off`), `climate.set_temperature`, or
`climate.set_preset_mode` (for example, `Level 3`). Use `switch.turn_on` and
`switch.turn_off` for preferences, and `select.select_option` for fan regulation.

The integration checks the power state before toggling; it does not toggle
during final cleaning or when the stove reports an unknown state.

## Diagnostics and troubleshooting

Last alarm, ignition count, and the two hour counters are diagnostic sensors.
There is no downloadable diagnostics file. For a support issue, share the
entity readings and relevant **Settings → System → Logs** entries with
private addresses and identifiers removed.

- **Setup cannot connect:** Confirm that Home Assistant can reach the stove's
  local HTTP interface. Check the configured hostname or address and port.
- **Entities are unavailable:** A failed poll makes them unavailable until a
  successful poll. Check the network and whether the configured address has changed.
- **Power or speed reads zero while the stove is off:** The client reports zero
  for stale power, flue temperature, and fan speed readings when the stove is
  off or reports an unknown power state.
- **A fan setting is missing or rejected:** The firmware may have reported an
  unsupported fan value. A channel cannot be changed if the other channel's
  value cannot be preserved.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local checks and [CHANGELOG.md](CHANGELOG.md)
for release notes. Report vulnerabilities privately as described in
[SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE).
