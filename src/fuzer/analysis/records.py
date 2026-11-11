"""Records that don't agree (#344): downtime with no operator log entry or notification near it, and log entries that
say something stopped with no downtime record. Gaps to raise before handover, and a check on every answer built on
those records.

- `mismatch(since, until)`: for every downtime record starting in the window, whether an operator log entry and
  (for the reason categories that call for one) a maintenance notification are on record about the same plant
  within the tolerances; and the reverse, every log entry in the window that reads as a stop (tripped, shut in,
  died, curtailed ...) with no downtime record about the same plant around it.
- `watch()`: the recent mismatches raised as `records_gap` exceptions on the ops lead's queue, one per record, kept
  while the gap holds and cleared (closing its case) when a matching record turns up.

"About the same plant" is read through the register, whatever the asset type: a record matches a downtime record when
it is about one of the downtime's subjects (what it affects and what caused it), something directly part of or
installed at one, or what one is directly part of. A log entry that reads as a stop also matches downtime on anything
it sits under (a trip logged on a compressor matches downtime recorded against its facility).

Tolerances are settings, stated in every result: `records.log_tolerance_hours` either side of the downtime (and
during it), `records.notification_tolerance_days` either side of its days (SAP notifications carry a date, not a
time). Records newer than the tolerance before the latest log entry are reported as too recent to judge: their
matching record may still be written.

A mismatch is a question for the shift, not proof that an event is missing or that anyone failed to record it.
"""

import re
from collections import defaultdict
from datetime import datetime, timedelta

from ..core import settings
from ..core.findings import DETERMINISTIC, Finding, at, fact

# Words that say something stopped, in an operator's log entry (the reverse check reads these).
STOP = re.compile(r"\b(trip(ped|s)?|shut[ -]?(in|down)|died|stopped|went down|lost lift|curtail(ed|ment)?|"
                  r"cut to|out of service|offline)\b", re.IGNORECASE)
UP = ("part_of", "installed_at", "located_at")  # src sits under dst in the register


def _t(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


def _rows(conn, kind: str, field: str, lo: str, hi: str, rel: str) -> list[dict]:
    """Live records of a kind whose `field` is within [lo, hi] (text bounds), with their golden fields and the
    entities they point at through `rel`."""
    rows = [dict(r) for r in conn.execute(
        """
        with x as (
            select e.fuzer_id as id from entity e join golden g using (fuzer_id)
            where e.kind = %(kind)s and e.retired_at is null and e.merged_into is null and g.field = %(field)s
              and g.value #>> '{}' >= %(lo)s and g.value #>> '{}' <= %(hi)s
        )
        select x.id, l.label,
               (select jsonb_object_agg(g.field, g.value) from golden g where g.fuzer_id = x.id) as f,
               array(select e.dst from edge e where e.src = x.id and e.rel = any(%(rel)s)
                     order by array_position(%(rel)s, e.rel), e.dst) as subjects
        from x left join entity_label l on l.fuzer_id = x.id
        """, {"kind": kind, "field": field, "lo": lo, "hi": hi, "rel": rel.split(",")})]
    for r in rows:
        r["subjects"] = list(dict.fromkeys(r["subjects"]))  # first rel first (what caused it before what it affects)
    return rows


class _Register:
    """The register's part-of / installed-at / located-at links, read once per call."""

    def __init__(self, conn):
        self.up: dict[str, set[str]] = defaultdict(set)
        self.down: dict[str, set[str]] = defaultdict(set)
        for r in conn.execute("select src, dst from edge where rel = any(%s) and valid_to is null", (list(UP),)):
            self.up[r["src"]].add(r["dst"])
            self.down[r["dst"]].add(r["src"])
        self._anc: dict[str, set[str]] = {}

    def near(self, ids) -> set[str]:
        out = set(ids)
        for i in ids:
            out |= self.up[i] | self.down[i]
        return out

    def above(self, i: str) -> set[str]:
        if i not in self._anc:
            seen, todo = set(), [i]
            while todo:
                for p in self.up[todo.pop()] - seen:
                    seen.add(p)
                    todo.append(p)
            self._anc[i] = seen
        return self._anc[i]

    def matches(self, downtime_subjects: list[str], subjects: list[str], stop: bool = False) -> bool:
        if self.near(downtime_subjects) & set(subjects):
            return True
        return stop and any(set(downtime_subjects) & self.above(s) for s in subjects)


def tolerances(conn) -> dict:
    return {"log_hours": int(settings.get(conn, "records.log_tolerance_hours")),
            "notification_days": int(settings.get(conn, "records.notification_tolerance_days")),
            "notification_categories": sorted(c.strip() for c in str(
                settings.get(conn, "records.notification_categories") or "").split(",") if c.strip())}


def _latest_log(conn) -> datetime | None:
    r = conn.execute("select max(g.value #>> '{}') as t from golden g join entity e using (fuzer_id) "
                     "where e.kind = 'log_entry' and g.field = 'timestamp' and e.retired_at is null").fetchone()
    return _t(r["t"]) if r else None


def mismatch(conn, since: str | None = None, until: str | None = None, days: int = 30) -> dict:
    """Downtime with no log entry or notification about the same plant, and stop entries in the log with no downtime
    record, for records in [since, until) (default: the `days` up to the latest log entry)."""
    tol = tolerances(conn)
    latest = _latest_log(conn)
    b = _t(until) if until else (latest + timedelta(seconds=1) if latest else None)
    if b is None:
        return {"status": "no_data", "message": "no operator log entries on record, so nothing can be compared"}
    a = _t(since) if since else b - timedelta(days=days)
    lh, nd = timedelta(hours=tol["log_hours"]), timedelta(days=tol["notification_days"])
    pad = timedelta(days=10)  # downtime that started before the window and ran into it
    downtime = _rows(conn, "downtime", "start", (a - pad - lh).isoformat(), (b + lh).isoformat(), "caused_by,affects")
    for d in downtime:
        d["start"] = _t(d["f"].get("start"))
        d["end"] = _t(d["f"].get("end")) or d["start"]
    downtime = [d for d in downtime if d["start"]]
    span_lo = min([a] + [d["start"] for d in downtime]) - lh
    span_hi = max([b] + [d["end"] for d in downtime]) + lh
    logs = _rows(conn, "log_entry", "timestamp", span_lo.isoformat(), span_hi.isoformat(), "mentions")
    for x in logs:
        x["at"] = _t(x["f"].get("timestamp"))
        x["stop"] = bool(STOP.search(str(x["f"].get("text") or "")))
    notes = _rows(conn, "notification", "created", (span_lo - nd).date().isoformat(), (span_hi + nd).isoformat(),
                  "concerns")
    reg = _Register(conn)
    labels = {r["fuzer_id"]: r["label"] for r in conn.execute(
        "select fuzer_id, label from entity_label where fuzer_id = any(%s)",
        (list({s for r in downtime + logs for s in r["subjects"]}),))}
    names = lambda ids: [labels.get(i) or i for i in ids]
    pending_after = latest - lh if latest else b

    out_dt, out_log, pending = [], [], []
    for d in sorted((d for d in downtime if a <= d["start"] < b), key=lambda d: d["start"]):
        lo, hi = d["start"] - lh, d["end"] + lh
        f = d["f"]
        cat = f.get("reason_category")
        log_ids = [x["id"] for x in logs if x["at"] and lo <= x["at"] <= hi
                   and reg.matches(d["subjects"], x["subjects"], x["stop"])]
        want_note = cat in tol["notification_categories"]
        note_ids = [n["id"] for n in notes if want_note and _t(n["f"].get("created"))
                    and (d["start"] - nd).date() <= _t(n["f"].get("created")).date() <= (d["end"] + nd).date()
                    and reg.matches(d["subjects"], n["subjects"])]
        missing = (["log"] if not log_ids else []) + (["notification"] if want_note and not note_ids else [])
        row = {"record": d["id"], "label": d["label"], "kind": "downtime", "at": d["start"].isoformat(timespec="minutes"),
               "end": d["end"].isoformat(timespec="minutes"), "reason": f.get("reason") or f.get("reason_code"),
               "reason_category": cat, "comment": f.get("comment"), "lost_oil_sm3": f.get("lost_oil_sm3"),
               "about": names(d["subjects"]), "subjects": d["subjects"], "missing": missing,
               "logs": log_ids[:5], "notifications": note_ids[:5],
               "notification_expected": want_note}
        if not missing:
            continue
        (pending if d["start"] > pending_after else out_dt).append(row)
    for x in sorted((x for x in logs if x["stop"] and x["at"] and a <= x["at"] < b), key=lambda x: x["at"]):
        hit = [d["id"] for d in downtime if d["start"] - lh <= x["at"] <= d["end"] + lh
               and reg.matches(d["subjects"], x["subjects"], True)]
        if hit:
            continue
        row = {"record": x["id"], "label": x["label"], "kind": "log_entry", "at": x["at"].isoformat(timespec="minutes"),
               "author": x["f"].get("author"), "text": x["f"].get("text"), "about": names(x["subjects"]),
               "subjects": x["subjects"], "missing": ["downtime"]}
        (pending if x["at"] > pending_after else out_log).append(row)
    checked = sum(1 for d in downtime if a <= d["start"] < b)
    stops = sum(1 for x in logs if x["stop"] and x["at"] and a <= x["at"] < b)
    caveats = ["A mismatch is a question for the shift: the record may be filed elsewhere, under another name, or "
               "not be needed. It is not proof that an event is missing."]
    if not checked and not stops:
        caveats.append("No downtime records and no log entries reading as a stop in this window: nothing to compare.")
    if latest and b > latest:
        caveats.append(f"The logs run to {latest:%Y-%m-%d %H:%M}; records within {tol['log_hours']} h of that are "
                       "too recent to judge.")
    return {
        "since": a.isoformat(timespec="minutes"), "until": b.isoformat(timespec="minutes"),
        "records_through": latest.isoformat(timespec="minutes") if latest else None,
        "tolerance": {"log_hours": tol["log_hours"], "notification_days": tol["notification_days"],
                      "notification_for_categories": tol["notification_categories"]},
        "downtime_checked": checked, "stop_entries_checked": stops,
        "downtime_without_records": out_dt, "log_stops_without_downtime": out_log, "too_recent": pending,
        "basis": (f"a log entry within {tol['log_hours']} h of a downtime record (or during it), and a notification "
                  f"within {tol['notification_days']} day(s) of its days for reason categories "
                  f"{', '.join(tol['notification_categories']) or '(none)'}, about the same plant: a subject of the "
                  "downtime (what it affects or what caused it), what is directly part of or installed at one, or "
                  "what one is part of; a log entry reading as a stop also matches downtime on anything it sits "
                  "under. Stop words: tripped, shut in / down, died, stopped, went down, lost lift, curtailed, "
                  "cut to, out of service, offline"),
        "sources": ["EC downtime", "operator log", "SAP notifications"],
        "caveats": caveats,
    }


# ---- raising them: the gaps as questions for the shift -----------------------------------------------------------

def _gap_type():
    from ..core.exceptions import TYPES, exception_type

    return TYPES.get("records_gap") or exception_type(
        "records_gap", "Records don't agree", role="role:ops_lead", audience=("ioc",), joins=False,
        description="Downtime with no operator log entry or notification about the same plant, or a log entry "
                    "that says something stopped with no downtime record. A question for the shift: add the "
                    "missing record, or say why none is needed. It clears when a matching record turns up.")


GAP_TYPE = _gap_type().name
SAY = {"log": "no log entry", "notification": "no notification", "downtime": "no downtime record"}


def title(row: dict) -> str:
    at = datetime.fromisoformat(row["at"]).strftime("%d %b %H:%M")
    about = row["about"][0] if row["about"] else "the plant"  # what caused it before what it affects
    gaps = " and ".join(SAY[m] for m in row["missing"])
    if row["kind"] == "downtime":
        return f"Downtime on {about} {at} ({row['reason']}): {gaps}"
    return f"Log says {about} stopped {at}: {gaps}"


def watch(conn, by: str = "agent:records_check") -> dict:
    """The mismatches in the last `records.lookback_days` before the latest log entry, raised as `records_gap`
    exceptions on the ops lead queue (one per record, keyed by it); open ones whose gap has closed are cleared."""
    from ..core import work
    from ..core.exceptions import ExceptionError, clear_exception, raise_exception

    latest = _latest_log(conn)
    if latest is None:
        return {"status": "no_data", "raised": [], "cleared": [], "errors": []}
    days = int(settings.get(conn, "records.lookback_days"))
    held = {r["key"]: r["evidence"].get("at") for r in conn.execute(
        "select key, evidence from exception_record where type = %s and state = 'open'", (GAP_TYPE,))}
    since = min([latest - timedelta(days=days)] + [_t(v) for v in held.values() if _t(v)])
    m = mismatch(conn, since.isoformat(), (latest + timedelta(seconds=1)).isoformat())
    recent = latest - timedelta(days=days)
    rows = m["downtime_without_records"] + m["log_stops_without_downtime"]
    raised, cleared, errors = [], [], []
    keep = {r["record"] for r in rows} | {r["record"] for r in m["too_recent"]}
    for r in rows:
        if r["record"] not in held and _t(r["at"]) < recent:
            continue  # older than the lookback: in the calc, not raised now
        tolerance = f"log ±{m['tolerance']['log_hours']} h, notification ±{m['tolerance']['notification_days']} d"
        found = Finding(
            "records.mismatch", DETERMINISTIC, method="Downtime records compared with the operator log and notifications",
            start=r["at"], data={"at": r["at"], "record": r["record"]},
            items=(at("When", r["at"]), fact("Missing", ", ".join(r["missing"])), fact("About", ", ".join(r["about"])),
                   fact("Reason recorded", r.get("reason")), fact("Comment", r.get("comment")),
                   fact("Log entry", r.get("text")), fact("Lost oil", r.get("lost_oil_sm3"), "Sm³"),
                   fact("Matched within", tolerance), fact("Note", m["caveats"][0])))
        try:
            x = raise_exception(conn, GAP_TYPE, r["record"], title(r), by, source="records_check",
                                refs=[r["record"], *r["subjects"]], priority=0.5, finding=found)
        except (work.WorkError, ExceptionError) as e:
            errors.append(f"{r['record']}: {e}")
            continue
        raised.append({"record": r["record"], "exception_id": x["id"], "item_id": x["item_id"], "new": x["raised"]})
    for key in set(held) - keep:
        clear_exception(conn, GAP_TYPE, key, by, reason="a matching record is now on file")
        cleared.append(key)
    conn.commit()
    return {"since": m["since"], "until": m["until"], "tolerance": m["tolerance"], "raised": raised,
            "cleared": cleared, "errors": errors}
