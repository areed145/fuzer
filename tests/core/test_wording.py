"""The wording layer (#1072): what IOC staff and field operators read carries no machine text."""

import re

import pytest

from fuzer.core import wording

MACHINE = re.compile(r"\b(?:alarm|iow|role):[a-z_]|\[case:|\[entity:|fz-[a-z_]+-[0-9a-f]{6,}|\bdegC\b|\bSm3\b")


@pytest.mark.parametrize("old, new", [
    ("Upset: Detected alarm:lo on OK_J21_08.WHP at 2025-12-30 00:00, reaching 25 units",
     "Low alarm on OK_J21_08.WHP from 2025-12-30 00:00, across 25 assets"),
    ("Triage on Upset: Detected alarm:hihi on 30-PT-2002 at 2025-12-24 00:00, reaching 1 units, with the ops lead queue",
     "Triage on High-high alarm on 30-PT-2002 from 2025-12-24 00:00, with the ops lead queue"),
    ("OK-J21-05: detected alarm:lo on OK_J21_05.WHP", "OK-J21-05: low alarm on OK_J21_05.WHP"),
    ("OK_J08_03.WHP alarm:lo: 8 Sm3 oil at risk over 1 d", "OK_J08_03.WHP low alarm: 8 Sm³ oil at risk over 1 d"),
    ("with the ops_lead queue, sent to role:data_steward queue", "with the ops lead queue, sent to data steward queue"),
    ("48.06 degC against 55.7 degC; +0.043 degC/week", "48.06 °C against 55.7 °C; +0.043 °C/week"),
    ("3,548 Am3/h, 1,093 Sm3/h, 12 kSm3/d, 4.2 um", "3,548 Am³/h, 1,093 Sm³/h, 12 kSm³/d, 4.2 µm"),
    ("High IOW limit on 30-TT-1001", "High IOW limit on 30-TT-1001"),
])
def test_text_already_written_reads_in_people_words(old, new):
    assert wording.say(old) == new
    assert wording.say(new) == new  # idempotent
    assert not MACHINE.search(new)


def test_words_that_merely_contain_a_unit_stay_as_written():
    assert wording.say("summer um, the m3 tank OK") == "summer um, the m³ tank OK"
    assert wording.say("a forum about drums") == "a forum about drums"


def test_alarm_keys_units_queues_plurals_and_failure_modes():
    assert wording.alarm("alarm:lo") == "Low alarm"
    assert wording.alarm("iow:hi") == "High IOW limit"
    assert wording.alarm("learned:lolo") == "Below learned normal range"
    assert wording.alarm("instrument:flatline") == "Instrument fault (flatline)"
    assert (wording.unit("degC"), wording.unit("Sm3/d"), wording.unit("um"), wording.unit("barg")) == ("°C", "Sm³/d", "µm", "barg")
    assert wording.queue("role:data_steward") == wording.queue("data_steward") == "data steward queue"
    assert (wording.plural(1, "detection"), wording.plural(7, "detection"), wording.plural(1, "place")) == \
        ("1 detection", "7 detections", "1 place")
    assert (wording.failure_mode("VIB"), wording.failure_mode("inl"), wording.failure_mode("ZZZ")) == \
        ("Vibration", "Internal leakage", "ZZZ")


def test_handover_lines_and_upset_labels_carry_no_machine_text():
    from fuzer.ops import handover

    facts = {"sections": {"Escalated or overdue": [
        {"text": "Triage on Upset: Detected alarm:lo on OK_J21_08.WHP at 2025-12-30 00:00, reaching 25 units, "
                 "with the ops_lead queue: escalated 18:10", "cites": ["case:1"]}]}}
    [line] = handover.readable(facts)["sections"]["Escalated or overdue"]
    assert not MACHINE.search(line["text"]) and line["cites"] == ["case:1"]
    assert handover._to("role:data_steward") == "the data steward queue" and handover._to("ann") == "ann"


def test_an_upset_incident_names_the_alarm_and_the_asset():
    from fuzer.graph.incidents import _incident

    group = [{"id": "upset:3", "kind": "upset", "unit": "fz-tag-x", "day": "2025-12-30", "lost_oil": None,
              "failure_mode": None, "event_type": None, "key": "alarm:lo", "point": "OK_J21_05.WHP", "text": None}]
    c = _incident("fz-tag-x", "OK-J21-05", group)
    assert c.fields["description"] == "Low alarm on OK-J21-05 (OK_J21_05.WHP)"


def test_an_escalation_reads_as_a_sentence_on_the_plant_clock():
    old = ("'High vibration on 1st stage compressor' was escalated to you. SLA PT4H on 'Work the case' passed at "
           "2026-11-12 23:29 UTC while it was with sam; moved to the ops_lead queue")
    new = wording.say(old)
    assert "PT4H" not in new and "ops_lead" not in new and "UTC" not in new, new
    assert "'Work the case' was due within 4 hours, by " in new and new.endswith("It moved to the ops lead queue.")
    assert wording.say(new) == new
    assert wording.duration("P1D") == "1 day" and wording.duration("P1DT2H") == "1 day 2 hours"
    assert wording.say("SLA P1D on 'Work the case' passed at 2026-11-14 02:44 UTC while it was with the "
                       "engineer queue; moved to the ops_lead queue").startswith("'Work the case' was due within 1 day")


def test_one_reads_singular_in_text_already_written():
    assert wording.say("Upset from 20:50: alarm:lo on X, 1 detections, still going") == \
        "Upset from 20:50: low alarm on X, 1 detection, still going"
    assert wording.say("11 detections and 2.1 alarms") == "11 detections and 2.1 alarms"


def test_money_reads_one_way_and_matches_the_ui():
    # the same cases as ui/tests/format.test.cjs
    assert [wording.money(v) for v in (5540400, 5520150.4, 1000, 100, 0)] == \
        ["$5,540,400", "$5,520,150", "$1,000", "$100", "$0"]
    assert [wording.money(v) for v in (12.5, 0.42, 0.004, -3)] == ["$12.50", "$0.42", "<$0.01", "-$3.00"]
    assert wording.money(5520150, "NOK") == "5,520,150 NOK"
    assert wording.money(40, "unknown") == "40.00 (currency unknown)"


def test_a_source_reads_as_words_as_the_pages_say_it():
    # the same cases as detectorName in ui/src/wording.ts (tour D15: "field report from sample crew")
    assert [wording.source(s) for s in ("sample crew", "ptw", "alarm_performance", "sap", None)] == \
        ["Demo crew", "Permit to work", "Alarm performance", "SAP", "an unknown source"]
    assert wording.say("Weeping flange (field report from sample crew, open)") == \
        "Weeping flange (field report from Demo crew, open)"
    # tour E (C8): a handover drafted with the kind's and the source's keys reads in words when it is opened
    old = "20-GT-2000A needs action: lube oil pressure below its alarm (machine health from rotating_screen, closed)"
    assert wording.say(old).endswith("(Machine health from Rotating screen, closed)")
    assert wording.say("PTW-30015 lapsed (permit lapsed from ptw, open)").endswith(
        "(Permit lapsed from Permit to work, open)")
    assert wording.say(wording.say(old)) == wording.say(old)
