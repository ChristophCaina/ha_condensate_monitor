import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    # Der Recorder muss vor hass eingerichtet werden
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    request.getfixturevalue("enable_custom_integrations")
    yield
