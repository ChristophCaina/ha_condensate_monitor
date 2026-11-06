"""Wärmepumpen-Eingänge: Abtauung (Text wie EMS-ESP hpactivity oder binär), Wanne, Heizungen."""

from custom_components.condensate_frost_monitor.calc import (
    Analyzer,
    Settings,
    parse_defrost_state,
)
from tests.test_integration import TEMP, _advance, _create, _ids, _set

MIN = 60.0
T0 = 1_790_000_000.0


def test_parse_defrost_state():
    assert parse_defrost_state("Abtauen") is True
    assert parse_defrost_state("defrost") is True
    assert parse_defrost_state("Heizen") is False
    assert parse_defrost_state("on") is True
    assert parse_defrost_state("off") is False
    assert parse_defrost_state("unavailable") is None


def _run(an, start, minutes, pipe_fn, defrost_fn):
    r = None
    for m in range(start, start + minutes):
        ts = T0 + m * MIN
        p = round(pipe_fn(m), 1)
        an.add_sample(ts, p, 2.0, None)
        r = an.evaluate(ts, p, 2.0, None, False, False, defrost=defrost_fn(m))
    return r


def test_defrost_with_reaction_and_recovery():
    an = Analyzer(Settings(), has_soil=False, has_defrost=True)
    _run(an, 0, 360, lambda m: 4.0, lambda m: False)
    # Abtauung Minute 360-368, Wasser kommt ab 365 an, erholt sich bis ~400
    pipe = lambda m: 4.0 + (3.0 if 365 <= m < 375 else max(0.0, 3.0 - (m - 375) * 0.1) if m >= 375 else 0.0)
    r = _run(an, 360, 120, pipe, lambda m: 360 <= m < 368)
    assert r.defrost_count_24h == 1
    assert r.last_defrost_duration_min == 8
    assert abs(r.last_defrost_peak - 3.0) < 0.05
    assert r.last_defrost_recovery_min is not None and 25 <= r.last_defrost_recovery_min <= 40
    assert not r.defrost_no_reaction and r.status == "ok"


def test_defrost_without_reaction_warns_after_two():
    an = Analyzer(Settings(), has_soil=False, has_defrost=True)
    _run(an, 0, 360, lambda m: 4.0, lambda m: False)
    r = _run(an, 360, 60, lambda m: 4.0, lambda m: 360 <= m < 368)
    assert r.defrost_misses == 1 and not r.defrost_no_reaction
    r = _run(an, 420, 60, lambda m: 4.0, lambda m: 420 <= m < 428)
    assert r.defrost_misses == 2 and r.defrost_no_reaction
    assert r.status == "warnung" and "abtauung_ohne_reaktion" in r.reasons
    # Eine Abtauung mit Reaktion hebt die Warnung auf
    r = _run(an, 480, 60, lambda m: 6.0 if 485 <= m < 490 else 4.0, lambda m: 480 <= m < 488)
    assert r.defrost_misses == 0 and not r.defrost_no_reaction


def test_pan_frost_and_heater_runtime():
    an = Analyzer(Settings(), has_soil=False, has_pan_temp=True, has_pan_heater=True)
    r = None
    for m in range(120):
        ts = T0 + m * MIN
        an.add_sample(ts, 4.0, -3.0, None, None, m < 30, None)
        r = an.evaluate(ts, 4.0, -3.0, None, False, False, pan_temp=-0.5)
    assert r.pan_frost and r.status == "warnung" and "kondensatwanne_frost" in r.reasons
    assert abs(r.pan_heater_hours_24h - 0.5) < 0.02


async def test_heatpump_step_with_ems_esp_like_entities(hass, freezer):
    hass.states.async_set("sensor.boiler_kompressoraktivitat", "Heizen")
    hass.states.async_set("sensor.boiler_kondensatorwanne_ta4", 3.0, TEMP)
    hass.states.async_set("binary_sensor.boiler_heizung_kondensatwanne_ea0", "on")
    hass.states.async_set("switch.boiler_heizband", "off")
    entry = await _create(hass, with_soil=False, heatpump={
        "defrost_entity": "sensor.boiler_kompressoraktivitat",
        "pan_temp_sensor": "sensor.boiler_kondensatorwanne_ta4",
        "pan_heater_entity": "binary_sensor.boiler_heizung_kondensatwanne_ea0",
        "heat_cable_entity": "switch.boiler_heizband",
    })
    assert len(_ids(hass, entry)) == 14 + 7 + 3
    await _advance(hass, freezer, 20)
    hass.states.async_set("sensor.boiler_kompressoraktivitat", "Abtauen")
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.kondensat_defrost_active").state == "on"
    for _ in range(3):
        await _advance(hass, freezer, 2)
        _set(hass, pipe=18.0)
    hass.states.async_set("sensor.boiler_kompressoraktivitat", "Heizen")
    await _advance(hass, freezer, 2)
    assert hass.states.get("binary_sensor.kondensat_defrost_active").state == "off"
    assert hass.states.get("sensor.kondensat_defrosts_24_h").state == "1"
    assert float(hass.states.get("sensor.kondensat_last_defrost_response").state) > 2
    assert float(hass.states.get("sensor.kondensat_drain_pan_heater_runtime_24_h").state) > 0.3
