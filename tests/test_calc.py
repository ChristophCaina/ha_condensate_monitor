"""Tests der Rechenlogik mit simulierten Temperaturkurven (0,1-K-Auflösung wie Ecowitt)."""

import math

from custom_components.condensate_frost_monitor.calc import (
    ForecastPoint,
    RainTracker,
    analyze_forecast,
    dewpoint_from_humidity,
    Analyzer,
    Debounce,
    Settings,
    linear_slope_per_hour,
    season_key_for,
)

MIN = 60.0
T0 = 1_790_000_000.0


def q(v):
    """Auf 0,1 K runden wie der Sensor."""
    return None if v is None else round(v, 1)


def run(an, minutes, pipe_fn, outdoor_fn=lambda m: 10.0, soil_fn=lambda m: 15.0, start=0):
    r = None
    for m in range(start, start + minutes):
        ts = T0 + m * MIN
        p, o, s = q(pipe_fn(m)), q(outdoor_fn(m)), q(soil_fn(m))
        an.add_sample(ts, p, o, s)
        an.integrate_frost(ts, o, 2026)
        r = an.evaluate(ts, p, o, s, False, False)
    return r


def test_slope_regression():
    pts = [(i * 60.0, 10 + i * 0.05) for i in range(30)]  # 3 K/h
    assert math.isclose(linear_slope_per_hour(pts), 3.0, rel_tol=1e-9)


def test_single_rounding_step_does_not_create_big_slope():
    an = Analyzer(Settings(), has_soil=True)
    # Echte Temperatur liegt knapp an der Rundungsgrenze -> Wert flackert 15.1/15.2
    r = run(an, 120, lambda m: 15.15 + (0.01 if m % 7 < 3 else -0.01))
    assert abs(r.slope) < 0.3
    assert r.span <= 0.11
    assert not r.plateau and not r.stagnant and r.status == "ok"


def test_quiet_state_ok_and_baseline():
    an = Analyzer(Settings(), has_soil=True)
    r = run(an, 400, lambda m: 15.1)
    assert r.baseline == 15.1
    assert abs(r.deviation) < 1e-9
    assert r.status == "ok"
    assert math.isclose(r.delta_soil, 0.1, abs_tol=1e-9)
    assert math.isclose(r.delta_outdoor, 5.1, abs_tol=1e-9)


def test_defrost_spike_recovers_without_warning():
    an = Analyzer(Settings(), has_soil=True)
    run(an, 360, lambda m: 5.0)

    def pipe(m):  # warmer Abtau-Schwall bei Minute 360, klingt in ~40 min ab
        if m < 360:
            return 5.0
        return 5.0 + 6.0 * math.exp(-(m - 360) / 10.0)

    r = run(an, 120, pipe, start=360)
    assert r.status == "ok"
    assert not r.stagnant
    assert abs(r.baseline - 5.0) < 0.05  # Grundlinie nicht verzogen


def test_freezing_plateau_detected_after_delay():
    an = Analyzer(Settings(), has_soil=True)
    run(an, 360, lambda m: 3.0, outdoor_fn=lambda m: -6.0)
    # Wasser steht und gefriert: fällt auf 0 und klebt dort
    fall = lambda m: max(0.0, 3.0 - (m - 360) * 0.1)
    r = run(an, 40, fall, outdoor_fn=lambda m: -6.0, start=360)
    assert not r.plateau
    r = run(an, 80, lambda m: 0.0 + (0.1 if m % 11 == 0 else 0.0), outdoor_fn=lambda m: -6.0, start=400)
    assert r.plateau
    assert r.status == "warnung"
    assert "gefrierplateau" in r.reasons


def test_below_zero_is_alarm():
    an = Analyzer(Settings(), has_soil=False)
    run(an, 60, lambda m: 1.0, outdoor_fn=lambda m: -8.0)
    r = run(an, 20, lambda m: -1.5, outdoor_fn=lambda m: -8.0, start=60)
    assert r.below_zero and r.status == "alarm"
    assert r.delta_soil is None and r.soil_frost is None  # ohne Erdfühler


def test_stagnant_water_with_frozen_baseline():
    an = Analyzer(Settings(), has_soil=True)
    run(an, 360, lambda m: 6.0)
    # Warmes Wasser bleibt stehen: Wert springt auf 9 und kriecht nur sehr langsam zurück
    r = run(an, 150, lambda m: 9.0 - (m - 360) * 0.001, start=360)
    assert r.baseline_frozen
    assert abs(r.baseline - 6.0) < 0.05
    assert r.stagnant and r.status == "warnung"


def test_soil_frost_info_and_trend_needs_coverage():
    an = Analyzer(Settings(), has_soil=True)
    soil = lambda m: 1.5 - (m / 1440) * 0.4  # -0,4 K pro Tag
    r = run(an, 600, lambda m: 5.0, soil_fn=soil)
    assert r.soil_frost and r.status == "info"
    assert r.soil_trend is None  # noch keine 20 h Daten
    r = run(an, 900, lambda m: 5.0, soil_fn=soil, start=600)
    assert r.soil_trend is not None and math.isclose(r.soil_trend, -0.4, abs_tol=0.05)


def test_frost_degree_hours_and_season_reset():
    an = Analyzer(Settings(), has_soil=False)
    run(an, 121, lambda m: 5.0, outdoor_fn=lambda m: -3.0)
    assert math.isclose(an.frost_degree_hours, 6.0, rel_tol=0.01)
    an.integrate_frost(T0 + 200 * MIN, -3.0, 2027)
    assert an.frost_degree_hours == 0.0
    assert season_key_for(2027, 3) == 2026 and season_key_for(2026, 10) == 2026


def test_debounce():
    d = Debounce(600, 300)
    assert not d.update(True, 0)
    assert not d.update(True, 599)
    assert d.update(True, 600)
    assert d.update(False, 700)
    assert not d.update(False, 1000)
    d2 = Debounce(600)
    d2.update(True, 0)
    d2.update(False, 300)  # Unterbrechung setzt zurück
    assert not d2.update(True, 800)


def test_fault_status_and_persistence_roundtrip():
    an = Analyzer(Settings(), has_soil=True)
    run(an, 30, lambda m: 10.0)
    r = an.evaluate(T0 + 31 * MIN, None, 10.0, 15.0, True, False)
    assert r.fault and r.status == "stoerung" and "rohrfuehler_ohne_werte" in r.reasons
    data = an.dump()
    an2 = Analyzer(Settings(), has_soil=True)
    an2.load(data, T0 + 32 * MIN)
    assert len(an2.samples) == len(an.samples)


def test_dewpoint_magnus():
    assert math.isclose(dewpoint_from_humidity(20.0, 50.0), 9.26, abs_tol=0.05)
    assert math.isclose(dewpoint_from_humidity(0.0, 100.0), 0.0, abs_tol=1e-6)


def test_rain_tracker_handles_daily_reset():
    rt = RainTracker()
    h = 3600.0
    rt.add(0, 0.0)
    rt.add(10 * h, 5.0)
    rt.add(23 * h, 8.0)
    rt.add(24 * h + 60, 0.0)  # Mitternachts-Reset (daily_rain)
    rt.add(30 * h, 4.0)
    assert rt.sum_since(-1) == 12.0
    assert rt.sum_since(24 * h) == 4.0


def test_wet_pit_needs_week_of_data():
    an = Analyzer(Settings(), has_soil=False, has_rain=True)
    h = 3600.0
    for i in range(7 * 24 + 1):
        an.rain.add(T0 + i * h, 100.0 + i * 0.2)
    now = T0 + 7 * 24 * h
    an.add_sample(now, 5.0, -1.0, None)
    r = an.evaluate(now, 5.0, -1.0, None, False, False)
    assert math.isclose(r.rain_7d, 33.6, abs_tol=0.3)
    assert r.wet_pit and r.status == "info" and "nasse_grube_bei_frost" in r.reasons


def test_forecast_analysis_hourly():
    now = T0
    pts = []
    for h in range(48):
        t = 4.0 - h * 0.25  # kühlt stetig ab: nach 16 h unter null
        pts.append(ForecastPoint(ts=now + h * 3600, temp=t, humidity=95 if h < 10 else 60,
                                 wind=1.0, cloud=80 if h < 20 else 10))
    fr = analyze_forecast(pts, now, Settings())
    assert fr.hourly
    assert math.isclose(fr.min_24h, 4.0 - 23 * 0.25)
    assert fr.frost is False  # -1,75 °C liegt über der Schwelle -2 °C
    assert math.isclose(fr.min_48h, 4.0 - 47 * 0.25)
    assert fr.frost_hours_48h == 31  # Stunden 17..47
    assert fr.radiative_frost and fr.radiative_frost_at == now + 20 * 3600
    assert fr.defrost_hours_24h == 10  # feucht und im Temperaturband


def test_forecast_daily_without_clouds():
    pts = [ForecastPoint(ts=T0 + d * 86400, temp=3.0, templow=-4.0) for d in range(3)]
    fr = analyze_forecast(pts, T0, Settings())
    assert not fr.hourly and fr.min_24h == -4.0 and fr.frost
    assert fr.radiative_frost is None and fr.frost_hours_48h is None


def test_radiative_frost_above_zero_air_forecast():
    """Nacht 1./2.11.2026: Vorhersage nur -0,2 °C, klar und windstill, gemessen -2,5 °C.

    Strahlungsfrost muss schon bei einer Luft-Vorhersage knapp über 0 °C anschlagen.
    """
    pts = [ForecastPoint(ts=T0 + h * 3600, temp=t, wind=1.5, cloud=5)
           for h, t in enumerate([6, 4, 3, 2, 1.5, 1.0, 0.5, 0.2, 0.5, 2, 5, 8])]
    fr = analyze_forecast(pts, T0, Settings())
    assert fr.radiative_frost and fr.radiative_frost_at == T0 + 3 * 3600  # erste Stunde <= +2 °C
    assert fr.frost is False  # ohne Standortkorrektur bleibt der Tiefstwert über -2 °C


def test_radiative_frost_needs_clear_and_calm():
    cloudy = [ForecastPoint(ts=T0 + h * 3600, temp=0.0, wind=1.0, cloud=90) for h in range(12)]
    windy = [ForecastPoint(ts=T0 + h * 3600, temp=0.0, wind=5.0, cloud=0) for h in range(12)]
    assert analyze_forecast(cloudy, T0, Settings()).radiative_frost is False
    assert analyze_forecast(windy, T0, Settings()).radiative_frost is False
    # 3 m/s (knapp 11 km/h) gelten als windstill
    calm = [ForecastPoint(ts=T0 + h * 3600, temp=0.0, wind=3.0, cloud=0) for h in range(12)]
    assert analyze_forecast(calm, T0, Settings()).radiative_frost is True


def test_clear_night_offset_only_in_clear_calm_hours():
    st = Settings(clear_night_offset=-2.0)
    clear = [ForecastPoint(ts=T0 + h * 3600, temp=-0.5, wind=1.0, cloud=10) for h in range(12)]
    fr = analyze_forecast(clear, T0, st)
    assert math.isclose(fr.min_24h, -2.5)
    assert fr.frost is True  # -2,5 °C <= -2 °C
    assert fr.frost_hours_48h == 12
    assert math.isclose(fr.frost_degree_hours_48h, 30.0)
    # Bewölkt: keine Korrektur
    cloudy = [ForecastPoint(ts=T0 + h * 3600, temp=-0.5, wind=1.0, cloud=90) for h in range(12)]
    fr = analyze_forecast(cloudy, T0, st)
    assert math.isclose(fr.min_24h, -0.5) and fr.frost is False
    # Ohne Bewölkungsdaten (z. B. Tagesvorhersage): keine Korrektur
    daily = [ForecastPoint(ts=T0 + d * 86400, temp=3.0, templow=-1.0) for d in range(3)]
    assert analyze_forecast(daily, T0, st).min_24h == -1.0
