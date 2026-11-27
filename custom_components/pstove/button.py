"""Local recovery for a pending power command."""

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import power_notification_id
from .coordinator import PstoveConfigEntry
from .entity import PstoveEntity
from .power import PowerTracker, power_cleared_signal, power_tracker

_CLEAR_PENDING = "clear_pending_power_command"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PstoveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [PstoveClearPendingPower(entry, power_tracker(hass, entry.entry_id))]
    )


class PstoveClearPendingPower(PstoveEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = _CLEAR_PENDING

    def __init__(self, entry: PstoveConfigEntry, tracker: PowerTracker) -> None:
        super().__init__(entry, _CLEAR_PENDING)
        self._tracker = tracker
        self._entry_id = entry.entry_id

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        async with self._tracker.lock:
            self._tracker.pending = None
            persistent_notification.async_dismiss(
                self.hass, power_notification_id(self._entry_id)
            )
            async_dispatcher_send(self.hass, power_cleared_signal(self._entry_id))
