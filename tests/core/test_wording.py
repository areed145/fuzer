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
