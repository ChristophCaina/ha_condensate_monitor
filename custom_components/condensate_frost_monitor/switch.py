"""Wartungsmodus: unterdrückt Meldungen, solange am Kondensatablauf gearbeitet wird."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import CondensateConfigEntry
from .entity import CondensateEntity

MAINTENANCE = SwitchEntityDescription(
    key="maintenance",
    translation_key="maintenance",
    entity_category=EntityCategory.CONFIG,
    icon="mdi:wrench-clock",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CondensateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([MaintenanceSwitch(entry.runtime_data, MAINTENANCE)])


class MaintenanceSwitch(CondensateEntity, SwitchEntity, RestoreEntity):
    @property
    def is_on(self) -> bool:
        return self.monitor.maintenance_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        until = self.monitor.maintenance_until
        if until is None or until == float("inf"):
            return {"endet_um": None}
        return {"endet_um": dt_util.utc_from_timestamp(until).isoformat()}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None or last.state != "on" or self.monitor.maintenance_active:
            return
        ends = last.attributes.get("endet_um")
        if ends is None:
            self.monitor.async_set_maintenance(True, float("inf"))
            return
        until = dt_util.parse_datetime(ends)
        if until is not None and until > dt_util.utcnow():
            self.monitor.async_set_maintenance(True, until.timestamp())

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.monitor.async_set_maintenance(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.monitor.async_set_maintenance(False)
