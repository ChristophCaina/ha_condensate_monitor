"""Berechnete Sensoren."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfPrecipitationDepth,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CondensateConfigEntry
from .calc import Result
from .const import STATUS_OPTIONS
from .entity import CondensateEntity

TEMP_DELTA = getattr(SensorDeviceClass, "TEMPERATURE_DELTA", None)


@dataclass(frozen=True, kw_only=True)
class CondensateSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Result], Any]
    requires_soil: bool = False
    requires: str | None = None  # "humidity", "rain" oder "wind"


def _delta(key: str, fn: Callable[[Result], Any], *, soil: bool = False,
           requires: str | None = None) -> CondensateSensorDescription:
    return CondensateSensorDescription(
        key=key,
        translation_key=key,
        device_class=TEMP_DELTA,
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=fn,
        requires_soil=soil,
        requires=requires,
    )


SENSORS: tuple[CondensateSensorDescription, ...] = (
    CondensateSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda r: r.status,
    ),
    CondensateSensorDescription(
        key="baseline",
        translation_key="baseline",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.baseline,
    ),
    _delta("deviation", lambda r: r.deviation),
    CondensateSensorDescription(
        key="slope",
        translation_key="slope",
        native_unit_of_measurement="K/h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda r: r.slope,
    ),
    _delta("span", lambda r: r.span),
    _delta("delta_outdoor", lambda r: r.delta_outdoor),
    _delta("frost_degree", lambda r: r.frost_degree),
    CondensateSensorDescription(
        key="frost_degree_hours",
        translation_key="frost_degree_hours",
        native_unit_of_measurement="K·h",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_fn=lambda r: r.frost_degree_hours,
    ),
    _delta("delta_soil", lambda r: r.delta_soil, soil=True),
    CondensateSensorDescription(
        key="soil_trend",
        translation_key="soil_trend",
        native_unit_of_measurement="K/d",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda r: r.soil_trend,
        requires_soil=True,
    ),
    CondensateSensorDescription(
        key="dewpoint",
        translation_key="dewpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.dewpoint,
        requires="computed_dewpoint",
    ),
    CondensateSensorDescription(
        key="dewpoint_spread",
        translation_key="dewpoint_spread",
        device_class=TEMP_DELTA,
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.dewpoint_spread,
        requires="humidity",
    ),
    CondensateSensorDescription(
        key="rain_24h",
        translation_key="rain_24h",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        suggested_display_precision=1,
        value_fn=lambda r: r.rain_24h,
        requires="rain",
    ),
    CondensateSensorDescription(
        key="rain_7d",
        translation_key="rain_7d",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=UnitOfPrecipitationDepth.MILLIMETERS,
        suggested_display_precision=1,
        value_fn=lambda r: r.rain_7d,
        requires="rain",
    ),
    CondensateSensorDescription(
        key="wind_1h",
        translation_key="wind_1h",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.METERS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda r: r.wind_1h,
        requires="wind",
    ),
    CondensateSensorDescription(
        key="forecast_min_24h",
        translation_key="forecast_min_24h",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda r: r.forecast.min_24h if r.forecast else None,
        requires="forecast",
    ),
    CondensateSensorDescription(
        key="forecast_min_48h",
        translation_key="forecast_min_48h",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
        value_fn=lambda r: r.forecast.min_48h if r.forecast else None,
        requires="forecast",
    ),
    CondensateSensorDescription(
        key="forecast_frost_hours",
        translation_key="forecast_frost_hours",
        native_unit_of_measurement="h",
        suggested_display_precision=0,
        value_fn=lambda r: r.forecast.frost_hours_48h if r.forecast else None,
        requires="forecast",
    ),
    CondensateSensorDescription(
        key="forecast_frost_degree_hours",
        translation_key="forecast_frost_degree_hours",
        native_unit_of_measurement="K·h",
        suggested_display_precision=1,
        value_fn=lambda r: r.forecast.frost_degree_hours_48h if r.forecast else None,
        requires="forecast",
    ),
    CondensateSensorDescription(
        key="forecast_defrost_hours",
        translation_key="forecast_defrost_hours",
        native_unit_of_measurement="h",
        suggested_display_precision=0,
        value_fn=lambda r: r.forecast.defrost_hours_24h if r.forecast else None,
        requires="forecast",
    ),
    CondensateSensorDescription(
        key="defrost_count_24h",
        translation_key="defrost_count_24h",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda r: r.defrost_count_24h,
        requires="defrost",
    ),
    CondensateSensorDescription(
        key="last_defrost",
        translation_key="last_defrost",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda r: dt_util.utc_from_timestamp(r.last_defrost_start) if r.last_defrost_start else None,
        requires="defrost",
    ),
    CondensateSensorDescription(
        key="last_defrost_duration",
        translation_key="last_defrost_duration",
        native_unit_of_measurement="min",
        suggested_display_precision=0,
        value_fn=lambda r: r.last_defrost_duration_min,
        requires="defrost",
    ),
    _delta("last_defrost_peak", lambda r: r.last_defrost_peak, requires="defrost"),
    CondensateSensorDescription(
        key="last_defrost_recovery",
        translation_key="last_defrost_recovery",
        native_unit_of_measurement="min",
        suggested_display_precision=0,
        value_fn=lambda r: r.last_defrost_recovery_min,
        requires="defrost",
    ),
    CondensateSensorDescription(
        key="pan_heater_hours",
        translation_key="pan_heater_hours",
        native_unit_of_measurement="h",
        suggested_display_precision=1,
        value_fn=lambda r: r.pan_heater_hours_24h,
        requires="pan_heater",
    ),
    CondensateSensorDescription(
        key="heat_cable_hours",
        translation_key="heat_cable_hours",
        native_unit_of_measurement="h",
        suggested_display_precision=1,
        value_fn=lambda r: r.heat_cable_hours_24h,
        requires="heat_cable",
    ),
    CondensateSensorDescription(
        key="buffer_hours",
        translation_key="buffer_hours",
        native_unit_of_measurement="h",
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda r: r.buffer_hours,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CondensateConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    monitor = entry.runtime_data
    async_add_entities(
        CondensateSensor(monitor, d)
        for d in SENSORS
        if (not d.requires_soil or monitor.has_soil) and _has(monitor, d.requires)
    )


def _has(monitor, requires: str | None) -> bool:
    return requires is None or getattr(monitor, f"has_{requires}")


class CondensateSensor(CondensateEntity, SensorEntity):
    entity_description: CondensateSensorDescription

    @property
    def available(self) -> bool:
        r = self.monitor.result
        return r is not None and self.entity_description.value_fn(r) is not None

    @property
    def native_value(self) -> Any:
        r = self.monitor.result
        if r is None:
            return None
        value = self.entity_description.value_fn(r)
        if isinstance(value, float):
            return round(value, 3)
        return value

    @property
    def last_reset(self) -> datetime | None:
        if self.entity_description.key == "frost_degree_hours":
            return self.monitor.season_start
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        r = self.monitor.result
        if r is None:
            return None
        key = self.entity_description.key
        if key == "status":
            return {"gruende": r.reasons}
        if key == "baseline":
            return {"eingefroren": r.baseline_frozen}
        if key in ("rain_24h", "rain_7d"):
            return {
                "abdeckung_tage": r.rain_coverage_days,
                "messpunkte": len(self.monitor.analyzer.rain.points),
            }
        if key == "buffer_hours":
            return {"abtastungen": r.sample_count}
        if key == "forecast_min_24h" and r.forecast and r.forecast.min_24h_at:
            return {
                "zeitpunkt": dt_util.utc_from_timestamp(r.forecast.min_24h_at).isoformat(),
                "vorhersage_typ": self.monitor.forecast_type,
                "schnee_erwartet": r.forecast.snow_expected,
            }
        return None
