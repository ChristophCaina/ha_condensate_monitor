"""Kondensat-Frostwächter: überwacht den Kondensatablauf einer Luft-Wärmepumpe auf Frost und Rückstau."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .monitor import CondensateMonitor

type CondensateConfigEntry = ConfigEntry[CondensateMonitor]


async def async_setup_entry(hass: HomeAssistant, entry: CondensateConfigEntry) -> bool:
    monitor = CondensateMonitor(hass, entry)
    await monitor.async_start()
    entry.runtime_data = monitor
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CondensateConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: CondensateConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
