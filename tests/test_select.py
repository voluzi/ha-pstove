import pytest
from homeassistant.helpers import entity_registry as er


@pytest.mark.parametrize("channel,current", [(2, "2"), (3, "auto")])
async def test_select_calls_packed_fan_api(
    hass, setup_entry, mock_stove, channel, current
):
    entity_id = f"select.pellet_stove_fan_{channel}_regulation"
    state = hass.states.get(entity_id)
    assert state.state == current
    assert state.attributes["options"] == ["off", "1", "2", "3", "4", "5", "auto"]
    assert er.async_get(hass).async_get(entity_id).entity_category == "config"
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": "4"},
        blocking=True,
    )
    mock_stove.async_set_fan_regulation.assert_awaited_once_with(f"fan{channel}", "4")
