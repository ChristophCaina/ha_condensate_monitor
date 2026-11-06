"""Reine Rechenlogik des Kondensat-Frostwächters (ohne Home-Assistant-Abhängigkeiten).

Alle Werte werden zeitbasiert abgetastet (einmal pro Minute der jeweils letzte
bekannte Wert). Dadurch sind die Kennzahlen unabhängig davon, wie oft ein
Funksensor sendet oder ob Home Assistant gleiche Werte doppelt meldet.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
from statistics import mean, median
from typing import Iterable

# Einzelner Abtastpunkt: Zeitstempel (Unix-Sekunden) und Werte in °C (None = unbekannt)


@dataclass(slots=True)
class Sample:
    ts: float
    pipe: float | None
    outdoor: float | None
    soil: float | None
    event: bool = False  # Rohrwert wich beim Abtasten deutlich von der Grundlinie ab
    wind: float | None = None  # m/s
    pan_heater: bool | None = None
    heat_cable: bool | None = None

    def to_list(self) -> list:
        return [round(self.ts), self.pipe, self.outdoor, self.soil, self.event, self.wind,
                self.pan_heater, self.heat_cable]

    @classmethod
    def from_list(cls, raw: list) -> "Sample":
        extra = list(raw[5:8]) + [None] * (3 - len(raw[5:8]))
        return cls(float(raw[0]), raw[1], raw[2], raw[3], bool(raw[4]), *extra)


@dataclass
class Settings:
    baseline_hours: float = 6.0
    slope_minutes: float = 30.0
    span_minutes: float = 60.0
    event_threshold: float = 1.0  # K Abweichung, ab der die Grundlinie eingefroren wird
    plateau_band: float = 0.5  # ± K um 0 °C
    plateau_slope: float = 0.3  # K/h
    plateau_minutes: float = 45.0
    below_zero_threshold: float = -0.5  # °C
    below_zero_minutes: float = 15.0
    soil_frost_threshold: float = 2.0  # °C
    soil_frost_minutes: float = 60.0
    stagnant_deviation: float = 1.0  # K
    stagnant_span: float = 0.3  # K
    stagnant_minutes: float = 60.0
    stale_minutes: float = 30.0
    defrost_temp_min: float = -7.0  # °C
    defrost_temp_max: float = 5.0  # °C
    defrost_spread: float = 3.0  # K Abstand Temperatur–Taupunkt
    wet_pit_rain_7d: float = 25.0  # mm
    wind_chill_speed: float = 3.0  # m/s (1-h-Mittel)
    forecast_frost_threshold: float = -2.0  # °C Tiefstwert in den nächsten 24 h
    clear_sky_cloud: float = 30.0  # % Bewölkung, darunter gilt eine Nacht als klar
    calm_wind: float = 2.0  # m/s, darunter gilt es als windstill
    defrost_reaction_threshold: float = 0.5  # K Ausschlag am Rohr, der als Reaktion zählt
    defrost_reaction_minutes: float = 30.0  # so lange nach Abtau-Ende wird auf Reaktion gewartet
    defrost_miss_count: float = 2.0  # so viele Abtauungen in Folge ohne Reaktion bis zur Warnung
    pan_frost_threshold: float = 0.5  # °C Kondensatwanne
    pan_frost_minutes: float = 15.0
    maintenance_hours: float = 4.0  # Wartungsmodus schaltet sich danach selbst ab (0 = nie)


MIN_BASELINE_SAMPLES = 15
MIN_SLOPE_SAMPLES = 10
SOIL_TREND_HOURS = 24.0
SOIL_TREND_MIN_COVERAGE_HOURS = 20.0
MAX_INTEGRATION_GAP_S = 300.0
BUFFER_HOURS = 25.0
RAIN_HISTORY_DAYS = 8.0
RAIN_MERGE_S = 600.0


def dewpoint_from_humidity(temp_c: float, rh: float) -> float | None:
    """Taupunkt nach Magnus (a=17,62, b=243,12 °C)."""
    if rh <= 0 or rh > 100:
        return None
    a, b = 17.62, 243.12
    gamma = math.log(rh / 100.0) + a * temp_c / (b + temp_c)
    return b * gamma / (a - gamma)


@dataclass
class ForecastPoint:
    """Ein Vorhersagezeitpunkt, bereits in °C, m/s, %, mm umgerechnet."""

    ts: float
    temp: float | None = None
    templow: float | None = None
    dewpoint: float | None = None
    humidity: float | None = None
    wind: float | None = None
    cloud: float | None = None
    fog: float | None = None
    precip: float | None = None
    snow: bool = False


@dataclass
class ForecastResult:
    min_24h: float | None = None
    min_24h_at: float | None = None
    min_48h: float | None = None
    frost_hours_48h: float | None = None
    frost_degree_hours_48h: float | None = None
    defrost_hours_24h: float | None = None
    frost: bool | None = None
    radiative_frost: bool | None = None
    radiative_frost_at: float | None = None
    snow_expected: bool | None = None
    hourly: bool = False


def analyze_forecast(points: list[ForecastPoint], now: float, st: "Settings") -> ForecastResult:
    """Vorhersage für die nächsten 24/48 Stunden auswerten."""
    res = ForecastResult()
    pts = sorted((p for p in points if p.ts >= now - 3600), key=lambda p: p.ts)
    if not pts:
        return res
    gaps = [b.ts - a.ts for a, b in zip(pts, pts[1:])]
    res.hourly = bool(gaps) and median(gaps) <= 3 * 3600
    step_h = (median(gaps) / 3600) if gaps else 1.0

    def low(p: ForecastPoint) -> float | None:
        vals = [v for v in (p.temp, p.templow) if v is not None]
        return min(vals) if vals else None

    def window(hours: float) -> list[ForecastPoint]:
        return [p for p in pts if p.ts < now + hours * 3600]

    w24 = [p for p in window(24) if low(p) is not None]
    w48 = [p for p in window(48) if low(p) is not None]
    if w24:
        coldest = min(w24, key=low)
        res.min_24h = low(coldest)
        res.min_24h_at = coldest.ts
        res.frost = res.min_24h <= st.forecast_frost_threshold
    if w48:
        res.min_48h = min(low(p) for p in w48)
    if res.hourly and w48:
        res.frost_hours_48h = sum(step_h for p in w48 if low(p) < 0)
        res.frost_degree_hours_48h = sum(max(0.0, -low(p)) * step_h for p in w48)
        defrost = 0.0
        for p in w24:
            t = low(p)
            dp = p.dewpoint
            if dp is None and p.humidity is not None and t is not None:
                dp = dewpoint_from_humidity(t, p.humidity)
            humid = (dp is not None and t - dp <= st.defrost_spread) or (p.fog or 0) >= 50
            if st.defrost_temp_min <= t <= st.defrost_temp_max and humid:
                defrost += step_h
        res.defrost_hours_24h = defrost
        clouds = [p for p in w24 if p.cloud is not None]
        if clouds:
            res.radiative_frost = False
            for p in clouds:
                calm = p.wind is None or p.wind <= st.calm_wind
                if low(p) <= 0 and p.cloud <= st.clear_sky_cloud and calm:
                    res.radiative_frost = True
                    res.radiative_frost_at = p.ts
                    break
    res.snow_expected = any(p.snow for p in window(48))
    return res


DEFROST_WORDS = {
    # EMS-ESP-Übersetzungen von "defrost" plus gängige Varianten
    "defrost", "defrosting", "abtauen", "abtauung", "enteisen", "enteisung", "ontdooien",
    "avfrostning", "odladzanie", "tine", "dégivrage", "degivrage", "buz çözücü",
    "scongelamento", "odmrazenie", "odmrazování", "cooling & defrost", "kühlen & abtauen",
}
RECOVERY_BAND = 0.3  # K: so nah an der Grundlinie gilt das Rohr als erholt
MAX_RECOVERY_S = 6 * 3600
MAX_DEFROST_EVENTS = 200


@dataclass
class DefrostEvent:
    start: float
    end: float | None = None
    baseline: float | None = None
    peak: float | None = None  # größter Ausschlag (mit Vorzeichen) gegenüber der Grundlinie
    reacted: bool | None = None  # None = Auswertung läuft noch
    recovery_s: float | None = None
    done: bool = False

    def to_list(self) -> list:
        return [self.start, self.end, self.baseline, self.peak, self.reacted,
                self.recovery_s, self.done]

    @classmethod
    def from_list(cls, raw: list) -> "DefrostEvent":
        return cls(*raw[:7])


class DefrostTracker:
    """Erkennt Abtauvorgänge und prüft, ob danach am Rohrfühler etwas ankommt."""

    def __init__(self) -> None:
        self.events: deque[DefrostEvent] = deque()
        self.active = False

    def update(self, now: float, defrost: bool | None, pipe: float | None,
               baseline: float | None, st: "Settings") -> None:
        if defrost is True and not self.active:
            self.active = True
            self.events.append(DefrostEvent(start=now, baseline=baseline if baseline is not None else pipe))
            while len(self.events) > MAX_DEFROST_EVENTS:
                self.events.popleft()
        elif defrost is False and self.active:
            self.active = False
            if self.events and self.events[-1].end is None:
                self.events[-1].end = now

        for ev in self.events:
            if ev.done:
                continue
            if pipe is not None and ev.baseline is not None:
                dev = pipe - ev.baseline
                if ev.peak is None or abs(dev) > abs(ev.peak):
                    ev.peak = dev
                if ev.peak is not None and abs(ev.peak) >= st.defrost_reaction_threshold:
                    ev.reacted = True
                if ev.reacted and ev.end is not None and abs(dev) <= RECOVERY_BAND:
                    ev.recovery_s = now - ev.end
                    ev.done = True
                    continue
            if ev.end is None:
                continue
            waited = now - ev.end
            if not ev.reacted and waited >= st.defrost_reaction_minutes * 60:
                ev.reacted = False
                ev.done = True
            elif ev.reacted and waited >= MAX_RECOVERY_S:
                ev.done = True

    def count_since(self, ts: float) -> int:
        return sum(1 for ev in self.events if ev.start >= ts)

    def last_finished(self) -> DefrostEvent | None:
        for ev in reversed(self.events):
            if ev.end is not None:
                return ev
        return None

    def consecutive_misses(self) -> int:
        misses = 0
        for ev in reversed(self.events):
            if not ev.done:
                continue
            if ev.reacted is False:
                misses += 1
            else:
                break
        return misses

    def dump(self) -> dict:
        return {"active": self.active, "events": [ev.to_list() for ev in self.events]}

    def load(self, raw: dict, now: float) -> None:
        cutoff = now - 7 * 86400
        self.events = deque(
            ev for ev in (DefrostEvent.from_list(x) for x in raw.get("events", []))
            if ev.start >= cutoff
        )
        self.active = bool(raw.get("active")) and bool(self.events) and self.events[-1].end is None


def parse_defrost_state(state: str | None) -> bool | None:
    """Abtau-Zustand aus Text- oder Binär-Entity lesen."""
    if state is None:
        return None
    value = str(state).strip().lower()
    if value in ("unknown", "unavailable", ""):
        return None
    if value in ("on", "true", "1"):
        return True
    if value in ("off", "false", "0"):
        return False
    return value in DEFROST_WORDS


class RainTracker:
    """Summiert einen Niederschlagszähler über gleitende Zeitfenster.

    Funktioniert mit jedem Zähler, der hochzählt und gelegentlich zurückgesetzt
    wird (total, yearly, monthly, weekly, daily): Nur positive Sprünge zählen.
    """

    def __init__(self) -> None:
        self.points: deque[tuple[float, float]] = deque()

    def add(self, ts: float, value: float | None) -> None:
        if value is None:
            return
        if self.points:
            last_ts, last_v = self.points[-1]
            if ts <= last_ts:
                return
            if value == last_v and ts - last_ts < 3600:
                return
            if ts - last_ts < RAIN_MERGE_S and value >= last_v and len(self.points) > 1:
                prev_v = self.points[-2][1]
                if last_v >= prev_v:
                    self.points[-1] = (ts, value)
                    return
        self.points.append((ts, value))
        cutoff = ts - RAIN_HISTORY_DAYS * 86400
        while len(self.points) > 1 and self.points[1][0] < cutoff:
            self.points.popleft()

    def coverage_s(self, now: float) -> float:
        return now - self.points[0][0] if self.points else 0.0

    def sum_since(self, ts_from: float) -> float:
        total = 0.0
        pts = list(self.points)
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t1 > ts_from and v1 > v0:
                total += v1 - v0
        return total

    def dump(self) -> list:
        return [[round(t), v] for t, v in self.points]

    def load(self, raw: list, now: float) -> None:
        cutoff = now - RAIN_HISTORY_DAYS * 86400
        self.points = deque((float(t), float(v)) for t, v in raw if cutoff <= float(t) <= now)


def linear_slope_per_hour(points: Iterable[tuple[float, float]]) -> float | None:
    """Steigung einer Ausgleichsgeraden in Einheiten pro Stunde."""
    pts = list(points)
    n = len(pts)
    if n < 2:
        return None
    mean_t = sum(t for t, _ in pts) / n
    mean_v = sum(v for _, v in pts) / n
    num = sum((t - mean_t) * (v - mean_v) for t, v in pts)
    den = sum((t - mean_t) ** 2 for t, _ in pts)
    if den == 0:
        return None
    return num / den * 3600.0


class Debounce:
    """Zustand wird erst wahr, wenn die Bedingung on_delay lang ununterbrochen gilt,
    und erst wieder falsch, wenn sie off_delay lang nicht mehr gilt."""

    def __init__(self, on_delay_s: float, off_delay_s: float = 0.0) -> None:
        self.on_delay = on_delay_s
        self.off_delay = off_delay_s
        self.state = False
        self._since: float | None = None  # Beginn der aktuellen gegenläufigen Phase

    def update(self, condition: bool | None, now: float) -> bool:
        if condition is None:
            # Unbekannt: Zustand halten, Zähler zurücksetzen
            self._since = None
            return self.state
        if condition == self.state:
            self._since = None
            return self.state
        if self._since is None:
            self._since = now
        delay = self.on_delay if condition else self.off_delay
        if now - self._since >= delay:
            self.state = condition
            self._since = None
        return self.state


@dataclass
class Result:
    """Ergebnis einer Auswertung; None bedeutet 'nicht verfügbar'."""

    pipe: float | None = None
    outdoor: float | None = None
    soil: float | None = None
    baseline: float | None = None
    baseline_frozen: bool = False
    deviation: float | None = None
    slope: float | None = None
    span: float | None = None
    delta_outdoor: float | None = None
    delta_soil: float | None = None
    soil_trend: float | None = None
    frost_degree: float | None = None
    frost_degree_hours: float = 0.0
    plateau: bool = False
    below_zero: bool = False
    stagnant: bool = False
    soil_frost: bool | None = None
    dewpoint: float | None = None
    dewpoint_spread: float | None = None
    defrost_weather: bool | None = None
    rain_24h: float | None = None
    rain_7d: float | None = None
    rain_coverage_days: float | None = None
    wet_pit: bool | None = None
    wind_1h: float | None = None
    wind_chill: bool | None = None
    forecast: ForecastResult | None = None
    defrost_active: bool | None = None
    defrost_count_24h: int | None = None
    last_defrost_start: float | None = None
    last_defrost_duration_min: float | None = None
    last_defrost_peak: float | None = None
    last_defrost_recovery_min: float | None = None
    defrost_misses: int | None = None
    defrost_no_reaction: bool | None = None
    pan_temp: float | None = None
    pan_frost: bool | None = None
    pan_heater_hours_24h: float | None = None
    heat_cable_hours_24h: float | None = None
    fault: bool = False
    fault_reasons: list[str] = field(default_factory=list)
    status: str = "ok"
    reasons: list[str] = field(default_factory=list)
    sample_count: int = 0
    buffer_hours: float = 0.0


class Analyzer:
    """Hält den Messpuffer und berechnet alle Kennzahlen."""

    def __init__(self, settings: Settings, has_soil: bool, *, has_humidity: bool = False,
                 has_rain: bool = False, has_wind: bool = False, has_defrost: bool = False,
                 has_pan_temp: bool = False, has_pan_heater: bool = False,
                 has_heat_cable: bool = False) -> None:
        self.settings = settings
        self.has_soil = has_soil
        self.has_humidity = has_humidity
        self.has_rain = has_rain
        self.has_wind = has_wind
        self.rain = RainTracker()
        self.has_defrost = has_defrost
        self.has_pan_temp = has_pan_temp
        self.has_pan_heater = has_pan_heater
        self.has_heat_cable = has_heat_cable
        self.defrost = DefrostTracker()
        self._deb_pan = Debounce(settings.pan_frost_minutes * 60, 30 * 60)
        self._deb_defrost = Debounce(30 * 60, 30 * 60)
        self._deb_wind = Debounce(30 * 60, 60 * 60)
        self.samples: deque[Sample] = deque()
        self.frost_degree_hours = 0.0
        self.season_key: int | None = None  # Jahr, in dem die aktuelle Frostsaison begann
        self._last_integration_ts: float | None = None
        self._last_baseline: float | None = None
        self._deb_plateau = Debounce(settings.plateau_minutes * 60, 15 * 60)
        self._deb_below = Debounce(settings.below_zero_minutes * 60, 30 * 60)
        self._deb_soil = Debounce(settings.soil_frost_minutes * 60, 3 * 3600)
        self._deb_stagnant = Debounce(settings.stagnant_minutes * 60, 30 * 60)
        # Die Wartezeit steckt schon im Alter der letzten Meldung (siehe monitor.py)
        self._deb_fault = Debounce(0, 0)

    # ------------------------------------------------------------------ Puffer
    def add_sample(self, ts: float, pipe: float | None, outdoor: float | None,
                   soil: float | None, wind: float | None = None,
                   pan_heater: bool | None = None, heat_cable: bool | None = None) -> None:
        baseline = self._baseline_at(ts)
        event = (
            pipe is not None
            and baseline is not None
            and abs(pipe - baseline) >= self.settings.event_threshold
        )
        self.samples.append(Sample(ts, pipe, outdoor, soil if self.has_soil else None, event,
                                   wind if self.has_wind else None,
                                   pan_heater if self.has_pan_heater else None,
                                   heat_cable if self.has_heat_cable else None))
        cutoff = ts - BUFFER_HOURS * 3600
        while self.samples and self.samples[0].ts < cutoff:
            self.samples.popleft()

    def _window(self, now: float, seconds: float) -> list[Sample]:
        start = now - seconds
        return [s for s in self.samples if s.ts >= start]

    def _baseline_at(self, now: float) -> float | None:
        """Median der Rohrwerte im Fenster, Ereignis-Abtastungen ausgenommen.

        Solange ein Ereignis läuft, bleibt die Grundlinie damit auf dem Wert davor
        stehen. Sind im Fenster nicht mehr genug ruhige Werte vorhanden (z. B. weil
        der Fühler dauerhaft versetzt wurde), wird auf alle Werte zurückgegriffen,
        damit sich die Grundlinie nach spätestens einer Fensterlänge neu einpendelt.
        """
        window = self._window(now, self.settings.baseline_hours * 3600)
        calm = [s.pipe for s in window if s.pipe is not None and not s.event]
        if len(calm) >= MIN_BASELINE_SAMPLES:
            self._last_baseline = median(calm)
            return self._last_baseline
        values = [s.pipe for s in window if s.pipe is not None]
        if len(values) >= MIN_BASELINE_SAMPLES and len(calm) < len(values) / 4:
            self._last_baseline = median(values)
            return self._last_baseline
        if len(values) >= MIN_BASELINE_SAMPLES:
            return self._last_baseline if self._last_baseline is not None else median(values)
        return None

    def rebuild(self, rows: list[tuple]) -> None:
        """Puffer neu aufbauen (für die Vorbefüllung).

        Zeilen: (ts, rohr, außen, erde, wind, wannenheizung, heizband[, abtauen]).
        Abtau-Ereignisse werden nur nachgespielt, wenn noch keine bekannt sind.
        """
        self.samples.clear()
        self._last_baseline = None
        replay = self.has_defrost and not self.defrost.events
        for row in sorted(rows, key=lambda r: r[0]):
            self.add_sample(*row[:7])
            if replay and len(row) > 7:
                ts, pipe = row[0], row[1]
                self.defrost.update(ts, row[7], pipe, self._baseline_at(ts), self.settings)
        if self.samples:
            last = self.samples[-1].ts
            if self._last_integration_ts is None or self._last_integration_ts < last:
                self._last_integration_ts = last

    # ------------------------------------------------------------- Kältesumme
    def add_frost(self, outdoor: float | None, seconds: float) -> None:
        """Nachgetragene Minuten zur Kältesumme addieren."""
        if outdoor is not None:
            self.frost_degree_hours += max(0.0, -outdoor) * seconds / 3600.0

    def integrate_frost(self, ts: float, outdoor: float | None, season_key: int) -> None:
        if self.season_key is None:
            self.season_key = season_key
        elif season_key != self.season_key:
            self.season_key = season_key
            self.frost_degree_hours = 0.0
            self._last_integration_ts = ts
            return
        if self._last_integration_ts is not None and outdoor is not None:
            dt = min(ts - self._last_integration_ts, MAX_INTEGRATION_GAP_S)
            if dt > 0:
                self.frost_degree_hours += max(0.0, -outdoor) * dt / 3600.0
        self._last_integration_ts = ts

    # -------------------------------------------------------------- Auswertung
    def evaluate(self, now: float, pipe: float | None, outdoor: float | None,
                 soil: float | None, pipe_stale: bool, soil_stale: bool, *,
                 humidity: float | None = None, dewpoint: float | None = None,
                 forecast: ForecastResult | None = None,
                 defrost: bool | None = None, pan_temp: float | None = None) -> Result:
        st = self.settings
        r = Result(pipe=pipe, outdoor=outdoor, soil=soil if self.has_soil else None)
        r.sample_count = len(self.samples)
        if self.samples:
            r.buffer_hours = round((now - self.samples[0].ts) / 3600, 2)

        r.baseline = self._baseline_at(now)
        if r.baseline is not None:
            window = self._window(now, st.baseline_hours * 3600)
            r.baseline_frozen = any(s.event for s in window[-5:])

        if pipe is not None and r.baseline is not None:
            r.deviation = pipe - r.baseline

        slope_pts = [(s.ts, s.pipe) for s in self._window(now, st.slope_minutes * 60)
                     if s.pipe is not None]
        if len(slope_pts) >= MIN_SLOPE_SAMPLES:
            r.slope = linear_slope_per_hour(slope_pts)

        span_vals = [s.pipe for s in self._window(now, st.span_minutes * 60)
                     if s.pipe is not None]
        if len(span_vals) >= MIN_SLOPE_SAMPLES:
            r.span = max(span_vals) - min(span_vals)

        if pipe is not None and outdoor is not None:
            r.delta_outdoor = pipe - outdoor
        if outdoor is not None:
            r.frost_degree = max(0.0, -outdoor)
        r.frost_degree_hours = self.frost_degree_hours

        if self.has_soil:
            if pipe is not None and soil is not None:
                r.delta_soil = pipe - soil
            soil_pts = [(s.ts, s.soil) for s in self._window(now, SOIL_TREND_HOURS * 3600)
                        if s.soil is not None]
            if soil_pts and (soil_pts[-1][0] - soil_pts[0][0]) >= SOIL_TREND_MIN_COVERAGE_HOURS * 3600:
                per_hour = linear_slope_per_hour(soil_pts)
                r.soil_trend = None if per_hour is None else per_hour * 24.0

        # --- Wetter (optional)
        if self.has_humidity and outdoor is not None:
            dp = dewpoint
            if dp is None and humidity is not None:
                dp = dewpoint_from_humidity(outdoor, humidity)
            if dp is not None:
                r.dewpoint = dp
                r.dewpoint_spread = max(0.0, outdoor - dp)
        if self.has_humidity:
            cond = None
            if outdoor is not None and r.dewpoint_spread is not None:
                cond = (st.defrost_temp_min <= outdoor <= st.defrost_temp_max
                        and r.dewpoint_spread <= st.defrost_spread)
            r.defrost_weather = self._deb_defrost.update(cond, now)

        if self.has_rain and self.rain.points:
            r.rain_24h = self.rain.sum_since(now - 86400)
            r.rain_7d = self.rain.sum_since(now - 7 * 86400)
            r.rain_coverage_days = round(min(self.rain.coverage_s(now), 7 * 86400) / 86400, 1)
            # Schon mit unvollständigen Daten "nass", wenn die Schwelle erreicht ist;
            # "trocken" erst, wenn wirklich eine ganze Woche vorliegt
            if r.rain_7d >= st.wet_pit_rain_7d:
                r.wet_pit = True
            elif self.rain.coverage_s(now) >= 6.5 * 86400:
                r.wet_pit = False

        if self.has_wind:
            winds = [s.wind for s in self._window(now, 3600) if s.wind is not None]
            if len(winds) >= MIN_SLOPE_SAMPLES:
                r.wind_1h = mean(winds)
            cond = None
            if r.wind_1h is not None and outdoor is not None:
                cond = outdoor < 0 and r.wind_1h >= st.wind_chill_speed
            r.wind_chill = self._deb_wind.update(cond, now)

        r.forecast = forecast

        # --- Wärmepumpe (optional)
        if self.has_defrost:
            self.defrost.update(now, defrost, pipe, r.baseline, st)
            r.defrost_active = self.defrost.active
            r.defrost_count_24h = self.defrost.count_since(now - 86400)
            last = self.defrost.last_finished()
            if last is not None:
                r.last_defrost_start = last.start
                r.last_defrost_duration_min = (last.end - last.start) / 60
                r.last_defrost_peak = last.peak
                if last.recovery_s is not None:
                    r.last_defrost_recovery_min = last.recovery_s / 60
            r.defrost_misses = self.defrost.consecutive_misses()
            r.defrost_no_reaction = r.defrost_misses >= max(1, int(st.defrost_miss_count))
        if self.has_pan_temp:
            r.pan_temp = pan_temp
            cond = None if pan_temp is None else pan_temp < st.pan_frost_threshold
            r.pan_frost = self._deb_pan.update(cond, now)
        day = self._window(now, 86400)
        if self.has_pan_heater:
            r.pan_heater_hours_24h = sum(1 for s in day if s.pan_heater) / 60
        if self.has_heat_cable:
            r.heat_cable_hours_24h = sum(1 for s in day if s.heat_cable) / 60

        # --- Zustände mit Verzögerung
        plateau_cond = None
        if pipe is not None and r.slope is not None:
            plateau_cond = abs(pipe) <= st.plateau_band and abs(r.slope) < st.plateau_slope
        r.plateau = self._deb_plateau.update(plateau_cond, now)

        below_cond = None if pipe is None else pipe < st.below_zero_threshold
        r.below_zero = self._deb_below.update(below_cond, now)

        stagnant_cond = None
        if r.deviation is not None and r.span is not None:
            stagnant_cond = abs(r.deviation) >= st.stagnant_deviation and r.span <= st.stagnant_span
        r.stagnant = self._deb_stagnant.update(stagnant_cond, now)

        if self.has_soil:
            soil_cond = None if soil is None else soil < st.soil_frost_threshold
            r.soil_frost = self._deb_soil.update(soil_cond, now)

        fault_now = pipe_stale or (self.has_soil and soil_stale)
        r.fault = self._deb_fault.update(fault_now, now)
        if r.fault:
            if pipe_stale:
                r.fault_reasons.append("rohrfuehler_ohne_werte")
            if self.has_soil and soil_stale:
                r.fault_reasons.append("erdfuehler_ohne_werte")

        # --- Gesamtstatus (höchste Stufe gewinnt)
        if r.below_zero:
            r.reasons.append("rohr_unter_null")
        if r.plateau:
            r.reasons.append("gefrierplateau")
        if r.stagnant:
            r.reasons.append("rueckstau_verdacht")
        if r.soil_frost:
            r.reasons.append("erdreich_frostnah")
        wet_and_frost = bool(r.wet_pit and r.frost_degree)
        if wet_and_frost:
            r.reasons.append("nasse_grube_bei_frost")
        if r.wind_chill:
            r.reasons.append("auskuehlung_wind")
        if r.defrost_no_reaction:
            r.reasons.append("abtauung_ohne_reaktion")
        if r.pan_frost:
            r.reasons.append("kondensatwanne_frost")
        fc_frost = bool(forecast and forecast.frost)
        if fc_frost:
            r.reasons.append("frost_vorhergesagt")
        fc_radiative = bool(forecast and forecast.radiative_frost)
        if fc_radiative:
            r.reasons.append("strahlungsfrost_vorhergesagt")
        r.reasons.extend(r.fault_reasons)

        if r.below_zero:
            r.status = "alarm"
        elif r.plateau or r.stagnant or r.defrost_no_reaction or r.pan_frost:
            r.status = "warnung"
        elif r.fault:
            r.status = "stoerung"
        elif r.soil_frost or wet_and_frost or r.wind_chill or fc_frost or fc_radiative:
            r.status = "info"
        else:
            r.status = "ok"
        return r

    # ------------------------------------------------------------ Persistenz
    def dump(self) -> dict:
        return {
            "samples": [s.to_list() for s in self.samples],
            "frost_degree_hours": self.frost_degree_hours,
            "season_key": self.season_key,
            "rain": self.rain.dump(),
            "defrost": self.defrost.dump(),
        }

    def load(self, data: dict, now: float) -> None:
        cutoff = now - BUFFER_HOURS * 3600
        self.samples = deque(
            s for s in (Sample.from_list(raw) for raw in data.get("samples", []))
            if s.ts >= cutoff and s.ts <= now
        )
        if not self.has_soil:
            for s in self.samples:
                s.soil = None
        self.frost_degree_hours = float(data.get("frost_degree_hours", 0.0))
        self.season_key = data.get("season_key")
        if self.has_rain:
            self.rain.load(data.get("rain", []), now)
        if self.has_defrost:
            self.defrost.load(data.get("defrost", {}), now)


def season_key_for(year: int, month: int, first_month: int = 10) -> int:
    """Frostsaison beginnt am 1. Oktober: Jan–Sep gehören zur Saison des Vorjahres."""
    return year if month >= first_month else year - 1
