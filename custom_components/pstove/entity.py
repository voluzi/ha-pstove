"""Shared entity identity and command error handling."""

from collections.abc import Awaitable

from aiopelletstove import PelletStoveError
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PstoveConfigEntry, PstoveDataUpdateCoordinator


class PstoveEntity(CoordinatorEntity[PstoveDataUpdateCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: PstoveConfigEntry, key: str, name: str) -> None:
        super().__init__(entry.runtime_data)
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=entry.title,
            manufacturer="Generic",
            model="Wi-Fi pellet stove",
        )

    async def async_command(self, operation: Awaitable[object]) -> None:
        try:
            await operation
        except PelletStoveError as err:
            raise HomeAssistantError("Pellet stove command failed") from err
        await self.coordinator.async_request_refresh()
