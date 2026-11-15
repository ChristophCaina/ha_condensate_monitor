# Hinweise für die Arbeit an diesem Repository

- Integration: `custom_components/condensate_frost_monitor/` (HACS-Struktur beibehalten).
- Sprache: Code-Kommentare, Commit-Messages, README und PR-Texte auf Deutsch.
- Tests: `pytest` (pytest-homeassistant-custom-component). Vor jedem Push müssen alle Tests grün sein.
- Übersetzungen: neue Texte immer in `strings.json`, `translations/en.json` und `translations/de.json`.

## Versionierung

Jeder PR, der Code der Integration ändert, hebt `version` in `manifest.json` an:

- **Patch** (0.7.0 → 0.7.1): Fehlerbehebungen, Texte, kleine Verbesserungen ohne neue Funktion.
- **Minor** (0.7.x → 0.8.0): neue Sensoren, Einstellungen oder Funktionen.
- **Major** (→ 1.0.0): erste öffentliche Version bzw. inkompatible Änderungen an Einstellungen oder Entities.

Laufen mehrere PRs parallel, hebt jeder die Version auf Basis des aktuellen `main` an. Bei einem Merge-Konflikt in `manifest.json` gilt die höhere Version.
