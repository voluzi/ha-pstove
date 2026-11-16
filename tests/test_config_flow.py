from unittest.mock import patch

import pytest
from aiopelletstove import PelletStoveConnectionError, PelletStoveResponseError
from homeassistant import config_entries, data_entry_flow


async def test_user_flow_normalizes_host(hass, mock_stove):
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_setup", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            "pstove",
            context={"source": config_entries.SOURCE_USER},
            data={"host": " STOVE.EXAMPLE ", "name": "Study stove"},
        )
        await hass.async_block_till_done()
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Study stove"
    assert result["data"] == {"host": "stove.example", "name": "Study stove"}
    assert result["result"].unique_id == "stove.example"
    mock_stove.async_get_state.assert_awaited_once_with(include_diagnostics=False)


@pytest.mark.parametrize(
    "error,expected",
    [
        (PelletStoveConnectionError, "cannot_connect"),
        (PelletStoveResponseError, "unknown"),
    ],
)
async def test_validation_errors(hass, mock_stove, error, expected):
    mock_stove.async_get_state.side_effect = error
    result = await hass.config_entries.flow.async_init(
        "pstove",
        context={"source": config_entries.SOURCE_USER},
        data={"host": "stove.example"},
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_duplicate_host_aborts_before_contact(hass, entry, mock_stove):
    result = await hass.config_entries.flow.async_init(
        "pstove",
        context={"source": config_entries.SOURCE_USER},
        data={"host": "STOVE.EXAMPLE"},
    )
    assert result["reason"] == "already_configured"
    mock_stove.async_get_state.assert_not_awaited()


@pytest.mark.parametrize("interval", [10, 60, 300])
async def test_options_reload(hass, setup_entry, reload_entry, interval):
    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval": interval},
    )
    await hass.async_block_till_done()
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert setup_entry.options == {"scan_interval": interval}
    reload_entry.assert_awaited_once_with(setup_entry.entry_id)


@pytest.mark.parametrize("interval", [9, 301])
async def test_options_reject_out_of_range(hass, setup_entry, interval):
    import voluptuous as vol

    result = await hass.config_entries.options.async_init(setup_entry.entry_id)
    with pytest.raises(vol.Invalid):
        result["data_schema"]({"scan_interval": interval})
