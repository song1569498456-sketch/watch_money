"""Five-second defaults without overwriting persisted user settings."""
import pytest
from pydantic import ValidationError
from watch_money.models import Settings
from watch_money.storage import Storage


def test_five_second_defaults_leave_identity_discovery_and_freshness_separate(tmp_path):
    settings = Storage(tmp_path).settings()
    assert settings.poll_seconds == settings.broad_poll_seconds == settings.funding_poll_seconds == 5
    assert settings.refresh_ms == 5000
    assert settings.discovery_seconds == 1800 and settings.stale_seconds == 60


def test_existing_values_preserved_and_only_missing_fields_take_new_defaults(tmp_path):
    store = Storage(tmp_path)
    store.set('settings', {'poll_seconds': 45, 'refresh_ms': 1500, 'broad_poll_seconds': 90, 'funding_poll_seconds': 120})
    settings = store.settings()
    assert (settings.poll_seconds, settings.refresh_ms, settings.broad_poll_seconds, settings.funding_poll_seconds) == (45, 1500, 90, 120)
    # The compatibility path used for old partial settings also keeps explicitly saved values.
    settings = Settings.model_validate({'poll_seconds': 15, 'refresh_ms': 1000})
    assert settings.poll_seconds == 15 and settings.refresh_ms == 1000
    assert settings.broad_poll_seconds == settings.funding_poll_seconds == 5


@pytest.mark.parametrize('field', ['poll_seconds', 'broad_poll_seconds', 'funding_poll_seconds'])
def test_accepts_five_but_not_under_five(field):
    assert getattr(Settings.model_validate({field: 5}), field) == 5
    with pytest.raises(ValidationError):
        Settings.model_validate({field: 4})
