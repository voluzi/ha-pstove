"""Two controls backed by one packed fan register."""

from typing import Literal

from aiopelletstove import FanRegulation
from homeassistant.components.select import SelectEntity
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
            PstoveSelect(entry, "fan2", "Fan 2 regulation"),
            PstoveSelect(entry, "fan3", "Fan 3 regulation"),
        ]
    )


class PstoveSelect(PstoveEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [option.value for option in FanRegulation]

    def __init__(
        self,
        entry: PstoveConfigEntry,
        channel: Literal["fan2", "fan3"],
        name: str,
    ) -> None:
        super().__init__(entry, f"{channel}_regulation", name)
        self._channel = channel

    @property
    def current_option(self) -> str | None:
        state = self.coordinator.data
        fan = (
            state.fan2_regulation if self._channel == "fan2" else state.fan3_regulation
        )
        return fan.value if fan is not None else None

    async def async_select_option(self, option: str) -> None:
        await self.async_command(
            self.coordinator.client.async_set_fan_regulation(self._channel, option),
        )
