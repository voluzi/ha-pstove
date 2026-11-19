# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project uses
[Semantic Versioning](https://semver.org/).

## [1.0.1]

### Changed

- Show the requested on or off mode right after a power command, until the
  stove reports the change. Read the power state every 10 seconds while a
  command is pending, and notify on an alarm or if the stove does not confirm
  within 3 minutes.
- Send at most one power toggle at a time: ignore a repeated power command and
  reject a conflicting one while a change is pending, including across reloads.
- Send no power toggle during final cleaning, standby, an alarm, or an unknown
  state, and explain why. Requires aiopelletstove 1.1.0.

## [1.0.0]

### Added

- Local thermostat and power presets, standby and buzzer preferences, and fan controls.
- Temperature, power, fan, alarm, and operating counter entities.
- Marketplace and HACS installation, local brand assets, and a release archive.

### Fixed

- Use the Wi-Fi module MAC as device identity when the local identity endpoint
  provides it. Update the connection address without replacing existing entities.

[1.0.1]: https://github.com/voluzi/ha-pstove/releases/tag/v1.0.1
[1.0.0]: https://github.com/voluzi/ha-pstove/releases/tag/v1.0.0
