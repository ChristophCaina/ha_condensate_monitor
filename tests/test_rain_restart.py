from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from tests.test_integration import _create, _set, _advance

RAIN = {"unit_of_measurement": "mm"}


async def test_rain_7d_survives_reload_next_day(recorder_mock, hass, freezer):
    from pytest_homeassistant_custom_component.components.recorder.common import (
        async_wait_recording_done,
    )

    start = dt_util.utcnow()
    freezer.move_to(start - timedelta(days=12))
    hass.states.async_set("sensor.regen", 100.0, RAIN)
    await hass.async_block_till_done()
    freezer.move_to(start)
    _set(hass)
    await async_wait_recording_done(hass)

    entry = await _create(hass, with_soil=False, weather={"rain_sensor": "sensor.regen"})
    print("first", entry.runtime_data.analyzer.rain.points)
    assert hass.states.get("sensor.kondensat_precipitation_7_days").state == "0.0"

    # Einen Tag später: Regen, dann Neuladen
    for _ in range(24):
        freezer.tick(timedelta(hours=1))
        _set(hass)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    hass.states.async_set("sensor.regen", 101.5, RAIN)
    await _advance(hass, freezer, 5)
    await async_wait_recording_done(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    print("after", entry.runtime_data.analyzer.rain.points)
    st = hass.states.get("sensor.kondensat_precipitation_7_days")
    assert st.state == "1.5", st
