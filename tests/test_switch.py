import pytest
from aiopelletstove.registers import BUZZER, ENABLE_FAN, STANDBY_MODE
from homeassistant.helpers import entity_registry as er


@pytest.mark.parametrize(
    "name,register,state",
    [
        ("standby", STANDBY_MODE, "on"),
        ("buzzer", BUZZER, "off"),
        ("fan", ENABLE_FAN, "on"),
    ],
)
async def test_setting_switch(hass, setup_entry, mock_stove, name, register, state):
    entity_id = f"switch.pellet_stove_{name}"
    assert hass.states.get(entity_id).state == state
    assert er.async_get(hass).async_get(entity_id).entity_category == "config"
    for service, value in [("turn_off", False), ("turn_on", True)]:
        await hass.services.async_call(
            "switch", service, {"entity_id": entity_id}, blocking=True
        )
        mock_stove.async_write.assert_awaited_with(register, value)
