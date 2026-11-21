"""Sensor for the most recent receipt of a store."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ReceiptConfigEntry
from .const import CONF_PROVIDER, DOMAIN
from .coordinator import ReceiptCoordinator
from .providers import PROVIDERS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReceiptConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor."""
    provider = PROVIDERS[entry.data[CONF_PROVIDER]]
    async_add_entities([LatestReceiptSensor(entry.runtime_data, entry.entry_id, provider.name)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("refresh", {}, "async_refresh_receipt")


class LatestReceiptSensor(CoordinatorEntity[ReceiptCoordinator], SensorEntity):
    """State = date/time of the latest receipt, attributes = the items."""

    _attr_has_entity_name = True
    _attr_translation_key = "latest_receipt"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:receipt-text"
    _unrecorded_attributes = frozenset({"items"})

    def __init__(self, coordinator: ReceiptCoordinator, entry_id: str, store_name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_latest_receipt"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=store_name,
            manufacturer=store_name,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_refresh_receipt(self) -> None:
        """Action: fetch the latest receipt right now."""
        await self.coordinator.async_refresh()

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data
        return data["datetime"] if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if not data:
            return None
        items = data["items"]
        attributes: dict[str, Any] = {
            "receipt_id": data["id"],
            "total": data["total"],
            "item_count": len(items),
            "items": items,
        }
        if data.get("store"):
            attributes["store"] = data["store"]
        if data.get("total_discount") is not None:
            attributes["total_discount"] = data["total_discount"]
        return attributes
