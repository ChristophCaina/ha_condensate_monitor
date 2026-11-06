"""Verbindet die Rechenlogik mit Home Assistant (Abtastung, Speicherung, Benachrichtigung der Entities)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import SpeedConverter, TemperatureConverter

from .calc import (
    BUFFER_HOURS,
    parse_defrost_state,
    RAIN_HISTORY_DAYS,
    Analyzer,
    ForecastPoint,
    ForecastResult,
    analyze_forecast,
)
from .calc import Result, Settings, season_key_for
from .const import (
    CONF_OUTDOOR_SENSOR,
    CONF_PIPE_SENSOR,
    CONF_DEFROST_ENTITY,
    CONF_DEWPOINT_SENSOR,
    CONF_FORECAST_ENTITY,
    CONF_HEAT_CABLE_ENTITY,
    CONF_PAN_HEATER_ENTITY,
    CONF_PAN_TEMP_SENSOR,
    CONF_HUMIDITY_SENSOR,
    CONF_RAIN_SENSOR,
    CONF_SOIL_SENSOR,
    CONF_WIND_SENSOR,
    DOMAIN,
    EVENT_PROBLEM_CLEARED,
    EVENT_PROBLEM_STARTED,
    EVENT_STATUS_CHANGED,
    EVENT_TYPE,
    FORECAST_INTERVAL_S,
    REASONS,
    FORECAST_MAX_AGE_S,
    SAMPLE_INTERVAL_S,
    SAVE_EVERY_TICKS,
    STORAGE_VERSION,
    THRESHOLDS,
)

_LOGGER = logging.getLogger(__name__)


def merged_config(entry: ConfigEntry) -> dict[str, Any]:
    """Daten und Optionen zusammenführen (Optionen haben Vorrang)."""
    return {**entry.data, **entry.options}


class CondensateMonitor:
    """Tastet die Quell-Sensoren minütlich ab und stellt das Ergebnis bereit."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        cfg = merged_config(entry)
        self.pipe_id: str = cfg[CONF_PIPE_SENSOR]
        self.outdoor_id: str = cfg[CONF_OUTDOOR_SENSOR]
        self.soil_id: str | None = cfg.get(CONF_SOIL_SENSOR) or None
        self.humidity_id: str | None = cfg.get(CONF_HUMIDITY_SENSOR) or None
        self.dewpoint_id: str | None = cfg.get(CONF_DEWPOINT_SENSOR) or None
        self.rain_id: str | None = cfg.get(CONF_RAIN_SENSOR) or None
        self.wind_id: str | None = cfg.get(CONF_WIND_SENSOR) or None
        self.forecast_id: str | None = cfg.get(CONF_FORECAST_ENTITY) or None
        self.defrost_id: str | None = cfg.get(CONF_DEFROST_ENTITY) or None
        self.pan_temp_id: str | None = cfg.get(CONF_PAN_TEMP_SENSOR) or None
        self.pan_heater_id: str | None = cfg.get(CONF_PAN_HEATER_ENTITY) or None
        self.heat_cable_id: str | None = cfg.get(CONF_HEAT_CABLE_ENTITY) or None
        self.forecast_points: list[ForecastPoint] = []
        self.forecast_fetched: float | None = None
        self.forecast_type: str | None = None
        self._forecast_running = False
        self.maintenance_until: float | None = None  # None = aus, inf = ohne Ablauf
        self._last_reasons: set[str] | None = None
        self._last_status: str | None = None
        self._fault_issue = False
        self._device_id: str | None = None
        settings = Settings(**{k: float(cfg.get(k, d[0])) for k, d in THRESHOLDS.items()})
        self.stale_s = settings.stale_minutes * 60
        self.analyzer = Analyzer(
            settings,
            has_soil=self.soil_id is not None,
            has_humidity=self.has_humidity,
            has_rain=self.rain_id is not None,
            has_wind=self.wind_id is not None,
            has_defrost=self.defrost_id is not None,
            has_pan_temp=self.pan_temp_id is not None,
            has_pan_heater=self.pan_heater_id is not None,
            has_heat_cable=self.heat_cable_id is not None,
        )
        self.store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.result: Result | None = None
        self.season_start: datetime | None = None
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []
        self._unsub_stop: Callable[[], None] | None = None
        self._ticks = 0
        self._started_at = dt_util.utcnow()

    @property
    def has_soil(self) -> bool:
        return self.soil_id is not None

    @property
    def has_humidity(self) -> bool:
        return self.humidity_id is not None or self.dewpoint_id is not None

    @property
    def has_computed_dewpoint(self) -> bool:
        """Taupunkt nur als eigene Entity, wenn er aus der Luftfeuchte berechnet wird."""
        return self.humidity_id is not None and self.dewpoint_id is None

    @property
    def has_rain(self) -> bool:
        return self.rain_id is not None

    @property
    def has_wind(self) -> bool:
        return self.wind_id is not None

    @property
    def has_forecast(self) -> bool:
        return self.forecast_id is not None

    @property
    def has_defrost(self) -> bool:
        return self.defrost_id is not None

    @property
    def has_pan_temp(self) -> bool:
        return self.pan_temp_id is not None

    @property
    def has_pan_heater(self) -> bool:
        return self.pan_heater_id is not None

    @property
    def has_heat_cable(self) -> bool:
        return self.heat_cable_id is not None

    @property
    def source_ids(self) -> list[str]:
        ids = [self.pipe_id, self.outdoor_id, self.soil_id, self.humidity_id,
               self.dewpoint_id, self.rain_id, self.wind_id, self.defrost_id,
               self.pan_temp_id, self.pan_heater_id, self.heat_cable_id]
        return [i for i in ids if i]

    # ------------------------------------------------------------ Lebenszyklus
    async def async_start(self) -> None:
        data = await self.store.async_load()
        if data:
            try:
                self.analyzer.load(data, dt_util.utcnow().timestamp())
                if "notified_reasons" in data:
                    self._last_reasons = set(data["notified_reasons"])
                    self._last_status = data.get("last_status")
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning("Gespeicherte Messwerte konnten nicht geladen werden: %s", err)
        await self._async_backfill()

        self._unsubs.append(
            async_track_state_change_event(self.hass, self.source_ids, self._handle_source_change)
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._handle_tick, timedelta(seconds=SAMPLE_INTERVAL_S)
            )
        )
        self._unsub_stop = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._handle_stop
        )
        if self.forecast_id:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, [self.forecast_id], self._handle_forecast_entity_change
                )
            )
            self._unsubs.append(
                async_track_time_interval(
                    self.hass, self._handle_forecast_timer, timedelta(seconds=FORECAST_INTERVAL_S)
                )
            )
            await self._async_update_forecast()
        self._tick(dt_util.utcnow())

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._unsub_stop is not None:
            self._unsub_stop()
            self._unsub_stop = None
        await self.store.async_save(self._dump())

    def _dump(self) -> dict:
        data = self.analyzer.dump()
        if self._last_reasons is not None:
            data["notified_reasons"] = sorted(self._last_reasons)
            data["last_status"] = self._last_status
        return data

    # ------------------------------------------------------------ Wartung
    @property
    def maintenance_active(self) -> bool:
        return self.maintenance_until is not None

    @callback
    def async_set_maintenance(self, on: bool, until: float | None = None) -> None:
        if on:
            hours = self.analyzer.settings.maintenance_hours
            if until is None:
                until = dt_util.utcnow().timestamp() + hours * 3600 if hours > 0 else float("inf")
            self.maintenance_until = until
        else:
            self.maintenance_until = None
        self._evaluate(dt_util.utcnow())
        self._notify()

    # ------------------------------------------------------------- Events
    def _message(self, reason: str) -> tuple[str, str]:
        level, de, en = REASONS.get(reason, ("info", reason, reason))
        lang = (self.hass.config.language or "en").lower()
        return level, de if lang.startswith("de") else en

    def _fire(self, kind: str, **data) -> None:
        if self._device_id is None:
            device = dr.async_get(self.hass).async_get_device(
                identifiers={(DOMAIN, self.entry.entry_id)}
            )
            self._device_id = device.id if device else None
        self.hass.bus.async_fire(EVENT_TYPE, {
            "type": kind,
            "entry_id": self.entry.entry_id,
            "device_id": self._device_id,
            "name": self.entry.title,
            **data,
        })

    def _process_events(self) -> None:
        r = self.result
        if r is None:
            return
        # Reparatur-Hinweis bei Fühlerstörung (auch im Wartungsmodus)
        if r.fault and not self._fault_issue:
            ir.async_create_issue(
                self.hass, DOMAIN, f"sensor_fault_{self.entry.entry_id}",
                is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="sensor_fault",
                translation_placeholders={
                    "name": self.entry.title,
                    "minutes": str(int(self.analyzer.settings.stale_minutes)),
                },
            )
            self._fault_issue = True
        elif not r.fault and self._fault_issue:
            ir.async_delete_issue(self.hass, DOMAIN, f"sensor_fault_{self.entry.entry_id}")
            self._fault_issue = False

        if self.maintenance_active:
            return
        current = set(r.reasons)
        if self._last_reasons is None:
            # Erster Lauf ohne gespeicherten Stand: nichts nachmelden
            self._last_reasons, self._last_status = current, r.status
            return
        for reason in sorted(current - self._last_reasons):
            level, message = self._message(reason)
            self._fire(EVENT_PROBLEM_STARTED, reason=reason, level=level, message=message,
                       status=r.status)
        for reason in sorted(self._last_reasons - current):
            level, message = self._message(reason)
            self._fire(EVENT_PROBLEM_CLEARED, reason=reason, level=level, message=message,
                       status=r.status)
        if r.status != self._last_status:
            self._fire(EVENT_STATUS_CHANGED, status=r.status, previous_status=self._last_status,
                       reasons=sorted(current))
        self._last_reasons, self._last_status = current, r.status

    @callback
    def _handle_stop(self, _event: Event) -> None:
        self._unsub_stop = None  # async_listen_once meldet sich selbst ab
        self.store.async_delay_save(self._dump, 0)

    # --------------------------------------------------------------- Listener
    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)

        @callback
        def _remove() -> None:
            self._listeners.remove(update)

        return _remove

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    # ----------------------------------------------------------------- Werte
    @staticmethod
    def _state_value(state: State | None) -> float | None:
        """Zustand in °C umrechnen; None bei unbekannt/nicht verfügbar."""
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "", None):
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        unit = state.attributes.get("unit_of_measurement")
        if unit in (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.KELVIN):
            value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
        return value

    @staticmethod
    def _bool_value(state: State | None) -> bool | None:
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "", None):
            return None
        return state.state == "on" or state.state in ("1", "true", "True")

    def _defrost_value(self, state: State | None) -> bool | None:
        return None if state is None else parse_defrost_state(state.state)

    @staticmethod
    def _plain_value(state: State | None) -> float | None:
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "", None):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    @classmethod
    def _rain_value(cls, state: State | None) -> float | None:
        """Niederschlag in mm."""
        value = cls._plain_value(state)
        if value is None:
            return None
        unit = state.attributes.get("unit_of_measurement")
        if unit in (UnitOfPrecipitationDepth.INCHES, UnitOfLength.INCHES):
            value *= 25.4
        elif unit == UnitOfPrecipitationDepth.CENTIMETERS:
            value *= 10.0
        return value

    @classmethod
    def _wind_value(cls, state: State | None) -> float | None:
        """Windgeschwindigkeit in m/s."""
        value = cls._plain_value(state)
        if value is None:
            return None
        unit = state.attributes.get("unit_of_measurement")
        if unit and unit != UnitOfSpeed.METERS_PER_SECOND:
            try:
                value = SpeedConverter.convert(value, unit, UnitOfSpeed.METERS_PER_SECOND)
            except Exception:  # noqa: BLE001 - unbekannte Einheit
                return None
        return value

    # --------------------------------------------------------- Vorhersage
    async def _handle_forecast_timer(self, _now: datetime) -> None:
        await self._async_update_forecast()
        self._evaluate(dt_util.utcnow())
        self._notify()

    @callback
    def _handle_forecast_entity_change(self, _event: Event) -> None:
        # Sobald die Wetter-Entity (wieder) verfügbar ist, sofort abrufen
        if self.forecast_fetched is None:
            self._schedule_forecast_update()

    @callback
    def _schedule_forecast_update(self) -> None:
        if self._forecast_running:
            return

        async def _run() -> None:
            await self._async_update_forecast()
            if self.forecast_fetched is not None:
                self._evaluate(dt_util.utcnow())
                self._notify()

        self.entry.async_create_background_task(self.hass, _run(), "condensate_forecast_update")

    async def _async_update_forecast(self) -> None:
        self._forecast_running = True
        try:
            await self._async_fetch_forecast()
        finally:
            self._forecast_running = False

    async def _async_fetch_forecast(self) -> None:
        """Vorhersage über weather.get_forecasts holen (stündlich bevorzugt)."""
        eid = self.forecast_id
        if eid is None or self.hass.states.get(eid) is None:
            return
        for fc_type in ("hourly", "twice_daily", "daily"):
            try:
                response = await self.hass.services.async_call(
                    "weather", "get_forecasts", {"entity_id": eid, "type": fc_type},
                    blocking=True, return_response=True,
                )
            except Exception:  # noqa: BLE001 - Typ nicht unterstützt o. Ä.
                continue
            entries = (response or {}).get(eid, {}).get("forecast") or []
            points = self._parse_forecast(entries, self.hass.states.get(eid)) if entries else []
            _LOGGER.debug(
                "Vorhersage %s (%s): %s Einträge, %s auswertbar, Beispiel: %s",
                eid, fc_type, len(entries), len(points), entries[0] if entries else None,
            )
            if points:
                self.forecast_points = points
                self.forecast_fetched = dt_util.utcnow().timestamp()
                self.forecast_type = fc_type
                return
        _LOGGER.debug("Keine Vorhersage von %s erhalten", eid)

    @staticmethod
    def _parse_forecast(entries: list[dict], state: State | None) -> list[ForecastPoint]:
        attrs = state.attributes if state else {}
        t_unit = attrs.get("temperature_unit", UnitOfTemperature.CELSIUS)
        w_unit = attrs.get("wind_speed_unit", UnitOfSpeed.KILOMETERS_PER_HOUR)
        p_unit = attrs.get("precipitation_unit", UnitOfPrecipitationDepth.MILLIMETERS)

        def num(entry: dict, *keys: str) -> float | None:
            for key in keys:
                value = entry.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return float(value)
            return None

        def temp(value: float | None) -> float | None:
            if value is None or t_unit == UnitOfTemperature.CELSIUS:
                return value
            try:
                return TemperatureConverter.convert(value, t_unit, UnitOfTemperature.CELSIUS)
            except Exception:  # noqa: BLE001
                return None

        points = []
        for entry in entries:
            when = entry.get("datetime")
            if isinstance(when, datetime):
                parsed = when
            elif isinstance(when, str):
                parsed = dt_util.parse_datetime(when)
            else:
                parsed = None
            if parsed is None:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
            wind = num(entry, "wind_speed")
            if wind is not None and w_unit != UnitOfSpeed.METERS_PER_SECOND:
                try:
                    wind = SpeedConverter.convert(wind, w_unit, UnitOfSpeed.METERS_PER_SECOND)
                except Exception:  # noqa: BLE001
                    wind = None
            precip = num(entry, "precipitation", "precipitation_amount")
            if precip is not None and p_unit == UnitOfPrecipitationDepth.INCHES:
                precip *= 25.4
            condition = entry.get("condition")
            points.append(ForecastPoint(
                ts=dt_util.as_utc(parsed).timestamp(),
                temp=temp(num(entry, "temperature", "native_temperature")),
                templow=temp(num(entry, "templow")),
                dewpoint=temp(num(entry, "dew_point")),
                humidity=num(entry, "humidity"),
                wind=wind,
                cloud=num(entry, "cloud_coverage", "cloudcover_percentage", "cloud_area_fraction"),
                fog=num(entry, "fog2m_area_fraction", "fog_area_fraction"),
                precip=precip,
                snow=isinstance(condition, str) and condition in ("snowy", "snowy-rainy"),
            ))
        return points

    def _forecast_result(self, now: datetime) -> ForecastResult | None:
        if self.forecast_id is None or self.forecast_fetched is None:
            return None
        if now.timestamp() - self.forecast_fetched > FORECAST_MAX_AGE_S:
            return None
        return analyze_forecast(self.forecast_points, now.timestamp(), self.analyzer.settings)

    # ------------------------------------------------------- Vorbefüllung
    async def _async_backfill(self) -> None:
        """Fehlende Minuten (max. 25 h) aus dem Recorder-Verlauf nachtragen."""
        if "recorder" not in self.hass.config.components:
            return
        now = dt_util.utcnow()
        await self._async_backfill_rain(now)
        earliest = now - timedelta(hours=BUFFER_HOURS)
        samples = self.analyzer.samples
        step = SAMPLE_INTERVAL_S
        if samples:
            head_gap = samples[0].ts - earliest.timestamp() > 2 * step
            tail_gap = now.timestamp() - samples[-1].ts > 2 * step
            # Nachträglich hinzugefügter Erd- oder Windsensor: Spalte fehlt im Puffer
            missing = (
                (self.soil_id and all(x.soil is None for x in samples))
                or (self.wind_id and all(x.wind is None for x in samples))
                or (self.pan_heater_id and all(x.pan_heater is None for x in samples))
                or (self.heat_cable_id and all(x.heat_cable is None for x in samples))
            )
            if missing:
                samples.clear()
            elif not head_gap and not tail_gap:
                return
        start = earliest

        from homeassistant.components.recorder import get_instance, history

        ids = [i for i in (self.pipe_id, self.outdoor_id, self.soil_id, self.wind_id,
                           self.pan_heater_id, self.heat_cable_id, self.defrost_id) if i]
        try:
            hist = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.get_significant_states,
                    self.hass,
                    start,
                    now,
                    ids,
                    None,
                    True,  # Zustand zum Startzeitpunkt mitliefern
                    False,  # alle Änderungen, nicht nur "signifikante"
                )
            )
        except Exception as err:  # noqa: BLE001 - Vorbefüllung ist optional
            _LOGGER.warning("Verlauf konnte nicht gelesen werden: %s", err)
            return

        series: dict[str, list[tuple[float, float | None]]] = {}
        for eid in ids:
            points = []
            for st in hist.get(eid, []):
                if isinstance(st, State):
                    if eid == self.wind_id:
                        value = self._wind_value(st)
                    elif eid in (self.pan_heater_id, self.heat_cable_id):
                        value = self._bool_value(st)
                    elif eid == self.defrost_id:
                        value = self._defrost_value(st)
                    else:
                        value = self._state_value(st)
                    points.append((st.last_updated.timestamp(), value))
            points.sort(key=lambda p: p[0])
            series[eid] = points
        if not any(series.values()):
            return

        def value_at(eid: str | None, ts: float, idx: dict[str, int]) -> float | None:
            if eid is None:
                return None
            pts = series.get(eid, [])
            i = idx.get(eid, -1)
            while i + 1 < len(pts) and pts[i + 1][0] <= ts:
                i += 1
            idx[eid] = i
            return pts[i][1] if i >= 0 else None

        idx: dict[str, int] = {}

        def history_rows(ts_from: float, ts_to: float) -> list[tuple[float, float | None, float | None, float | None]]:
            rows = []
            ts = ts_from
            while ts <= ts_to:
                pipe = value_at(self.pipe_id, ts, idx)
                outdoor = value_at(self.outdoor_id, ts, idx)
                soil = value_at(self.soil_id, ts, idx)
                wind = value_at(self.wind_id, ts, idx)
                pan_heater = value_at(self.pan_heater_id, ts, idx)
                heat_cable = value_at(self.heat_cable_id, ts, idx)
                defrost = value_at(self.defrost_id, ts, idx)
                if pipe is not None or outdoor is not None:
                    rows.append((ts, pipe, outdoor, soil, wind, pan_heater, heat_cable, defrost))
                ts += step
            return rows

        existing = [(x.ts, x.pipe, x.outdoor, x.soil, x.wind, x.pan_heater, x.heat_cable)
                    for x in samples]
        end = now.timestamp() - step
        if existing:
            head = history_rows(start.timestamp(), existing[0][0] - step / 2)
            tail = history_rows(existing[-1][0] + step, end)
        else:
            head, tail = [], history_rows(start.timestamp(), end)
        if not head and not tail:
            return

        # Puffer in zeitlicher Reihenfolge neu aufbauen, damit die Ereignis-Markierungen stimmen
        self.analyzer.rebuild(head + existing + tail)
        for row in head + tail:
            self.analyzer.add_frost(row[2], step)
        count = len(head) + len(tail)
        _LOGGER.debug("%s Abtastungen aus dem Verlauf nachgetragen", count)

    async def _async_backfill_rain(self, now: datetime) -> None:
        """Niederschlagszähler der letzten 8 Tage aus dem Verlauf holen."""
        if self.rain_id is None:
            return
        from homeassistant.components.recorder import get_instance, history

        rain = self.analyzer.rain
        start = now - timedelta(days=RAIN_HISTORY_DAYS)
        if rain.points and rain.points[0][0] <= start.timestamp() + 3600:
            start = dt_util.utc_from_timestamp(rain.points[-1][0])
            if (now - start).total_seconds() < 2 * SAMPLE_INTERVAL_S:
                return
        try:
            hist = await get_instance(self.hass).async_add_executor_job(
                partial(history.get_significant_states, self.hass, start, now,
                        [self.rain_id], None, True, False)
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Niederschlagsverlauf konnte nicht gelesen werden: %s", err)
            return
        points = [
            (st.last_updated.timestamp(), self._rain_value(st))
            for st in hist.get(self.rain_id, [])
            if isinstance(st, State)
        ]
        points.sort(key=lambda p: p[0])
        _LOGGER.debug(
            "Niederschlag: %s Verlaufspunkte ab %s, %s gespeicherte Punkte",
            len(points), start, len(rain.points),
        )
        if not points:
            return
        if not rain.points or points[0][0] < rain.points[0][0]:
            # Neu aufbauen: Verlauf plus bereits gespeicherte, neuere Punkte
            existing = list(rain.points)
            rain.points.clear()
            for ts, value in points:
                rain.add(ts, value)
            for ts, value in existing:
                rain.add(ts, value)
        else:
            for ts, value in points:
                rain.add(ts, value)

    def _read(self, entity_id: str | None, now: datetime) -> tuple[float | None, bool]:
        """Aktuellen Wert in °C lesen und prüfen, ob der Sensor zu lange schweigt."""
        if entity_id is None:
            return None, False
        state: State | None = self.hass.states.get(entity_id)
        if state is None:
            return None, (now - self._started_at).total_seconds() > self.stale_s
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "", None):
            return None, (now - max(state.last_changed, self._started_at)).total_seconds() > self.stale_s
        try:
            value = float(state.state)
        except ValueError:
            return None, True
        unit = state.attributes.get("unit_of_measurement")
        if unit in (UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.KELVIN):
            value = TemperatureConverter.convert(value, unit, UnitOfTemperature.CELSIUS)
        last_seen = max(state.last_reported, self._started_at)
        return value, (now - last_seen).total_seconds() > self.stale_s

    def _evaluate(self, now: datetime) -> None:
        pipe, pipe_stale = self._read(self.pipe_id, now)
        outdoor, _ = self._read(self.outdoor_id, now)
        soil, soil_stale = self._read(self.soil_id, now)
        humidity = self._plain_value(self.hass.states.get(self.humidity_id)) if self.humidity_id else None
        dewpoint = self._state_value(self.hass.states.get(self.dewpoint_id)) if self.dewpoint_id else None
        self.result = self.analyzer.evaluate(
            now.timestamp(), pipe, outdoor, soil, pipe_stale, soil_stale,
            humidity=humidity, dewpoint=dewpoint, forecast=self._forecast_result(now),
            defrost=self._defrost_value(self.hass.states.get(self.defrost_id)) if self.defrost_id else None,
            pan_temp=self._state_value(self.hass.states.get(self.pan_temp_id)) if self.pan_temp_id else None,
        )
        if self.maintenance_until is not None and now.timestamp() >= self.maintenance_until:
            self.maintenance_until = None  # Wartungsmodus abgelaufen
        self._process_events()
        if self.maintenance_active:
            self.result.status = "wartung"

    @callback
    def _tick(self, now: datetime) -> None:
        pipe, _ = self._read(self.pipe_id, now)
        outdoor, _ = self._read(self.outdoor_id, now)
        soil, _ = self._read(self.soil_id, now)
        wind = self._wind_value(self.hass.states.get(self.wind_id)) if self.wind_id else None
        ts = now.timestamp()
        pan_heater = self._bool_value(self.hass.states.get(self.pan_heater_id)) if self.pan_heater_id else None
        heat_cable = self._bool_value(self.hass.states.get(self.heat_cable_id)) if self.heat_cable_id else None
        self.analyzer.add_sample(ts, pipe, outdoor, soil, wind, pan_heater, heat_cable)
        if self.rain_id:
            self.analyzer.rain.add(ts, self._rain_value(self.hass.states.get(self.rain_id)))

        local = dt_util.as_local(now)
        key = season_key_for(local.year, local.month)
        self.analyzer.integrate_frost(ts, outdoor, key)
        self.season_start = dt_util.as_utc(
            datetime(self.analyzer.season_key or key, 10, 1, tzinfo=dt_util.get_default_time_zone())
        )

        self._evaluate(now)
        self._notify()

        self._ticks += 1
        if self.forecast_id and self.forecast_fetched is None:
            # Noch keine Vorhersage (z. B. Wetter-Entity beim Start noch nicht geladen):
            # jede Minute erneut versuchen
            self._schedule_forecast_update()
        if self._ticks % SAVE_EVERY_TICKS == 0:
            self.store.async_delay_save(self._dump, 1)

    @callback
    def _handle_tick(self, now: datetime) -> None:
        self._tick(now)

    @callback
    def _handle_source_change(self, _event: Event) -> None:
        # Sofort neu berechnen (Deltas, Status), ohne einen neuen Abtastpunkt anzulegen
        self._evaluate(dt_util.utcnow())
        self._notify()
