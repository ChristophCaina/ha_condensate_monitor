"""Tests gegen ein echtes Home Assistant: Config Flow, Entities, Options, Entladen."""

from datetime import timedelta

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.condensate_frost_monitor.const import (
    CONF_OUTDOOR_SENSOR,
    CONF_PIPE_SENSOR,
    CONF_SOIL_SENSOR,
    DOMAIN,
)

TEMP = {"unit_of_measurement": "°C", "device_class": "temperature"}


def _set(hass, pipe=15.1, outdoor=12.0, soil=15.0):
    hass.states.async_set("sensor.rohr", pipe, TEMP)
    hass.states.async_set("sensor.aussen", outdoor, TEMP)
    hass.states.async_set("sensor.erde", soil, TEMP)


async def _create(hass, with_soil, weather=None, heatpump=None):
    _set(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    data = {CONF_NAME: "Kondensat", CONF_PIPE_SENSOR: "sensor.rohr", CONF_OUTDOOR_SENSOR: "sensor.aussen"}
    if with_soil:
        data[CONF_SOIL_SENSOR] = "sensor.erde"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    assert result["step_id"] == "weather"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], weather or {})
    assert result["step_id"] == "heatpump"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], heatpump or {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result["result"]


async def _advance(hass, freezer, minutes):
    for _ in range(minutes):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def _ids(hass, entry):
    reg = er.async_get(hass)
    return {e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}


async def test_setup_with_soil(hass, freezer):
    entry = await _create(hass, with_soil=True)
    ids = _ids(hass, entry)
    assert "sensor.kondensat_delta_soil" in ids or any("soil" in i or "boden" in i for i in ids)
    assert len(ids) == 17
    await _advance(hass, freezer, 20)
    states = {i: hass.states.get(i) for i in ids if hass.states.get(i)}
    print({i: (s.state, s.attributes.get("unit_of_measurement")) for i, s in states.items()})
    status = next(s for i, s in states.items() if i.endswith("_status"))
    assert status.state == "ok"
    base = next(s for i, s in states.items() if i.endswith("_baseline"))
    assert float(base.state) == 15.1


async def test_setup_without_soil_has_fewer_entities(hass):
    entry = await _create(hass, with_soil=False)
    assert len(_ids(hass, entry)) == 14


async def test_duplicate_and_same_sensor(hass):
    await _create(hass, with_soil=False)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "x", CONF_PIPE_SENSOR: "sensor.aussen", CONF_OUTDOOR_SENSOR: "sensor.aussen"},
    )
    assert result["errors"] == {CONF_OUTDOOR_SENSOR: "same_sensor"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_NAME: "x", CONF_PIPE_SENSOR: "sensor.rohr", CONF_OUTDOOR_SENSOR: "sensor.aussen"},
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_options_add_soil_and_thresholds(hass):
    entry = await _create(hass, with_soil=False)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_PIPE_SENSOR: "sensor.rohr", CONF_OUTDOOR_SENSOR: "sensor.aussen", CONF_SOIL_SENSOR: "sensor.erde"},
    )
    assert result["step_id"] == "weather"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "heatpump"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "thresholds"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {s: {} for s in ("analysis", "below_zero", "stagnant", "soil", "fault", "maintenance")} | {"plateau": {"plateau_minutes": 30}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["plateau_minutes"] == 30
    assert entry.runtime_data.has_soil
    assert entry.runtime_data.analyzer.settings.plateau_minutes == 30


async def test_alarm_and_fault_and_unload(hass, freezer):
    entry = await _create(hass, with_soil=True)
    await _advance(hass, freezer, 30)
    _set(hass, pipe=-2.0, outdoor=-8.0, soil=1.0)
    await _advance(hass, freezer, 20)
    status = [s for s in hass.states.async_all("sensor") if s.entity_id.endswith("_status")][0]
    assert status.state == "alarm", status
    assert "rohr_unter_null" in status.attributes["gruende"]
    hass.states.async_set("sensor.rohr", "unavailable", TEMP)
    await _advance(hass, freezer, 35)
    fault = [s for s in hass.states.async_all("binary_sensor") if "stor" in s.entity_id or "fault" in s.entity_id][0]
    assert fault.state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_samples_survive_reload(hass, freezer):
    entry = await _create(hass, with_soil=True)
    await _advance(hass, freezer, 20)
    n = len(entry.runtime_data.analyzer.samples)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(entry.runtime_data.analyzer.samples) >= n


async def test_backfill_from_recorder(recorder_mock, hass, freezer):
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    start = dt_util.utcnow()
    freezer.move_to(start - timedelta(hours=3))
    for i in range(18):  # alle 10 min ein Wert über 3 h
        _set(hass, pipe=15.0 + (i % 2) * 0.1, outdoor=-2.0, soil=14.5)
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=10))
    freezer.move_to(start)
    await async_wait_recording_done(hass)

    entry = await _create(hass, with_soil=True)
    an = entry.runtime_data.analyzer
    assert len(an.samples) >= 170
    assert an.frost_degree_hours > 5.5
    base = hass.states.get("sensor.kondensat_baseline")
    assert base is not None and float(base.state) in (15.0, 15.1)
    assert hass.states.get("sensor.kondensat_slope").state != "unavailable"


async def test_backfill_before_existing_buffer(recorder_mock, hass, freezer):
    """Puffer aus früherer Version (nur 20 min) wird nach vorne mit Verlauf ergänzt."""
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    start = dt_util.utcnow()
    freezer.move_to(start - timedelta(hours=22))
    for i in range(22 * 6):
        _set(hass, pipe=15.0, outdoor=10.0, soil=15.0 - i * 0.003)
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=10))
    freezer.move_to(start)
    await async_wait_recording_done(hass)

    entry = await _create(hass, with_soil=True)
    an = entry.runtime_data.analyzer
    # Simulieren: nur die letzten 20 min waren im Speicher
    cutoff = start.timestamp() - 20 * 60
    while an.samples and an.samples[0].ts < cutoff:
        an.samples.popleft()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    trend = hass.states.get("sensor.kondensat_soil_trend")
    assert trend.state != "unavailable", trend
    assert float(trend.state) < 0


async def test_weather_sensors_optional(hass, freezer):
    hass.states.async_set("sensor.feuchte", 95, {"unit_of_measurement": "%", "device_class": "humidity"})
    hass.states.async_set("sensor.regen", 100.0, {"unit_of_measurement": "mm"})
    hass.states.async_set("sensor.wind", 18.0, {"unit_of_measurement": "km/h", "device_class": "wind_speed"})
    entry = await _create(hass, with_soil=True, weather={
        "humidity_sensor": "sensor.feuchte", "rain_sensor": "sensor.regen", "wind_sensor": "sensor.wind",
    })
    assert len(_ids(hass, entry)) == 17 + 5 + 3
    _set(hass, pipe=3.0, outdoor=-2.0, soil=3.0)
    hass.states.async_set("sensor.regen", 112.5, {"unit_of_measurement": "mm"})
    for _ in range(4):  # Fühler melden regelmäßig (wie Ecowitt), sonst gäbe es eine Störung
        await _advance(hass, freezer, 10)
        _set(hass, pipe=3.0, outdoor=-2.0, soil=3.0)
    st = hass.states
    assert abs(float(st.get("sensor.kondensat_wind_1_h_mean").state) - 18.0) < 0.01  # 5 m/s, angezeigt in km/h
    assert float(st.get("sensor.kondensat_precipitation_24_h").state) == 12.5
    assert st.get("binary_sensor.kondensat_defrost_weather").state == "on"
    assert st.get("binary_sensor.kondensat_wind_chill").state == "on"
    status = st.get("sensor.kondensat_status")
    assert status.state == "info" and "auskuehlung_wind" in status.attributes["gruende"]
    assert float(st.get("sensor.kondensat_dew_point").state) < -2.0


async def test_forecast_from_astroweather_like_entity(hass, freezer):
    from homeassistant.core import SupportsResponse

    now = dt_util.utcnow()
    hass.states.async_set("weather.astro", "clear-night", {
        "temperature_unit": "°C", "wind_speed_unit": "km/h", "precipitation_unit": "mm",
    })
    calls = []

    async def get_forecasts(call):
        calls.append(call.data["type"])
        if call.data["type"] != "hourly":
            raise ValueError("unsupported")
        return {"weather.astro": {"forecast": [
            {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 2.0 - h * 0.5,
             "humidity": 90, "wind_speed": 3.6, "cloudcover_percentage": 5,
             "fog2m_area_fraction": 0, "precipitation_amount": 0}
            for h in range(48)
        ]}}

    hass.services.async_register("weather", "get_forecasts", get_forecasts,
                                 supports_response=SupportsResponse.ONLY)
    entry = await _create(hass, with_soil=False, weather={"forecast_entity": "weather.astro"})
    assert calls[0] == "hourly"
    assert len(_ids(hass, entry)) == 14 + 5 + 2
    st = hass.states
    assert float(st.get("sensor.kondensat_forecast_minimum_24_h").state) == 2.0 - 23 * 0.5
    assert st.get("binary_sensor.kondensat_frost_forecast").state == "on"
    assert st.get("binary_sensor.kondensat_radiative_frost_forecast").state == "on"
    status = st.get("sensor.kondensat_status")
    assert status.state == "info"
    assert "strahlungsfrost_vorhergesagt" in status.attributes["gruende"]


async def test_threshold_sections_follow_configured_sensors(hass):
    entry = await _create(hass, with_soil=False)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIPE_SENSOR: "sensor.rohr", CONF_OUTDOOR_SENSOR: "sensor.aussen"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"rain_sensor": "sensor.aussen"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    names = [str(k) for k in result["data_schema"].schema]
    assert names == ["analysis", "plateau", "below_zero", "stagnant", "weather", "fault", "maintenance"]
    weather = result["data_schema"].schema[next(k for k in result["data_schema"].schema if str(k) == "weather")]
    assert [str(k) for k in weather.schema.schema] == ["wet_pit_rain_7d"]


async def test_wind_added_later_is_backfilled(recorder_mock, hass, freezer):
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    start = dt_util.utcnow()
    freezer.move_to(start - timedelta(hours=2))
    for _ in range(12):
        _set(hass)
        hass.states.async_set("sensor.wind", 7.2, {"unit_of_measurement": "km/h", "device_class": "wind_speed"})
        await hass.async_block_till_done()
        freezer.tick(timedelta(minutes=10))
    freezer.move_to(start)
    await async_wait_recording_done(hass)

    entry = await _create(hass, with_soil=False)
    await _advance(hass, freezer, 2)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PIPE_SENSOR: "sensor.rohr", CONF_OUTDOOR_SENSOR: "sensor.aussen"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"wind_sensor": "sensor.wind"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {str(k): {} for k in result["data_schema"].schema})
    await hass.async_block_till_done()
    wind = hass.states.get("sensor.kondensat_wind_1_h_mean")
    assert wind.state != "unavailable" and abs(float(wind.state) - 7.2) < 0.01


async def test_dewpoint_entity_only_when_computed(hass):
    hass.states.async_set("sensor.feuchte", 80, {"unit_of_measurement": "%", "device_class": "humidity"})
    hass.states.async_set("sensor.tau", 5.0, TEMP)
    entry = await _create(hass, with_soil=False, weather={
        "humidity_sensor": "sensor.feuchte", "dewpoint_sensor": "sensor.tau"})
    ids = _ids(hass, entry)
    assert not any(i.endswith("_dew_point") for i in ids)
    assert any(i.endswith("_dew_point_spread") for i in ids)
