"""Konstanten für den Kondensat-Frostwächter."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "condensate_frost_monitor"
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH]

CONF_PIPE_SENSOR = "pipe_sensor"
CONF_OUTDOOR_SENSOR = "outdoor_sensor"
CONF_SOIL_SENSOR = "soil_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_DEWPOINT_SENSOR = "dewpoint_sensor"
CONF_RAIN_SENSOR = "rain_sensor"
CONF_WIND_SENSOR = "wind_sensor"
CONF_FORECAST_ENTITY = "forecast_entity"
CONF_DEFROST_ENTITY = "defrost_entity"
CONF_PAN_TEMP_SENSOR = "pan_temp_sensor"
CONF_PAN_HEATER_ENTITY = "pan_heater_entity"
CONF_HEAT_CABLE_ENTITY = "heat_cable_entity"
HEATPUMP_SENSORS = [
    CONF_DEFROST_ENTITY, CONF_PAN_TEMP_SENSOR, CONF_PAN_HEATER_ENTITY, CONF_HEAT_CABLE_ENTITY,
]
WEATHER_SENSORS = [
    CONF_HUMIDITY_SENSOR, CONF_DEWPOINT_SENSOR, CONF_RAIN_SENSOR, CONF_WIND_SENSOR, CONF_FORECAST_ENTITY,
]
FORECAST_INTERVAL_S = 30 * 60
FORECAST_MAX_AGE_S = 3 * 3600

SAMPLE_INTERVAL_S = 60
SAVE_EVERY_TICKS = 15
STORAGE_VERSION = 1

STATUS_OPTIONS = ["ok", "info", "stoerung", "warnung", "alarm", "wartung"]

EVENT_TYPE = "condensate_frost_monitor_event"
EVENT_PROBLEM_STARTED = "problem_started"
EVENT_PROBLEM_CLEARED = "problem_cleared"
EVENT_STATUS_CHANGED = "status_changed"

# Gründe -> (Stufe, Text deutsch, Text englisch)
REASONS: dict[str, tuple[str, str, str]] = {
    "rohr_unter_null": ("alarm",
        "Kondensatrohr unter 0 °C: Das Wasser im Rohr ist vermutlich gefroren.",
        "Condensate pipe below 0 °C: water in the pipe has probably frozen."),
    "gefrierplateau": ("warnung",
        "Gefrierplateau im Kondensatrohr: Stehendes Wasser gefriert gerade.",
        "Freezing plateau in the condensate pipe: standing water is freezing."),
    "rueckstau_verdacht": ("warnung",
        "Rückstau-Verdacht: Die Temperatur im Kondensatrohr hängt fest.",
        "Suspected backlog: the condensate pipe temperature is stuck."),
    "abtauung_ohne_reaktion": ("warnung",
        "Abtauwasser kommt nicht am Rohrfühler an. Ablauf an der Außeneinheit prüfen.",
        "Defrost water does not reach the pipe probe. Check the outdoor unit drain."),
    "kondensatwanne_frost": ("warnung",
        "Kondensatwanne der Außeneinheit ist frostgefährdet.",
        "The outdoor unit drain pan is at risk of freezing."),
    "erdreich_frostnah": ("info",
        "Der Frost nähert sich dem Kondensatrohr.",
        "Frost is approaching the condensate pipe."),
    "nasse_grube_bei_frost": ("info",
        "Sickergrube nass bei Frost: Rückstau möglich.",
        "Soakaway wet during frost: backlog possible."),
    "auskuehlung_wind": ("info",
        "Frost mit Wind: Das senkrechte Rohr kühlt stärker aus.",
        "Frost with wind: the vertical pipe cools faster."),
    "frost_vorhergesagt": ("info",
        "Frost vorhergesagt.",
        "Frost forecast."),
    "strahlungsfrost_vorhergesagt": ("info",
        "Klare, windstille Frostnacht vorhergesagt.",
        "Clear, calm frost night forecast."),
    "rohrfuehler_ohne_werte": ("stoerung",
        "Der Rohrfühler liefert keine Werte (Batterie, Funk?).",
        "The pipe probe sends no values (battery, radio?)."),
    "erdfuehler_ohne_werte": ("stoerung",
        "Der Erdfühler liefert keine Werte (Batterie, Funk?).",
        "The soil probe sends no values (battery, radio?)."),
}

# Einstellbare Schwellen: Schlüssel -> (Standardwert, Minimum, Maximum, Schrittweite, Einheit)
THRESHOLDS: dict[str, tuple[float, float, float, float, str]] = {
    "baseline_hours": (6.0, 1.0, 24.0, 0.5, "h"),
    "slope_minutes": (30.0, 10.0, 120.0, 5.0, "min"),
    "span_minutes": (60.0, 15.0, 240.0, 5.0, "min"),
    "event_threshold": (1.0, 0.3, 10.0, 0.1, "K"),
    "plateau_band": (0.5, 0.1, 2.0, 0.1, "K"),
    "plateau_slope": (0.3, 0.05, 2.0, 0.05, "K/h"),
    "plateau_minutes": (45.0, 5.0, 240.0, 5.0, "min"),
    "below_zero_threshold": (-0.5, -5.0, 1.0, 0.1, "°C"),
    "below_zero_minutes": (15.0, 0.0, 240.0, 5.0, "min"),
    "soil_frost_threshold": (2.0, -2.0, 10.0, 0.5, "°C"),
    "soil_frost_minutes": (60.0, 0.0, 360.0, 5.0, "min"),
    "stagnant_deviation": (1.0, 0.3, 10.0, 0.1, "K"),
    "stagnant_span": (0.3, 0.1, 2.0, 0.1, "K"),
    "stagnant_minutes": (60.0, 10.0, 480.0, 5.0, "min"),
    "stale_minutes": (30.0, 5.0, 240.0, 5.0, "min"),
    "defrost_temp_min": (-7.0, -20.0, 0.0, 0.5, "°C"),
    "defrost_temp_max": (5.0, 0.0, 10.0, 0.5, "°C"),
    "defrost_spread": (3.0, 0.5, 10.0, 0.5, "K"),
    "wet_pit_rain_7d": (25.0, 1.0, 200.0, 1.0, "mm"),
    "wind_chill_speed": (3.0, 0.5, 20.0, 0.5, "m/s"),
    "forecast_frost_threshold": (-2.0, -15.0, 3.0, 0.5, "°C"),
    "clear_sky_cloud": (30.0, 0.0, 100.0, 5.0, "%"),
    "calm_wind": (3.0, 0.0, 10.0, 0.5, "m/s"),
    "radiative_temp": (2.0, -5.0, 5.0, 0.5, "°C"),
    "clear_night_offset": (0.0, -8.0, 0.0, 0.5, "K"),
    "defrost_reaction_threshold": (0.5, 0.2, 5.0, 0.1, "K"),
    "defrost_reaction_minutes": (30.0, 5.0, 180.0, 5.0, "min"),
    "defrost_miss_count": (2.0, 1.0, 10.0, 1.0, "x"),
    "pan_frost_threshold": (0.5, -3.0, 5.0, 0.5, "°C"),
    "pan_frost_minutes": (15.0, 0.0, 120.0, 5.0, "min"),
    "maintenance_hours": (4.0, 0.0, 72.0, 0.5, "h"),
}


# Gruppierung der Schwellen in einklappbare Abschnitte; "requires" blendet
# Abschnitte bzw. Felder aus, wenn der zugehörige Sensor nicht eingerichtet ist.
THRESHOLD_SECTIONS: dict[str, list[tuple[str, str | None]]] = {
    "analysis": [("baseline_hours", None), ("slope_minutes", None), ("span_minutes", None),
                 ("event_threshold", None)],
    "plateau": [("plateau_band", None), ("plateau_slope", None), ("plateau_minutes", None)],
    "below_zero": [("below_zero_threshold", None), ("below_zero_minutes", None)],
    "stagnant": [("stagnant_deviation", None), ("stagnant_span", None), ("stagnant_minutes", None)],
    "soil": [("soil_frost_threshold", "soil"), ("soil_frost_minutes", "soil")],
    "weather": [("defrost_temp_min", "humidity"), ("defrost_temp_max", "humidity"),
                ("defrost_spread", "humidity"), ("wet_pit_rain_7d", "rain"),
                ("wind_chill_speed", "wind")],
    "forecast": [("forecast_frost_threshold", "forecast"), ("clear_sky_cloud", "forecast"),
                 ("calm_wind", "forecast"), ("radiative_temp", "forecast"),
                 ("clear_night_offset", "forecast")],
    "heatpump": [("defrost_reaction_threshold", "defrost"), ("defrost_reaction_minutes", "defrost"),
                 ("defrost_miss_count", "defrost"), ("pan_frost_threshold", "pan_temp"),
                 ("pan_frost_minutes", "pan_temp")],
    "fault": [("stale_minutes", None)],
    "maintenance": [("maintenance_hours", None)],
}
