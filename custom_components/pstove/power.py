"""Power command state shared by the thermostat and recovery button."""

import asyncio
from dataclasses import dataclass, field

from aiopelletstove import PowerState
from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN


@dataclass
class PendingPower:
    on: bool
    deadline: float | None
    acknowledged: bool = True
    optimistic_while: PowerState | None = None

    @property
    def optimistic(self) -> bool:
        return self.deadline is not None and self.optimistic_while is not None


@dataclass
class PowerTracker:
    """Serialize toggles and retain uncertain outcomes across entry reloads."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: PendingPower | None = None
    direct_read: tuple[PowerState, float] | None = None


_POWER_TRACKERS = HassKey[dict[str, PowerTracker]](DOMAIN)


def power_trackers(hass: HomeAssistant) -> dict[str, PowerTracker]:
    return hass.data.setdefault(_POWER_TRACKERS, {})


def power_tracker(hass: HomeAssistant, entry_id: str) -> PowerTracker:
    return power_trackers(hass).setdefault(entry_id, PowerTracker())


def power_cleared_signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_power_cleared"
