"""Detect disagreement between the stove board and an external power meter."""

from collections.abc import Callable
from datetime import datetime
from math import isfinite

from aiopelletstove import PowerState
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, EntityCategory, UnitOfPower
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util.unit_conversion import PowerConverter

from .const import (
    CONF_POWER_SENSOR,
    CONF_POWER_THRESHOLD,
    DEFAULT_POWER_THRESHOLD,
    DOMAIN,
    LOW_POWER_THRESHOLD,
)
from .coordinator import PstoveConfigEntry
from .entity import PstoveEntity

_MISMATCH = "board_state_mismatch"
_HIGH_POWER_DELAY = 5 * 60
_LOW_POWER_DELAY = 10 * 60
_ACTIVE_STATES = frozenset(
    {
        PowerState.LIGHTING,
        PowerState.RUNNING,
        PowerState.ASHPAN_CLEANING,
        PowerState.FINAL_CLEANING,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PstoveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if not (sensor := entry.options.get(CONF_POWER_SENSOR)):
        return
    detector = BoardStateDetector(hass, entry, sensor)
    detector.async_start()
    async_add_entities([PstoveBoardStateMismatch(entry, detector)])


def _watts(state: State | None) -> float | None:
    if state is None:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if not isinstance(unit, str) or unit not in PowerConverter.VALID_UNITS:
        return None
    try:
        watts = PowerConverter.convert(float(state.state), unit, UnitOfPower.WATT)
    except ValueError:
        return None
    return watts if isfinite(watts) else None


class BoardStateDetector:
    def __init__(
        self, hass: HomeAssistant, entry: PstoveConfigEntry, sensor: str
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.issue_id = f"{_MISMATCH}_{entry.entry_id}"
        self.signal = f"{DOMAIN}_{self.issue_id}"
        self.value: bool | None = None
        self._sensor = er.async_resolve_entity_id(er.async_get(hass), sensor)
        self._threshold = float(
            entry.options.get(CONF_POWER_THRESHOLD, DEFAULT_POWER_THRESHOLD)
        )
        self._pending_delay: int | None = None
        self._cancel_timer: Callable[[], None] | None = None

    @callback
    def async_start(self) -> None:
        if self._sensor is not None:
            self.entry.async_on_unload(self._async_stop)
            self.entry.async_on_unload(
                self.entry.runtime_data.async_add_listener(self._async_board_changed)
            )
            self.entry.async_on_unload(
                async_track_state_change_event(
                    self.hass, self._sensor, self._async_plug_changed
                )
            )
            self._async_board_changed()

    @callback
    def _async_stop(self) -> None:
        self._async_cancel()
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)

    @callback
    def _async_cancel(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _async_plug_changed(self, event: Event[EventStateChangedData]) -> None:
        self._async_evaluate(event.data["new_state"])

    @callback
    def _async_board_changed(self) -> None:
        assert self._sensor is not None
        self._async_evaluate(self.hass.states.get(self._sensor))

    def _mismatch_delay(self, plug: State | None) -> int | None:
        coordinator = self.entry.runtime_data
        reported = coordinator.data.power_state
        watts = _watts(plug)
        if (
            not coordinator.last_update_success
            or not reported.is_known
            or watts is None
        ):
            return None
        if reported in _ACTIVE_STATES:
            return _LOW_POWER_DELAY if watts < LOW_POWER_THRESHOLD else 0
        return _HIGH_POWER_DELAY if watts >= self._threshold else 0

    @callback
    def _async_evaluate(
        self, plug: State | None, *, timer_elapsed: bool = False
    ) -> None:
        delay = self._mismatch_delay(plug)
        if delay != self._pending_delay:
            self._async_cancel()
            self._pending_delay = delay
            if delay:
                self._cancel_timer = async_call_later(
                    self.hass, delay, self._async_confirm
                )
            self._async_publish(None if delay is None else False)
        elif timer_elapsed and delay:
            self._async_publish(True)

    @callback
    def _async_confirm(self, _now: datetime) -> None:
        self._cancel_timer = None
        assert self._sensor is not None
        self._async_evaluate(self.hass.states.get(self._sensor), timer_elapsed=True)

    @callback
    def _async_publish(self, value: bool | None) -> None:
        if value == self.value:
            return
        if value:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=_MISMATCH,
                translation_placeholders={"stove": self.entry.title},
            )
        elif self.value:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        self.value = value
        async_dispatcher_send(self.hass, self.signal)


class PstoveBoardStateMismatch(PstoveEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = _MISMATCH

    def __init__(self, entry: PstoveConfigEntry, detector: BoardStateDetector) -> None:
        super().__init__(entry, _MISMATCH)
        self._detector = detector

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._detector.signal, self.async_write_ha_state
            )
        )

    @property
    def available(self) -> bool:
        return self._detector.value is not None

    @property
    def is_on(self) -> bool | None:
        return self._detector.value
