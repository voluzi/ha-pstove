"""Pellet stove thermostat with five power presets."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from aiopelletstove import PelletStoveError, PowerState
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
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, POWER_CONFIRM_TIMEOUT
from .coordinator import PstoveConfigEntry
from .entity import PstoveEntity

PRESETS = [f"Level {level}" for level in range(1, 6)]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PstoveConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([PstoveClimate(entry)])


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

    def __init__(self, entry: PstoveConfigEntry) -> None:
        super().__init__(entry, "thermostat", "Thermostat")
        # The stove acknowledges a power toggle at once but only reports the
        # new power state when its start or shutdown sequence begins, often a
        # minute or more later. Until then the requested mode is shown.
        self._pending_on: bool | None = None
        self._cancel_pending: Callable[[], None] | None = None

    @property
    def _stove_name(self) -> str:
        entry = self.coordinator.config_entry
        return entry.title if entry is not None else "Pellet stove"

    @property
    def _notification_id(self) -> str:
        return f"{DOMAIN}_{self.unique_id}_power_unconfirmed"

    def _clear_pending(self) -> None:
        self._pending_on = None
        if self._cancel_pending is not None:
            self._cancel_pending()
            self._cancel_pending = None

    @callback
    def _handle_coordinator_update(self) -> None:
        if (
            self._pending_on is not None
            and self.coordinator.data.power_state.is_on == self._pending_on
        ):
            self._clear_pending()
            persistent_notification.async_dismiss(self.hass, self._notification_id)
        super()._handle_coordinator_update()

    @callback
    def _pending_timed_out(self, _now: object) -> None:
        self._cancel_pending = None
        requested = "on" if self._pending_on else "off"
        self._pending_on = None
        _LOGGER.warning(
            "%s did not confirm power %s within %s seconds",
            self.entity_id,
            requested,
            POWER_CONFIRM_TIMEOUT,
        )
        persistent_notification.async_create(
            self.hass,
            f"The pellet stove acknowledged the power {requested} command but "
            f"did not report the change within {POWER_CONFIRM_TIMEOUT // 60} "
            "minutes. Check the stove.",
            title=f"{self._stove_name}: power {requested} not confirmed",
            notification_id=self._notification_id,
        )
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        self._clear_pending()
        await super().async_will_remove_from_hass()

    @property
    def native_current_temperature(self) -> float:
        return self.coordinator.data.air_temperature

    @property
    def native_target_temperature(self) -> float:
        return self.coordinator.data.target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        on = self.coordinator.data.power_state.is_on
        if self._pending_on is not None:
            on = self._pending_on
        return HVACMode.HEAT if on else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction:
        if self._pending_on is True:
            return HVACAction.PREHEATING
        if self._pending_on is False:
            return HVACAction.OFF
        state = self.coordinator.data
        if not state.power_state.is_on:
            return HVACAction.OFF
        if state.power_state == PowerState.RUNNING and state.current_power == 0:
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
        if self._pending_on is not None:
            # The stove still reports the old state, so a second toggle would
            # cancel the first one instead of being ignored.
            if self._pending_on == on:
                return
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="power_change_pending",
            )
        client = self.coordinator.client
        try:
            current = PowerState(int(await client.async_read(POWER_STATE)))
        except PelletStoveError as err:
            raise HomeAssistantError("Pellet stove command failed") from err
        if on and current == PowerState.FINAL_CLEANING:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="final_cleaning",
            )
        if not current.is_known:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unknown_power_state",
                translation_placeholders={"state": str(current.value)},
            )
        if current.is_on == on:
            await self.coordinator.async_request_refresh()
            return
        await self.async_command(client.async_set_power(on))
        if self.coordinator.data.power_state.is_on == on:
            return
        self._pending_on = on
        self._cancel_pending = async_call_later(
            self.hass, POWER_CONFIRM_TIMEOUT, self._pending_timed_out
        )
        persistent_notification.async_dismiss(self.hass, self._notification_id)
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
