"""ILI anomalies (#366): Harrier's mainline has two MFL runs. The corrosion at the Battle Creek crossing grows fast
enough to cross the screen within the horizon, the rest hold, and the new anomaly on the stretch with no recorded
grade gets no strength rather than an assumed one."""

from fuzer.analysis import ili
from fuzer.plots import maintenance


def test_growth_and_strength_screen_the_mainline_and_refuse_strength_without_a_grade(harrier_db):
    out = ili.anomalies(harrier_db)
    assert [x["line"] for x in out["lines"]] == ["HR-PL-001"]
    line = out["lines"][0]
    assert [r["run"] for r in line["runs"]] == ["RUN-2024-MFL", "RUN-2026-MFL"]
    assert 0.95 < line["years_between_runs"] < 1.1
    by = {a["feature"]: a for a in line["anomalies"]}
    assert set(by) == {"ILI-26-0006", "ILI-26-0029", "ILI-26-0068", "ILI-26-0069", "ILI-26-0090", "ILI-26-0112"}
    crossing = by["ILI-26-0029"]
    assert crossing["previous"] == "ILI-24-0031" and crossing["segment"] == "HR-PL-001-S02"
    assert crossing["screen"] == "exceeds_within_5_years"
    assert 0.85 < crossing["erf"] < 0.95 and crossing["projected_depth_pct"] >= ili.DEPTH_LIMIT
    assert crossing["within_tool_tolerance"]  # 8 % of wall in a year: flagged, and the tolerance said
    assert by["ILI-26-0090"]["screen"] == "within_screen" and by["ILI-26-0090"]["previous"] == "ILI-24-0094"
    new = by["ILI-26-0112"]
    assert new["screen"] == "no_strength" and new["failure_barg"] is None and "material grade" in new["why"]
    assert new["new_since"] == "RUN-2024-MFL"
    assert line["anomalies"][0]["feature"] == "ILI-26-0029"
    assert line["not_in_latest_run"] == []
    assert out["verdict"] == "exceeds_within_5_years" and out["summary"]["flagged"] == 1
    fig = maintenance.ili_anomalies(harrier_db)
    assert fig.summary["verdict"] == out["verdict"] and len(fig.data) >= 2


def test_b31g_failure_pressure_matches_the_hand_calculation():
    # 49 % deep, 131 mm long on 610 x 15.9 mm X65: z = 1.769, M = 1.452, flow stress 517 MPa
    assert round(ili.failure_pressure(49, 131, 15.9, 610.0, 448), 1) == 220.7
    assert ili.smys("API 5L X65") == 448 and ili.smys("Grade B") == 241 and ili.smys("") is None
