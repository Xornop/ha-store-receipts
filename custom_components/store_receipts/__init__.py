"""Store Receipts: the latest receipt of your stores as Home Assistant sensors."""
from __future__ import annotations

from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_PROVIDER, PLATFORMS
from .coordinator import ReceiptCoordinator, persist_tokens
from .providers import PROVIDERS

type ReceiptConfigEntry = ConfigEntry[ReceiptCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ReceiptConfigEntry) -> bool:
    """Set up one store from a config entry."""
    provider = PROVIDERS[entry.data[CONF_PROVIDER]]
    api = provider.api_class(
        async_get_clientsession(hass),
        dict(entry.data),
        token_callback=partial(persist_tokens, hass, entry),
    )
    coordinator = ReceiptCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ReceiptConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
