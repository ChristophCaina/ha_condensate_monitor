"""Berechnete Zustände (mit eingebauter Verzögerung gegen Fehlalarme)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CondensateConfigEntry
from .calc import Result
from .entity import CondensateEntity


@dataclass(frozen=True, kw_only=True)
class CondensateBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Result], bool | None]
    requires_soil: bool = False
    requires: str | None = None


BINARY_SENSORS: tuple[CondensateBinaryDescription, ...] = (
    CondensateBinaryDescription(
        key="freezing_plateau",
        translation_key="freezing_plateau",
        device_class=BinarySensorDeviceClass.COLD,
        value_fn=lambda r: r.plateau,
    ),
    CondensateBinaryDescription(
        key="below_zero",
        translation_key="below_zero",
        device_class=BinarySensorDeviceClass.COLD,
        value_fn=lambda r: r.below_zero,
    ),
    CondensateBinaryDescription(
        key="stagnant_water",
        translation_key="stagnant_water",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda r: r.stagnant,
    ),
    CondensateBinaryDescription(
        key="soil_frost",
        translation_key="soil_frost",
        device_class=BinarySensorDeviceClass.COLD,
        value_fn=lambda r: r.soil_frost,
        requires_soil=True,
    ),
    CondensateBinaryDescription(
        key="defrost_weather",
        translation_key="defrost_weather",
        device_class=BinarySensorDeviceClass.COLD,
        value_fn=lambda r: r.defrost_weather,
        requires="humidity",
    ),
    CondensateBinaryDescription(
        key="wet_pit",
        translation_key="wet_pit",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value_fn=lambda r: r.wet_pit,
        requires="rain",
    ),
    CondensateBinaryDescription(
        key="wind_chill",
        translation_key="wind_chill",
        device_class=BinarySensorDeviceClass.COLD,
        value_fn=lambda r: r.wind_chill,
        requires="wind",
    ),
    CondensateBinaryDescription(
        key="forecast_frost",
        translation_key="forecast_frost",
        device_class=BinarySensorDeviceClass.COLD,
        value_fn=lambda r: r.forecast.frost if r.forecast else None,
        requires="forecast",
    ),
    CondensateBinaryDescription(
        key="radiative_frost",
        translation_key="radiative_frost",
        device_class=BinarySensorDeviceClass.COLD,
        value_fn=lambda r: r.forecast.radiative_frost if r.forecast else None,
        requires="forecast",
    ),
    CondensateBinaryDescription(
        key="defrost_active",
        translation_key="defrost_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda r: r.defrost_active,
        requires="defrost",
    ),
    CondensateBinaryDescription(
        key="defrost_no_reaction",
        translation_key="defrost_no_reaction",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda r: r.defrost_no_reaction,
        requires="defrost",
    ),
    CondensateBinaryDescription(
        key="pan_frost",
        translation_key="pan_frost",
        device_class=BinarySensorDeviceClass.COLD,
        value_fn=lambda r: r.pan_frost,
        requires="pan_temp",
    ),
    CondensateBinaryDescription(
        key="sensor_fault",
        translation_key="sensor_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r: r.fault,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CondensateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    monitor = entry.runtime_data
    async_add_entities(
        CondensateBinarySensor(monitor, d)
        for d in BINARY_SENSORS
        if (not d.requires_soil or monitor.has_soil)
        and (d.requires is None or getattr(monitor, f"has_{d.requires}"))
    )


class CondensateBinarySensor(CondensateEntity, BinarySensorEntity):
    entity_description: CondensateBinaryDescription

    @property
    def available(self) -> bool:
        return self.monitor.result is not None

    @property
    def is_on(self) -> bool | None:
        r = self.monitor.result
        return None if r is None else self.entity_description.value_fn(r)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        r = self.monitor.result
        if r is not None and self.entity_description.key == "sensor_fault":
            return {"gruende": r.fault_reasons}
        if r is not None and self.entity_description.key == "defrost_no_reaction":
            return {"abtauungen_ohne_reaktion_in_folge": r.defrost_misses}
        if r is not None and self.entity_description.key == "pan_frost":
            return {"wannentemperatur": r.pan_temp}
        if r is not None and self.entity_description.key == "radiative_frost" and r.forecast \
                and r.forecast.radiative_frost_at:
            return {"zeitpunkt": dt_util.utc_from_timestamp(r.forecast.radiative_frost_at).isoformat()}
        return None
