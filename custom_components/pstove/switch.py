"""Preference switches."""

from typing import Any

from aiopelletstove import registers as r
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PstoveConfigEntry
from .entity import PstoveEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PstoveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [
            PstoveSwitch(entry, "standby", "Standby", r.STANDBY_MODE),
            PstoveSwitch(entry, "buzzer", "Buzzer", r.BUZZER),
            PstoveSwitch(entry, "fan", "Fan", r.ENABLE_FAN),
        ]
    )


class PstoveSwitch(PstoveEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        entry: PstoveConfigEntry,
        key: str,
        name: str,
        register: r.Register,
    ) -> None:
        super().__init__(entry, key, name)
        self._register = register

    @property
    def is_on(self) -> bool:
        return bool(getattr(self.coordinator.data, self._register.name))

    async def _async_set(self, on: bool) -> None:
        await self.async_command(
            self.coordinator.client.async_write(self._register, on)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)
