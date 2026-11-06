# Kondensat-Frostwächter

Home-Assistant-Integration, die den Kondensatablauf einer Luft-Wärmepumpe auf Frost und Rückstau überwacht. Sie liest vorhandene Temperatursensoren und berechnet alle Kennzahlen selbst, damit du dafür keine Helfer anlegen musst.

## Sensoren

| Rolle | Pflicht | Beispiel |
|---|---|---|
| Rohrfühler im Kondensatrohr | ja | Ecowitt WN34L |
| Außentemperatur | ja | Wetterstation |
| Erdreich-Referenzfühler | nein | Ecowitt WN34S |

Die Entities für den Erdfühler werden nur angelegt, wenn du ihn angibst. Du kannst ihn später in den Optionen ergänzen oder wieder entfernen.

Im zweiten Schritt der Einrichtung kannst du optional Sensoren einer Wetterstation angeben:

| Rolle | Entities |
|---|---|
| Luftfeuchte und/oder Taupunkt | Abstand zum Taupunkt, Abtau-Wetter, Taupunkt (nur wenn aus der Luftfeuchte berechnet) |
| Niederschlagszähler (total, yearly, daily …) | Niederschlag 24 h, Niederschlag 7 Tage, Sickergrube nass |
| Windgeschwindigkeit | Wind 1-h-Mittel, Auskühlung durch Wind |
| Wettervorhersage (`weather`-Entity) | Tiefstwert 24/48 h, Froststunden und Frostgradstunden 48 h, Abtau-Stunden 24 h, Frost vorhergesagt, Strahlungsfrost vorhergesagt |

Im dritten Schritt kannst du optional Signale der Wärmepumpe angeben, z. B. aus EMS-ESP (eigene Integration oder MQTT) oder von einer anderen Wärmepumpe:

| Rolle | EMS-ESP (Buderus) | Entities |
|---|---|---|
| Abtau-Signal (Binärsensor oder Text-Sensor) | `hpactivity` („Abtauen“/„defrost“) | Abtauung aktiv, Abtauungen 24 h, Letzte Abtauung, Dauer, Ausschlag und Erholzeit der letzten Abtauung, Abtauung ohne Reaktion |
| Temperatur Kondensatwanne | `hpta4` | Kondensatwanne frostgefährdet |
| Heizung Kondensatwanne | `hpea0` | Laufzeit Wannenheizung 24 h |
| Heizband | `heatcable` | Laufzeit Heizband 24 h |

Ist nur die Luftfeuchte gesetzt, wird der Taupunkt nach der Magnus-Formel berechnet. Beim Niederschlag zählen nur positive Sprünge des Zählers, tägliche oder wöchentliche Rücksetzungen stören also nicht. Die letzten 8 Tage werden aus dem Recorder nachgeladen.

Die Vorhersage wird alle 30 Minuten über `weather.get_forecasts` geholt, bevorzugt stündlich, sonst zweimal täglich oder täglich. Neben den Standardfeldern werden auch die Felder von AstroWeather ausgewertet (`cloudcover_percentage`, `fog2m_area_fraction`, `precipitation_amount`). Ohne stündliche Vorhersage gibt es nur die Tiefstwerte.

## Entities

**Sensoren**
- **Status:** `ok`, `info`, `stoerung`, `warnung` oder `alarm`. Das Attribut `gruende` sagt, warum.
- **Grundlinie:** Median der Rohrtemperatur (Standard 6 h). Während eines Ereignisses (Abweichung ab 1 K) bleibt sie eingefroren, siehe Attribut `eingefroren`.
- **Abweichung:** Rohr minus Grundlinie.
- **Steigung:** Ausgleichsgerade über 30 min in K/h, unempfindlich gegen 0,1-K-Rundungssprünge.
- **Spannweite:** Max minus Min über 60 min.
- **Δ Außen:** Rohr minus Außenluft.
- **Frostgrad:** Wie weit die Außenluft unter 0 °C liegt.
- **Frostgradstunden Saison:** Kältesumme, Reset am 1. Oktober.
- **Δ Boden, Erdreich Trend** (nur mit Erdfühler): Der Trend in K/Tag wird erst nach 20 h Daten angezeigt, dadurch gibt es kein Einbau-Artefakt.
- **Messpuffer** (Diagnose, standardmäßig deaktiviert).

**Binärsensoren** (mit eingebauter Verzögerung)
- **Gefrierplateau:** Rohr im Band 0 ± 0,5 °C und Steigung unter 0,3 K/h, mindestens 45 min lang → Warnung.
- **Rohr unter Null:** unter −0,5 °C, mindestens 15 min lang → Alarm.
- **Rückstau-Verdacht:** Abweichung ≥ 1 K bei Spannweite ≤ 0,3 K, mindestens 60 min lang. Die Temperatur „hängt fest“ → Warnung.
- **Erdreich frostnah** (nur mit Erdfühler): unter 2 °C, mindestens 60 min lang → Info.
- **Abtau-Wetter** (mit Feuchte/Taupunkt): −7 bis +5 °C und höchstens 3 K Abstand zum Taupunkt, mindestens 30 min lang. Beeinflusst den Status nicht, sondern zeigt nur, wann mit vielen Abtauungen zu rechnen ist.
- **Sickergrube nass** (mit Niederschlag): mindestens 25 mm in 7 Tagen. Zusammen mit Frost → Info.
- **Auskühlung durch Wind** (mit Wind): Frost und Windmittel ab 3 m/s, mindestens 30 min lang → Info.
- **Frost vorhergesagt** (mit Vorhersage): Tiefstwert der nächsten 24 h ≤ −2 °C → Info.
- **Strahlungsfrost vorhergesagt** (mit Vorhersage, braucht Bewölkungsdaten): In den nächsten 24 h kommt eine Stunde mit höchstens 0 °C, höchstens 30 % Bewölkung und höchstens 2 m/s Wind. In klaren, windstillen Nächten kühlt der Boden stärker aus als die Luft → Info.
- **Abtauung ohne Reaktion** (mit Abtau-Signal): Nach mehreren Abtauungen in Folge (Standard 2) hat sich der Rohrfühler innerhalb von 30 min nach Abtau-Ende nicht um mindestens 0,5 K bewegt. Das Abtauwasser kommt nicht an, oben ist vermutlich etwas zu → Warnung.
- **Kondensatwanne frostgefährdet** (mit Wannentemperatur): Wanne unter 0,5 °C, mindestens 15 min lang → Warnung.
- **Fühlerstörung:** Rohr- oder Erdfühler seit 30 min ohne neue Meldung → Störung.

Alle Schwellen lassen sich in den Optionen anpassen.

## Benachrichtigungen

Die Integration verschickt selbst keine Nachrichten, sondern löst Events aus. Was daraus wird, entscheidest du per Blueprint oder eigener Automation.

**Event `condensate_frost_monitor_event`**

| Feld | Inhalt |
|---|---|
| `type` | `problem_started`, `problem_cleared` oder `status_changed` |
| `reason` | z. B. `rohr_unter_null`, `gefrierplateau`, `abtauung_ohne_reaktion` (siehe Attribut `gruende` am Status) |
| `level` | `alarm`, `warnung`, `stoerung` oder `info` |
| `message` | fertiger Text in der Sprache von Home Assistant (de/en) |
| `status`, `previous_status`, `reasons` | bei `status_changed` |
| `device_id`, `entry_id`, `name` | zur Zuordnung |

Nach einem Neustart wird nichts doppelt gemeldet: Der zuletzt gemeldete Stand wird gespeichert.

**Wartungsmodus** (Schalter): Solange er an ist, gibt es keine Events, und der Status zeigt `wartung`. Nach Ablauf (Standard 4 h, einstellbar) schaltet er sich selbst ab. Was in der Zwischenzeit begonnen hat und noch andauert, wird danach gemeldet.

**Reparatur-Hinweis:** Liefert ein Fühler keine Werte mehr, erscheint unter *Einstellungen → Reparaturen* ein Hinweis. Er verschwindet von selbst wieder.

**Blueprint** `blueprints/automation/condensate_frost_monitor/notify.yaml`: Auswahl der Ereignisse, Benachrichtigung in Home Assistant (wird beim Ende eines Ereignisses wieder entfernt), optional ein Smartphone über einen Notify-Dienst mit wählbaren Stufen, „Alarm“ als kritische Benachrichtigung, die „Nicht stören“ durchbricht, und optional eine Entwarnung. Zum Einspielen die Datei nach `/config/blueprints/automation/condensate_frost_monitor/` kopieren.

## Funktionsweise

Die Integration tastet die Quellen einmal pro Minute ab, jeweils mit dem letzten bekannten Wert. Die Kennzahlen hängen deshalb nicht davon ab, wie oft ein Funksensor sendet. Der Messpuffer (25 h) und die Kältesumme werden gespeichert und überstehen Neustarts. Beim Start werden fehlende Minuten aus dem Recorder-Verlauf nachgetragen: beim ersten Einrichten die letzten 25 h, nach einem Neustart nur die Lücke. Die Kennzahlen sind dadurch sofort verfügbar. Die Verzögerungszeiten der Binärsensoren beginnen nach einem Neustart neu.

## Installation

Den Ordner `custom_components/condensate_frost_monitor` nach `/config/custom_components/` kopieren, Home Assistant neu starten, dann unter *Einstellungen → Geräte & Dienste → Integration hinzufügen* nach „Kondensat-Frostwächter“ suchen. Alternativ als benutzerdefiniertes HACS-Repository einbinden.

## Tests

```bash
pip install pytest-homeassistant-custom-component
pytest
```
