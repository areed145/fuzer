"""Settings (#131): the registry checks values; overrides stand in for the database in tests."""

import pytest

from fuzer.core import settings


def test_values_are_checked_against_the_registry():
    s = settings.REGISTRY["sre.handoff.max_diagnoses"]
    assert s.parse("4") == 4
    with pytest.raises(settings.SettingError, match="must be int"):
        s.parse("2.5")
    with pytest.raises(settings.SettingError, match="at most"):
        s.parse(1000)
    assert settings.REGISTRY["sre.handoff.max_usd"].parse("7.5") == 7.5
    secret = settings.Setting("x.password", "secret", None, "", "test")
    with pytest.raises(settings.SettingError, match="reference"):
        secret.parse("hunter2")
    assert secret.parse("env:X_PASSWORD") == "env:X_PASSWORD"


def test_overrides_need_no_database():
    with settings.Overrides({"sre.handoff.max_usd": 1.5}):
        assert settings.get(None, "sre.handoff.max_usd") == 1.5
    with pytest.raises(settings.SettingError, match="no setting"):
        settings.get(None, "no.such.setting")
