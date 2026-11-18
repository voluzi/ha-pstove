"""Read the optional Wi-Fi module identity without retaining the response."""

import re

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac

from .const import REQUEST_TIMEOUT

_MAC = re.compile(r"[0-9a-f]{2}([:-]?)(?:[0-9a-f]{2}\1){4}[0-9a-f]{2}")


async def async_get_stove_mac(hass: HomeAssistant, host: str) -> str | None:
    """Return a unicast MAC on modules that implement the identity endpoint."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"http://{host}/api/id",
            timeout=ClientTimeout(total=REQUEST_TIMEOUT),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                return None
            payload = await response.json(content_type=None)
    except (ClientError, TimeoutError, ValueError):
        return None

    value = payload.get("mac") if isinstance(payload, dict) else None
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if not _MAC.fullmatch(value) or int(value[:2], 16) & 1:
        return None
    mac = format_mac(value)
    return mac if mac != "00:00:00:00:00:00" else None
