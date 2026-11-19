"""Config flow for Store Receipts."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_CODE, CONF_PROVIDER, CONF_REFRESH_TOKEN, DOMAIN
from .errors import ReceiptApiError, ReceiptAuthError
from .providers import PROVIDERS

_LOGGER = logging.getLogger(__name__)


class StoreReceiptsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._provider_key: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the store."""
        if user_input is not None:
            self._provider_key = user_input[CONF_PROVIDER]
            await self.async_set_unique_id(f"{DOMAIN}_{self._provider_key}")
            self._abort_if_unique_id_configured()
            return await self.async_step_credentials()

        schema = vol.Schema(
            {vol.Required(CONF_PROVIDER): vol.In({k: p.name for k, p in PROVIDERS.items()})}
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in to the chosen store."""
        return await self._async_login("credentials", self._provider_key, False, user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Log in again."""
        key = self._get_reauth_entry().data[CONF_PROVIDER]
        return await self._async_login("reauth_confirm", key, True, user_input)

    async def _async_login(
        self, step_id: str, key: str | None, reauth: bool, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        provider = PROVIDERS[key]
        errors: dict[str, str] = {}

        if user_input is not None:
            code = (user_input.get(CONF_CODE) or "").strip()
            refresh = (user_input.get(CONF_REFRESH_TOKEN) or "").strip()
            session = async_get_clientsession(self.hass)
            tokens: dict[str, Any] | None = None
            try:
                if provider.exchange_code and provider.extract_code and code:
                    tokens = await provider.exchange_code(session, provider.extract_code(code))
                elif refresh:
                    tokens = await provider.refresh_tokens(session, refresh)
                else:
                    errors["base"] = "no_input"
            except (ValueError, ReceiptAuthError) as err:
                _LOGGER.warning("%s login rejected: %s", provider.name, str(err)[:300])
                errors["base"] = "invalid_auth"
            except ReceiptApiError as err:
                _LOGGER.warning("%s login failed: %s", provider.name, str(err)[:300])
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "unknown"

            if tokens and not errors:
                if reauth:
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data_updates=tokens
                    )
                return self.async_create_entry(
                    title=provider.name, data={**tokens, CONF_PROVIDER: key}
                )

        if provider.exchange_code:
            schema = vol.Schema(
                {vol.Optional(CONF_REFRESH_TOKEN): str, vol.Optional(CONF_CODE): str}
            )
        else:
            schema = vol.Schema({vol.Required(CONF_REFRESH_TOKEN): str})
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders={"store": provider.name},
        )
