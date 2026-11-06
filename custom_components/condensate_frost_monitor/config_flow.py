"""Einrichtung über die Oberfläche."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_OUTDOOR_SENSOR,
    CONF_PIPE_SENSOR,
    CONF_DEFROST_ENTITY,
    CONF_DEWPOINT_SENSOR,
    CONF_FORECAST_ENTITY,
    CONF_HEAT_CABLE_ENTITY,
    CONF_PAN_HEATER_ENTITY,
    CONF_PAN_TEMP_SENSOR,
    HEATPUMP_SENSORS,
    CONF_HUMIDITY_SENSOR,
    CONF_RAIN_SENSOR,
    CONF_SOIL_SENSOR,
    CONF_WIND_SENSOR,
    DOMAIN,
    THRESHOLD_SECTIONS,
    THRESHOLDS,
    WEATHER_SENSORS,
)

DEFAULT_NAME = "Kondensat-Frostwächter"


def _temp_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.TEMPERATURE)
    )


def _sensor_selector(device_class: SensorDeviceClass | None = None) -> selector.EntitySelector:
    if device_class is None:
        return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class=device_class)
    )


WEATHER_SELECTORS = {
    CONF_HUMIDITY_SENSOR: lambda: _sensor_selector(SensorDeviceClass.HUMIDITY),
    CONF_DEWPOINT_SENSOR: lambda: _sensor_selector(SensorDeviceClass.TEMPERATURE),
    CONF_RAIN_SENSOR: lambda: _sensor_selector(),
    CONF_WIND_SENSOR: lambda: _sensor_selector(SensorDeviceClass.WIND_SPEED),
    CONF_FORECAST_ENTITY: lambda: selector.EntitySelector(
        selector.EntitySelectorConfig(domain="weather")
    ),
}


def _weather_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Optional(key, description={"suggested_value": defaults.get(key)}): make()
        for key, make in WEATHER_SELECTORS.items()
    })


def _clean_weather(user_input: dict[str, Any]) -> dict[str, Any]:
    return {key: user_input.get(key) or None for key in WEATHER_SENSORS}


SWITCHLIKE = ["binary_sensor", "switch", "input_boolean"]

HEATPUMP_SELECTORS = {
    CONF_DEFROST_ENTITY: lambda: selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["sensor", *SWITCHLIKE])
    ),
    CONF_PAN_TEMP_SENSOR: lambda: _sensor_selector(SensorDeviceClass.TEMPERATURE),
    CONF_PAN_HEATER_ENTITY: lambda: selector.EntitySelector(
        selector.EntitySelectorConfig(domain=SWITCHLIKE)
    ),
    CONF_HEAT_CABLE_ENTITY: lambda: selector.EntitySelector(
        selector.EntitySelectorConfig(domain=SWITCHLIKE)
    ),
}


def _heatpump_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Optional(key, description={"suggested_value": defaults.get(key)}): make()
        for key, make in HEATPUMP_SELECTORS.items()
    })


def _clean_heatpump(user_input: dict[str, Any]) -> dict[str, Any]:
    return {key: user_input.get(key) or None for key in HEATPUMP_SENSORS}


def _validate(user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    pipe = user_input.get(CONF_PIPE_SENSOR)
    outdoor = user_input.get(CONF_OUTDOOR_SENSOR)
    soil = user_input.get(CONF_SOIL_SENSOR)
    if pipe == outdoor:
        errors[CONF_OUTDOOR_SENSOR] = "same_sensor"
    if soil and soil in (pipe, outdoor):
        errors[CONF_SOIL_SENSOR] = "same_sensor"
    return errors


def _sensor_schema(defaults: dict[str, Any], with_name: bool) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME))] = str
    fields[vol.Required(CONF_PIPE_SENSOR, default=defaults.get(CONF_PIPE_SENSOR, vol.UNDEFINED))] = _temp_selector()
    fields[vol.Required(CONF_OUTDOOR_SENSOR, default=defaults.get(CONF_OUTDOOR_SENSOR, vol.UNDEFINED))] = _temp_selector()
    soil_default = defaults.get(CONF_SOIL_SENSOR)
    soil_key = (
        vol.Optional(CONF_SOIL_SENSOR, description={"suggested_value": soil_default})
    )
    fields[soil_key] = _temp_selector()
    return vol.Schema(fields)


def _available(cfg: dict[str, Any]) -> set[str]:
    have: set[str] = set()
    if cfg.get(CONF_SOIL_SENSOR):
        have.add("soil")
    if cfg.get(CONF_HUMIDITY_SENSOR) or cfg.get(CONF_DEWPOINT_SENSOR):
        have.add("humidity")
    if cfg.get(CONF_RAIN_SENSOR):
        have.add("rain")
    if cfg.get(CONF_WIND_SENSOR):
        have.add("wind")
    if cfg.get(CONF_FORECAST_ENTITY):
        have.add("forecast")
    if cfg.get(CONF_DEFROST_ENTITY):
        have.add("defrost")
    if cfg.get(CONF_PAN_TEMP_SENSOR):
        have.add("pan_temp")
    return have


def _threshold_schema(defaults: dict[str, Any], sensors: dict[str, Any]) -> vol.Schema:
    have = _available(sensors)
    sections: dict[Any, Any] = {}
    for name, fields in THRESHOLD_SECTIONS.items():
        inner: dict[Any, Any] = {}
        for key, requires in fields:
            if requires is not None and requires not in have:
                continue
            default, lo, hi, step, unit = THRESHOLDS[key]
            inner[vol.Required(key, default=defaults.get(key, default))] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=lo, max=hi, step=step, unit_of_measurement=unit,
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
        if inner:
            sections[vol.Required(name)] = section(vol.Schema(inner), {"collapsed": True})
    return vol.Schema(sections)


def _flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for value in user_input.values():
        if isinstance(value, dict):
            flat.update(value)
    return flat


class CondensateFrostMonitorConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._name = ""
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_PIPE_SENSOR])
                self._abort_if_unique_id_configured()
                self._name = user_input.pop(CONF_NAME)
                self._data = {**user_input, CONF_SOIL_SENSOR: user_input.get(CONF_SOIL_SENSOR)}
                return await self.async_step_weather()
        return self.async_show_form(
            step_id="user",
            data_schema=_sensor_schema(user_input or {}, with_name=True),
            errors=errors,
        )

    async def async_step_weather(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Optionale Wetterstations-Sensoren."""
        if user_input is not None:
            self._data.update(_clean_weather(user_input))
            return await self.async_step_heatpump()
        return self.async_show_form(step_id="weather", data_schema=_weather_schema({}))

    async def async_step_heatpump(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Optionale Signale der Wärmepumpe (z. B. über EMS-ESP)."""
        if user_input is not None:
            return self.async_create_entry(
                title=self._name, data={**self._data, **_clean_heatpump(user_input)}
            )
        return self.async_show_form(step_id="heatpump", data_schema=_heatpump_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CondensateFrostMonitorOptionsFlow()


class CondensateFrostMonitorOptionsFlow(OptionsFlow):
    """Sensoren nachträglich ändern (z. B. Erdfühler ergänzen) und Schwellen anpassen."""

    def __init__(self) -> None:
        self._sensors: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(user_input)
            if not errors:
                self._sensors = {
                    CONF_PIPE_SENSOR: user_input[CONF_PIPE_SENSOR],
                    CONF_OUTDOOR_SENSOR: user_input[CONF_OUTDOOR_SENSOR],
                    CONF_SOIL_SENSOR: user_input.get(CONF_SOIL_SENSOR),
                }
                return await self.async_step_weather()
        return self.async_show_form(
            step_id="init",
            data_schema=_sensor_schema(user_input or current, with_name=False),
            errors=errors,
        )

    async def async_step_weather(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            self._sensors.update(_clean_weather(user_input))
            return await self.async_step_heatpump()
        return self.async_show_form(step_id="weather", data_schema=_weather_schema(current))

    async def async_step_heatpump(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            self._sensors.update(_clean_heatpump(user_input))
            return await self.async_step_thresholds()
        return self.async_show_form(step_id="heatpump", data_schema=_heatpump_schema(current))

    async def async_step_thresholds(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            # Nicht angezeigte Schwellen behalten ihren bisherigen Wert
            kept = {k: current[k] for k in THRESHOLDS if k in current}
            return self.async_create_entry(data={**kept, **self._sensors, **_flatten(user_input)})
        return self.async_show_form(
            step_id="thresholds", data_schema=_threshold_schema(current, self._sensors)
        )
