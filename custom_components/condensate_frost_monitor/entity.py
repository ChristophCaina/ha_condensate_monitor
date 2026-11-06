"""Gemeinsame Basis für alle Entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN
from .monitor import CondensateMonitor


class CondensateEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, monitor: CondensateMonitor, description: EntityDescription) -> None:
        self.monitor = monitor
        self.entity_description = description
        entry = monitor.entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Community",
            model="Kondensat-Frostwächter",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.monitor.async_add_listener(self.async_write_ha_state))
