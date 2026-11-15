"""Events, Wartungsmodus, Reparatur-Hinweis und Benachrichtigungs-Blueprint."""

import shutil
from pathlib import Path

from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_capture_events, async_mock_service

from custom_components.condensate_frost_monitor.const import DOMAIN, EVENT_TYPE
from tests.test_integration import TEMP, _advance, _create, _set

BLUEPRINT = Path(__file__).parent.parent / "blueprints/automation/condensate_frost_monitor/notify.yaml"


async def test_events_on_problem_start_and_clear(hass, freezer):
    events = async_capture_events(hass, EVENT_TYPE)
    await _create(hass, with_soil=False)
    await _advance(hass, freezer, 20)
    assert events == []
    _set(hass, pipe=-2.0, outdoor=-8.0)
    await _advance(hass, freezer, 20)
    started = [e.data for e in events if e.data["type"] == "problem_started"]
    assert started and started[0]["reason"] == "rohr_unter_null" and started[0]["level"] == "alarm"
    assert started[0]["device_id"] and started[0]["message"]
    assert any(e.data["type"] == "status_changed" and e.data["status"] == "alarm" for e in events)
    _set(hass, pipe=4.0, outdoor=2.0)
    await _advance(hass, freezer, 40)
    assert any(e.data["type"] == "problem_cleared" and e.data["reason"] == "rohr_unter_null" for e in events)


async def test_maintenance_suppresses_and_reports_after(hass, freezer):
    events = async_capture_events(hass, EVENT_TYPE)
    await _create(hass, with_soil=False)
    await _advance(hass, freezer, 20)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kondensat_maintenance_mode"}, blocking=True)
    assert hass.states.get("sensor.kondensat_status").state == "wartung"
    _set(hass, pipe=-2.0, outdoor=-8.0)
    await _advance(hass, freezer, 20)
    assert not [e for e in events if e.data["type"] == "problem_started"]
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.kondensat_maintenance_mode"}, blocking=True)
    await _advance(hass, freezer, 1)
    assert [e for e in events if e.data["type"] == "problem_started"]


async def test_maintenance_auto_off(hass, freezer):
    await _create(hass, with_soil=False)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.kondensat_maintenance_mode"}, blocking=True)
    assert hass.states.get("switch.kondensat_maintenance_mode").attributes["endet_um"]
    for _ in range(26):
        await _advance(hass, freezer, 10)
        _set(hass)
    assert hass.states.get("switch.kondensat_maintenance_mode").state == "off"
    assert hass.states.get("sensor.kondensat_status").state == "ok"


async def test_repairs_issue_on_sensor_fault(hass, freezer):
    entry = await _create(hass, with_soil=False)
    hass.states.async_set("sensor.rohr", "unavailable", TEMP)
    await _advance(hass, freezer, 35)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"sensor_fault_{entry.entry_id}")
    assert issue is not None and issue.translation_key == "sensor_fault"
    # Der Hinweis nennt den betroffenen Fühler
    assert "sensor.rohr" in issue.translation_placeholders["sensors"]
    _set(hass)
    await _advance(hass, freezer, 2)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"sensor_fault_{entry.entry_id}") is None


async def test_blueprint_notifies(hass, freezer):
    target = Path(hass.config.path("blueprints/automation/condensate_frost_monitor"))
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT, target / "notify.yaml")
    await _create(hass, with_soil=False)
    device_id = next(iter(
        d.id for d in __import__("homeassistant.helpers.device_registry", fromlist=["x"]).async_get(hass).devices.values()
    ))
    created = async_mock_service(hass, "persistent_notification", "create")
    dismissed = async_mock_service(hass, "persistent_notification", "dismiss")
    phone = async_mock_service(hass, "notify", "mobile_app_test")
    assert await async_setup_component(hass, "automation", {"automation": {
        "use_blueprint": {
            "path": "condensate_frost_monitor/notify.yaml",
            "input": {"monitor": device_id, "notify_service": "notify.mobile_app_test"},
        }
    }})
    await hass.async_block_till_done()
    await _advance(hass, freezer, 20)
    _set(hass, pipe=-2.0, outdoor=-8.0)
    await _advance(hass, freezer, 20)
    assert created and created[0].data["notification_id"].endswith("rohr_unter_null")
    assert phone and phone[0].data["data"]["channel"] == "alarm_stream"
    assert phone[0].data["data"]["push"]["sound"]["critical"] == 1
    _set(hass, pipe=4.0, outdoor=2.0)
    for _ in range(4):
        await _advance(hass, freezer, 10)
        _set(hass, pipe=4.0, outdoor=2.0)
    assert dismissed
    assert phone[-1].data["message"].startswith("Entwarnung") or "Entwarnung" in phone[-1].data["message"]
