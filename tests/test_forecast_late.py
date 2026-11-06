from datetime import timedelta

from homeassistant.core import SupportsResponse
from homeassistant.util import dt as dt_util

from tests.test_integration import _advance, _create


async def test_forecast_entity_loaded_later(hass, freezer):
    calls = []

    async def get_forecasts(call):
        calls.append(call.data["type"])
        now = dt_util.utcnow()
        return {"weather.astro": {"forecast": [
            {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 5.0, "humidity": 70,
             "wind_speed": 3.6, "cloudcover_percentage": 50}
            for h in range(48)]}}

    hass.services.async_register("weather", "get_forecasts", get_forecasts,
                                 supports_response=SupportsResponse.ONLY)
    entry = await _create(hass, with_soil=False, weather={"forecast_entity": "weather.astro"})
    assert hass.states.get("sensor.kondensat_forecast_minimum_24_h").state == "unavailable"
    # Wetter-Entity erscheint erst nach dem Start
    hass.states.async_set("weather.astro", "cloudy", {"temperature_unit": "°C", "wind_speed_unit": "km/h"})
    await hass.async_block_till_done()  # ohne Warten: Abruf direkt beim Erscheinen der Entity
    assert calls, "kein erneuter Abruf"
    assert hass.states.get("sensor.kondensat_forecast_minimum_24_h").state == "5.0"


async def test_forecast_with_datetime_objects(hass, freezer):
    async def get_forecasts(call):
        now = dt_util.utcnow()
        return {"weather.dwd": {"forecast": [
            {"datetime": now + timedelta(hours=h), "temperature": -3.0, "cloud_coverage": 10,
             "wind_speed": 1.0, "dew_point": -4.0}
            for h in range(48)]}}

    hass.services.async_register("weather", "get_forecasts", get_forecasts,
                                 supports_response=SupportsResponse.ONLY)
    hass.states.async_set("weather.dwd", "clear-night", {"temperature_unit": "°C", "wind_speed_unit": "km/h"})
    await _create(hass, with_soil=False, weather={"forecast_entity": "weather.dwd"})
    assert hass.states.get("sensor.kondensat_forecast_minimum_24_h").state == "-3.0"
    assert hass.states.get("binary_sensor.kondensat_radiative_frost_forecast").state == "on"
