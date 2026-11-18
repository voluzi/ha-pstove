"""User setup and polling options."""

from typing import TYPE_CHECKING, Any

from aiopelletstove import (
    PelletStove,
    PelletStoveConnectionError,
    PelletStoveError,
    PelletStoveValueError,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

if TYPE_CHECKING:
    import probatio as vol
else:
    try:
        import probatio as vol
    except ModuleNotFoundError:
        import voluptuous as vol

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENTRY_MINOR_VERSION,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    REQUEST_TIMEOUT,
)
from .identity import StoveIdentityUnavailableError, async_get_stove_mac


class PstoveConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = ENTRY_MINOR_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_connection_step("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_connection_step(
            "reconfigure", user_input, self._get_reconfigure_entry()
        )

    async def _async_connection_step(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        entry: ConfigEntry | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip().lower().rstrip(".")
            try:
                client = PelletStove(
                    host,
                    session=async_get_clientsession(self.hass),
                    timeout=REQUEST_TIMEOUT,
                )
            except PelletStoveValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                if any(
                    other is not entry and other.data[CONF_HOST] == host
                    for other in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                try:
                    await client.async_get_state(include_diagnostics=False)
                except PelletStoveConnectionError:
                    errors["base"] = "cannot_connect"
                except PelletStoveError:
                    errors["base"] = "unknown"
                else:
                    try:
                        mac = await async_get_stove_mac(self.hass, host)
                    except StoveIdentityUnavailableError:
                        mac = None
                    if entry is None:
                        existing = await self.async_set_unique_id(mac or host)
                        self._abort_if_unique_id_configured(
                            updates={CONF_HOST: host},
                            reload_on_update=existing is None
                            or not existing.update_listeners,
                        )
                        name = (
                            str(user_input.get(CONF_NAME, DEFAULT_NAME)).strip()
                            or DEFAULT_NAME
                        )
                        return self.async_create_entry(
                            title=name,
                            data={CONF_HOST: host, CONF_NAME: name},
                        )
                    current_host = entry.data[CONF_HOST]
                    expected = entry.unique_id
                    if not expected or expected == current_host:
                        matches = host == current_host
                    else:
                        matches = mac == expected
                    if matches:
                        return self.async_update_reload_and_abort(
                            entry, data_updates={CONF_HOST: host}
                        )
                    errors["base"] = "unique_id_mismatch"

        schema: dict[Any, Any] = {
            vol.Required(
                CONF_HOST, default=entry.data[CONF_HOST] if entry else vol.UNDEFINED
            ): str,
        }
        if entry is None:
            schema[vol.Optional(CONF_NAME, default=DEFAULT_NAME)] = str
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), user_input
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PstoveOptionsFlow()


class PstoveOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL,
                            DEFAULT_SCAN_INTERVAL,
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                        ),
                    ),
                }
            ),
        )
