"""Pellet stove thermostat with five power presets."""

from typing import TYPE_CHECKING, Any

from aiopelletstove import PowerState
from aiopelletstove.registers import POWER_LEVEL, TARGET_TEMPERATURE
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import PstoveConfigEntry
from .entity import PstoveEntity

PRESETS = [f"Level {level}" for level in range(1, 6)]


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

    @property
    def native_current_temperature(self) -> float:
        return self.coordinator.data.air_temperature

    @property
    def native_target_temperature(self) -> float:
        return self.coordinator.data.target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        return (
            HVACMode.HEAT if self.coordinator.data.power_state.is_on else HVACMode.OFF
        )

    @property
    def hvac_action(self) -> HVACAction:
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
        await self.async_command(
            self.coordinator.client.async_set_power(hvac_mode == HVACMode.HEAT),
        )

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
