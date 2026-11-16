"""Integration constants."""

from homeassistant.const import Platform

DOMAIN = "pstove"
DEFAULT_NAME = "Pellet Stove"
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 10
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 300
DIAGNOSTICS_INTERVAL = 300
REQUEST_TIMEOUT = 5.0
PLATFORMS = [Platform.CLIMATE, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]
