"""Native local polling integration for pellet stoves."""

from aiopelletstove import PelletStove
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_REGISTRY_ID,
    DOMAIN,
    ENTRY_MINOR_VERSION,
    PLATFORMS,
    REQUEST_TIMEOUT,
)
from .coordinator import PstoveConfigEntry, PstoveDataUpdateCoordinator
from .identity import StoveIdentityUnavailableError, async_get_stove_mac


async def async_migrate_entry(hass: HomeAssistant, entry: PstoveConfigEntry) -> bool:
    if entry.version != 1:
        return False
    if entry.minor_version >= ENTRY_MINOR_VERSION:
        return True

    try:
        mac = await async_get_stove_mac(hass, entry.data[CONF_HOST])
    except StoveIdentityUnavailableError:
        return True
    if mac and not hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, mac):
        registry_id = entry.unique_id or entry.entry_id
        hass.config_entries.async_update_entry(
            entry,
            unique_id=mac,
            data={**entry.data, CONF_REGISTRY_ID: registry_id},
            minor_version=ENTRY_MINOR_VERSION,
        )
    else:
        hass.config_entries.async_update_entry(entry, minor_version=ENTRY_MINOR_VERSION)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PstoveConfigEntry) -> bool:
    client = PelletStove(
        entry.data[CONF_HOST],
        session=async_get_clientsession(hass),
        timeout=REQUEST_TIMEOUT,
    )
    coordinator = PstoveDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: PstoveConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PstoveConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.client.async_close()
        return True
    return False


async def async_remove_entry(hass: HomeAssistant, entry: PstoveConfigEntry) -> None:
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
