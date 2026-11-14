"""P3.10b (#840): Fuzer never becomes a second alarm list. No page asks anyone to acknowledge an alarm, and alarm
records are read only by the case's "around the event" view and the alarm-performance view (whose rows link to the
cases raised for the alarm owner). An asset's alarm limits are configuration, not records, and are not covered."""

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

UI = Path(__file__).resolve().parents[2] / "ui" / "src"
READS_ALARMS = re.compile(r"\.alarms\b|alarmPerformance\b|[\"'`]/excursions")
ASKS_TO_ACK = re.compile(r">\s*Ack(nowledge)?\b|[\"'`]Ack(nowledge)?( alarm|[\"'`])|\bonAck\b", re.IGNORECASE)
ALLOWED_READERS = {"api/work.ts", "api/metrics.ts", "pages/workflows/Around.tsx", "pages/AlarmPerformance.tsx"}
# How many alarms are sounding on an asset, as a count on its status card and a colour on its 3D shape (P5.16b): a
# count beside open work and downtime, never a list of alarms and never anything to acknowledge.
ALLOWED_COUNTS = {"EntityStatus.tsx", "pages/NetworkScene.tsx", "sceneLogic.ts"}
HANDOVER = {"pages/Handover.tsx"}  # a shift handover is acknowledged by the incoming lead: not an alarm


def _sources():
    for f in sorted(UI.rglob("*.ts*")):
        yield f.relative_to(UI).as_posix(), f.read_text()


@pytest.mark.unit
def test_alarm_records_are_read_only_from_a_case_or_the_alarm_performance_view():
    readers = {name for name, src in _sources() if READS_ALARMS.search(src)}
    assert readers <= ALLOWED_READERS | ALLOWED_COUNTS, readers - ALLOWED_READERS - ALLOWED_COUNTS


@pytest.mark.unit
def test_no_page_asks_anyone_to_acknowledge_an_alarm():
    asking = {name for name, src in _sources() if name not in HANDOVER and ASKS_TO_ACK.search(src)}
    assert not asking, asking
    assert ASKS_TO_ACK.search('<Button onClick={ack}>Acknowledge</Button>')  # the check would catch one


@pytest.mark.unit
def test_no_route_lists_alarms_or_acknowledges_one():
    from fuzer.api import app

    def flat(routes):
        for r in routes:
            if isinstance(r, APIRoute):
                yield r
            elif hasattr(r, "original_router"):
                yield from flat(r.original_router.routes)

    paths = {r.path for r in flat(app.routes)}
    assert {p for p in paths if "alarm" in p} == {"/plant/alarm-performance"}
    assert {p for p in paths if re.search(r"\back", p)} == {"/handovers/{handover_id}/acknowledge"}
    assert not {p for p in paths if "excursion" in p}  # no plant-wide list of excursions, alarm ones among them
