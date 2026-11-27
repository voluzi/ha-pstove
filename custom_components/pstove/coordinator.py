"""Polling with a separate diagnostics cadence."""

import logging
from dataclasses import replace
from datetime import timedelta
from time import monotonic

from aiopelletstove import PelletStove, PelletStoveError, StoveState
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DIAGNOSTICS_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class PstoveDataUpdateCoordinator(DataUpdateCoordinator[StoveState]):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: PelletStove,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(
                    CONF_SCAN_INTERVAL,
                    DEFAULT_SCAN_INTERVAL,
                )
            ),
            # An equal sample can supersede a direct power read.
            always_update=True,
        )
        self.client = client
        self._next_diagnostics = 0.0
        self._diagnostics: StoveState | None = None
        self.sampled_at = 0.0

    async def _async_update_data(self) -> StoveState:
        now = monotonic()
        include = now >= self._next_diagnostics
        if include:
            self._next_diagnostics = now + DIAGNOSTICS_INTERVAL
        try:
            state = await self.client.async_get_state(include_diagnostics=include)
        except PelletStoveError as err:
            raise UpdateFailed("Unable to update pellet stove") from err
        if include:
            self._diagnostics = state
        elif self._diagnostics is not None:
            state = replace(
                state,
                last_alarm=self._diagnostics.last_alarm,
                total_hours=self._diagnostics.total_hours,
                partial_hours=self._diagnostics.partial_hours,
                ignition_count=self._diagnostics.ignition_count,
            )
        self.sampled_at = now
        return state


type PstoveConfigEntry = ConfigEntry[PstoveDataUpdateCoordinator]
