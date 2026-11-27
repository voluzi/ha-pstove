"""Pellet stove thermostat with five power presets."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING, Any

from aiopelletstove import (
    PelletStoveError,
    PelletStovePowerOutcomeUnknownError,
    PelletStoveRejectedError,
    PowerResult,
    PowerState,
)
from aiopelletstove.registers import POWER_LEVEL, POWER_STATE, TARGET_TEMPERATURE
from homeassistant.components import persistent_notification
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    DOMAIN,
    POWER_CONFIRM_POLL_INTERVAL,
    POWER_CONFIRM_TIMEOUT,
    power_notification_id,
)
from .coordinator import PstoveConfigEntry
from .entity import PstoveEntity
from .power import PendingPower, PowerTracker, power_cleared_signal, power_tracker

PRESETS = [f"Level {level}" for level in range(1, 6)]

_LOGGER = logging.getLogger(__name__)

_CONFIRMS_ON = frozenset({PowerState.LIGHTING, PowerState.RUNNING})
_CONFIRMS_OFF = frozenset({PowerState.OFF})
_ALARMS = frozenset({PowerState.ALARM, PowerState.ALARM_MEMORY})


def _blocked_error(state: PowerState) -> HomeAssistantError:
    if state == PowerState.FINAL_CLEANING:
        key = "final_cleaning"
    elif state == PowerState.STANDBY:
        key = "standby"
    elif state in _ALARMS:
        key = "alarm"
    else:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="unknown_power_state",
            translation_placeholders={"state": str(state.value)},
        )
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PstoveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([PstoveClimate(entry, power_tracker(hass, entry.entry_id))])


class PstoveClimate(PstoveEntity, ClimateEntity):
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_preset_modes = PRESETS
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 7
    _attr_max_temp = 40
    _attr_target_temperature_step = 1
    _attr_precision = 0.1
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, entry: PstoveConfigEntry, tracker: PowerTracker) -> None:
        super().__init__(entry, "thermostat", "Thermostat")
        self._entry = entry
        self._power = tracker
        self._cancel_poll: Callable[[], None] | None = None
        self._polling = False
        self._attached = False

    @property
    def _notification_id(self) -> str:
        return power_notification_id(self._entry.entry_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._attached = True
        self.async_on_remove(self._detach)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                power_cleared_signal(self._entry.entry_id),
                self._async_power_cleared,
            )
        )
        # A command started by the previous entity may still hold the lock.
        self.hass.async_create_task(self._async_resume_pending())

    async def _async_resume_pending(self) -> None:
        async with self._power.lock:
            pass
        if self._attached and self._power.pending is not None:
            self._start_power_poll()
            self.async_write_ha_state()
            await self._async_poll_power()

    @callback
    def _detach(self) -> None:
        self._attached = False
        self._stop_power_poll()

    @callback
    def _async_power_cleared(self) -> None:
        self._stop_power_poll()
        self.async_write_ha_state()

    def _start_power_poll(self) -> None:
        if self._cancel_poll is None:
            self._cancel_poll = async_track_time_interval(
                self.hass,
                self._async_poll_power,
                timedelta(seconds=POWER_CONFIRM_POLL_INTERVAL),
            )

    def _stop_power_poll(self) -> None:
        if self._cancel_poll is not None:
            self._cancel_poll()
            self._cancel_poll = None

    async def _async_poll_power(self, _now: datetime | None = None) -> None:
        pending = self._power.pending
        if pending is None:
            self._stop_power_poll()
            return
        if self._polling:
            return
        self._polling = True
        read_at = monotonic()
        try:
            state: PowerState | None = PowerState(
                int(await self.coordinator.client.async_read(POWER_STATE))
            )
        except PelletStoveError:
            state = None
        finally:
            self._polling = False
        # A poll that outlived its entity, or a superseded command, must not
        # touch the shared state.
        if not self._attached or self._power.pending is not pending:
            return
        if state is not None and not state.is_known:
            state = None
        if state is not None:
            self._power.direct_read = (state, read_at)
            if state != pending.optimistic_while:
                pending.optimistic_while = None

        requested = "on" if pending.on else "off"
        if state in _ALARMS:
            self._finish_power(
                f"The stove reported an alarm after the power {requested} command. "
                "Check the stove."
            )
            return
        if state in (_CONFIRMS_ON if pending.on else _CONFIRMS_OFF):
            self._finish_power(None)
            return

        if pending.deadline is None or monotonic() < pending.deadline:
            if state is not None:
                self.async_write_ha_state()
            return
        pending.deadline = None
        minutes = POWER_CONFIRM_TIMEOUT // 60
        if not pending.acknowledged:
            problem = (
                f"The power {requested} command may not have reached the stove: "
                f"it did not report the change within {minutes} minutes."
            )
        elif state is None:
            problem = (
                f"The stove acknowledged the power {requested} command but "
                f"could not be read to confirm the change within {minutes} minutes."
            )
        else:
            problem = (
                f"The stove acknowledged the power {requested} command but "
                f"did not report the change within {minutes} minutes."
            )
        self._publish_power(
            f"{problem} Check the stove before pressing Clear pending power command "
            "to reset the guard manually."
        )

    @callback
    def _finish_power(self, problem: str | None) -> None:
        self._power.pending = None
        self._stop_power_poll()
        self._publish_power(problem)

    @callback
    def _publish_power(self, problem: str | None) -> None:
        if problem is None:
            persistent_notification.async_dismiss(self.hass, self._notification_id)
        else:
            _LOGGER.warning("%s: %s", self.entity_id, problem)
            persistent_notification.async_create(
                self.hass,
                problem,
                title=f"{self._entry.title}: power command not confirmed",
                notification_id=self._notification_id,
            )
        self.async_write_ha_state()
        self.hass.async_create_task(self.coordinator.async_request_refresh())

    @property
    def native_current_temperature(self) -> float:
        return self.coordinator.data.air_temperature

    @property
    def native_target_temperature(self) -> float:
        return self.coordinator.data.target_temperature

    @property
    def _power_state(self) -> PowerState:
        """Newest known power state from a direct read or a full poll."""
        if self._power.direct_read is not None:
            state, read_at = self._power.direct_read
            if self.coordinator.sampled_at < read_at:
                return state
        return self.coordinator.data.power_state

    @callback
    def _handle_coordinator_update(self) -> None:
        if (
            self._power.direct_read is not None
            and self.coordinator.sampled_at >= self._power.direct_read[1]
        ):
            self._power.direct_read = None
        super()._handle_coordinator_update()

    @property
    def hvac_mode(self) -> HVACMode:
        state = self._power_state
        # Standby is armed: the stove relights when the room cools.
        on = state.is_on or state == PowerState.STANDBY
        if (pending := self._power.pending) is not None and pending.optimistic:
            on = pending.on
        return HVACMode.HEAT if on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if (pending := self._power.pending) is not None and pending.optimistic:
            return HVACAction.PREHEATING if pending.on else HVACAction.OFF
        state = self._power_state
        if state == PowerState.STANDBY:
            return HVACAction.IDLE
        if not state.is_on:
            return HVACAction.OFF
        if state == PowerState.RUNNING and self.coordinator.data.current_power == 0:
            return HVACAction.IDLE
        return HVACAction.HEATING

    @property
    def preset_mode(self) -> str:
        return f"Level {self.coordinator.data.power_level}"

    if not TYPE_CHECKING and not hasattr(ClimateEntity, "native_temperature_unit"):
        _attr_temperature_unit = _attr_native_temperature_unit
        current_temperature = native_current_temperature
        target_temperature = native_target_temperature

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in self._attr_hvac_modes:
            raise ServiceValidationError("Unsupported HVAC mode")
        on = hvac_mode == HVACMode.HEAT
        async with self._power.lock:
            if not self._attached:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="thermostat_unavailable",
                )
            if (pending := self._power.pending) is not None:
                if pending.on == on:
                    return
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="power_change_pending",
                )
            try:
                outcome = await self.coordinator.client.async_set_power(on)
            except (PelletStovePowerOutcomeUnknownError, asyncio.CancelledError) as err:
                # Cancellation can interrupt a toggle after it was sent.
                self._power.pending = PendingPower(
                    on, monotonic() + POWER_CONFIRM_TIMEOUT, acknowledged=False
                )
                self._after_power_command()
                if isinstance(err, asyncio.CancelledError):
                    raise
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="power_unacknowledged",
                ) from err
            except PelletStoveRejectedError as err:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="power_rejected",
                ) from err
            except PelletStoveError as err:
                raise HomeAssistantError("Pellet stove command failed") from err
            if outcome.result == PowerResult.TOGGLED:
                self._power.direct_read = None
                self._power.pending = PendingPower(
                    on,
                    monotonic() + POWER_CONFIRM_TIMEOUT,
                    optimistic_while=outcome.state,
                )
        if outcome.result == PowerResult.BLOCKED:
            raise _blocked_error(outcome.state)
        if outcome.result == PowerResult.UNCHANGED:
            await self.coordinator.async_request_refresh()
            return
        self._after_power_command()

    def _after_power_command(self) -> None:
        if self._attached:
            persistent_notification.async_dismiss(self.hass, self._notification_id)
            self._start_power_poll()
            self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if ATTR_TEMPERATURE in kwargs:
            temperature = float(kwargs[ATTR_TEMPERATURE])
            if not temperature.is_integer():
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="whole_temperature_required",
                )
            await self.async_command(
                self.coordinator.client.async_write(
                    TARGET_TEMPERATURE,
                    int(temperature),
                )
            )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in PRESETS:
            raise ServiceValidationError("Unsupported power preset")
        await self.async_command(
            self.coordinator.client.async_write(
                POWER_LEVEL,
                PRESETS.index(preset_mode) + 1,
            )
        )
