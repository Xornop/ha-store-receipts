"""Data update coordinator for Store Receipts."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL
from .errors import ReceiptApiError, ReceiptAuthError
from .providers.base import BaseApi

_LOGGER = logging.getLogger(__name__)


@callback
def persist_tokens(hass: HomeAssistant, entry: ConfigEntry, tokens: dict[str, Any]) -> None:
    """Store (rotated) tokens in the config entry."""
    if any(entry.data.get(key) != value for key, value in tokens.items()):
        hass.config_entries.async_update_entry(entry, data={**entry.data, **tokens})


class ReceiptCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Fetch the latest receipt periodically."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: BaseApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            update_interval=SCAN_INTERVAL,
            config_entry=entry,
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any] | None:
        try:
            return await self.api.async_get_latest_receipt()
        except ReceiptAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ReceiptApiError as err:
            raise UpdateFailed(str(err)) from err
        finally:
            persist_tokens(self.hass, self.config_entry, self.api.tokens)
