from importlib.util import find_spec
from unittest.mock import AsyncMock, patch

import pytest
from aiopelletstove import FanRegulation, PowerState, StoveState

from custom_components.pstove.const import DOMAIN

if find_spec("pytest_homeassistant"):
    from pytest_homeassistant.common import MockConfigEntry
else:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def custom_integration(enable_custom_integrations):
    yield


@pytest.fixture
def stove_state():
    return StoveState(
        air_temperature=21.5,
        flue_temperature=118,
        power_state=PowerState.RUNNING,
        current_power=3,
        fan_speed=860,
        target_temperature=23,
        power_level=3,
        standby_mode=True,
        buzzer=False,
        enable_fan=True,
        fan2_regulation=FanRegulation.LEVEL_2,
        fan3_regulation=FanRegulation.AUTO,
        last_alarm=2,
        total_hours=4660,
        partial_hours=266,
        ignition_count=766,
    )


@pytest.fixture
def mock_stove(stove_state):
    with patch("custom_components.pstove.PelletStove", autospec=True) as factory:
        client = factory.return_value
        client.async_get_state.return_value = stove_state
        with patch("custom_components.pstove.config_flow.PelletStove", factory):
            yield client


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pellet Stove",
        unique_id="stove.example",
        minor_version=2,
        data={"host": "stove.example", "name": "Pellet Stove"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def reload_entry():
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload",
        new_callable=AsyncMock,
    ) as reload:
        yield reload


@pytest.fixture
async def setup_entry(hass, entry, mock_stove):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
