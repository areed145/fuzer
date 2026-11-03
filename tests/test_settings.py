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


def test_sre_defaults_match_the_constants_they_replace():
    from datetime import timedelta

    from fuzer.graph import topology
    from fuzer.ops import sre

    with settings.Overrides({}):
        g = sre.grouping(None)
    assert g == {"gap": sre.GAP, "hops": topology.HOPS}
    assert {k: settings.REGISTRY[k].default for k in ("sre.step_minutes", "sre.learned_minutes", "sre.spe_minutes",
                                                      "sre.drift_rise")} == \
        {"sre.step_minutes": sre.STEP, "sre.learned_minutes": sre.LEARNED_MINUTES, "sre.spe_minutes": sre.SPE_MINUTES,
         "sre.drift_rise": sre.DRIFT_RISE}
    assert timedelta(hours=settings.REGISTRY["sre.lookback_hours"].default) == sre.LOOKBACK
    assert timedelta(minutes=settings.REGISTRY["sre.merge_minutes"].default) == sre.MERGE


def test_group_gap_is_a_setting():
    from datetime import datetime

    from fuzer.ops import sre

    found = [{"point": "A", "unit_id": None, "detector": "steps", "key": "step", "start": datetime(2025, 1, 1, h),
              "end": None} for h in (0, 3)]  # 3 hours apart: past the 2-hour default
    assert len(sre.group_detections(found, {}, **sre.grouping(None))) == 2
    with settings.Overrides({"sre.group_gap_hours": 4}):
        assert len(sre.group_detections(found, {}, **sre.grouping(None))) == 1


def test_signals_thresholds_are_settings():
    from fuzer.analysis import signals

    with settings.Overrides({}):
        assert signals.thresholds(None) == signals.DEFAULTS
    with settings.Overrides({"signals.flat_hours": 4}):
        th = signals.thresholds(None)
    assert th["flat_hours"] == 4.0
    assert signals.BASIS["frozen"].format(**th) == "no change at all for 4 h or more while the unit runs"


def test_alarm_defaults_match_the_constants_they_replace():
    from fuzer.analysis import alarms

    assert [settings.REGISTRY[f"alarms.{k}"].default for k in (
        "flood_per_10min", "target_per_hour", "standing_hours", "chatter", "failed_start_minutes")] == \
        [alarms.FLOOD, alarms.TARGET_PER_HOUR, alarms.STANDING_HOURS, alarms.CHATTER, alarms.FAILED_START_MINUTES]


def test_opportunity_defaults_match_the_constants_they_replace():
    from fuzer.ops import opportunities

    assert [settings.REGISTRY[f"opportunities.{k}"].default for k in ("gas_lift_breakeven", "stale_days", "material")] == \
        [opportunities.BREAKEVEN, opportunities.STALE_DAYS, opportunities.MATERIAL]
