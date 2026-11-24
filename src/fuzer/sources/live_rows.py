"""What a live facility's systems record about its live events (sources/live_events): the deferment in Energy
Components, the shift's entries in the operator log, the notification raised in SAP. Each arrives as a row of the
facility's own export, in its own columns and codes, once its time has come, and goes through the same ingest,
mapping and register matching as every other row:

    a trip               a downtime row once it is over, with the volume lost; log entries when it trips and when the
                         train is back; an M2 notification within the hour
    rough running        a log entry at its worst; a notification on about half of them
    a level excursion    a log entry as the panel operator takes it in hand
    a wandering value    a log entry on about half of them
    fouling              a notification a few days in, when the outlet temperature is plainly high

    wrap(name, connector)             the dataset's connector with the live rows due by now added to its own

Rows carry relative dates (`today-2d 04:10`, relative_dates), so a rolling facility's file dates move on and these do
not. They are a pure function of the events, so every instance and every load reads the same rows; the connector's
fingerprint changes when a new one falls due, which is what makes the hourly load pick it up.
"""

import csv
import hashlib
import statistics
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

DATASETS = ("ec_downtime", "oplogs", "sap_notifications")
EC_LAG = timedelta(hours=2)  # Energy Components has the downtime row a couple of hours after the train is back
# Data quality, as a real site's records have it: shares of the live rows that come in wrong
LATE = 0.15        # a downtime row entered the next day (the allocation engineer was off shift)
UNMATCHED = 0.12   # a notification raised against an old functional location with no equipment number
DUPLICATE = 0.10   # a trip raised twice, by the control room and again by the field


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _rel(t: datetime, today: date, time: bool = True) -> str:
    days = (t.date() - today).days
    day = "today" if days == 0 else f"today{days:+d}d"
    return f"{day} {t:%H:%M}" if time else day


def _h(*key) -> float:
    return int(hashlib.sha256(":".join(map(str, key)).encode()).hexdigest()[:12], 16) / 16 ** 12


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _floc(code: str, unit: str) -> str:
    """The SAP functional location of a model unit: `B1-K-2001` at KRGP is `KRGP-B1-K2001`."""
    head, _, tail = unit.rpartition("-")
    return f"{code}-{head}{tail}" if head else f"{code}-{unit}"


class _Site:
    """What the live rows need from the facility's own exports: its code, numbering and codes."""

    def __init__(self, data_dir: Path, spec: dict):
        self.dir, self.spec = data_dir, spec
        self.units = spec.get("units", {})
        down = _read(data_dir / "ec_downtime.csv")
        self.code = Counter(r["object_code"] for r in down).most_common(1)[0][0] if down else "SITE"
        trips = [r for r in down if "TRIP" in (r.get("reason_code") or "").upper()]
        self.trip_code = Counter(r["reason_code"] for r in trips).most_common(1)[0][0] if trips else "PROC-TRIP"
        self.down_prefix = (down[0]["event_id"].split("-")[0] if down else self.code[:2]) + "-LIVE-"
        self.lost = {k: self._per_hour(trips, k) for k in ("lost_oil_bbl", "lost_gas_mscf")}
        logs = _read(data_dir / "oplogs.csv")
        self.log_prefix = (logs[0]["entry_id"].split("-")[0] if logs else self.code[:2] + "LOG") + "-LIVE-"
        self.facility = logs[0]["facility"] if logs else self.code
        notes = _read(data_dir / "sap_notifications.csv")
        self.codes = Counter((r["damage_code"], r["cause_code"]) for r in notes).most_common(1)[0][0] if notes \
            else ("", "")
        self.equipment = {r["floc"]: r for r in _read(data_dir / "sap_equipment.csv") if r.get("floc")}

    @staticmethod
    def _per_hour(rows: list[dict], col: str) -> float:
        from . import relative_dates

        rates = []
        for r in rows:
            try:
                start, end = (datetime.fromisoformat(str(relative_dates.resolve(r[k]))) for k in ("start", "end"))
                rates.append(float(r[col] or 0) / ((end - start).total_seconds() / 3600))
            except (ValueError, ZeroDivisionError, KeyError):
                continue  # relative dates or a blank: no rate from this row
        return statistics.median(rates) if rates else 0.0

    def machine(self, e: dict) -> str:
        """The equipment an event is about: a train's machine for a trip."""
        unit = e["unit"]
        if self.units.get(unit, {}).get("type") == "train":
            inside = sorted(k for k, u in self.units.items() if u.get("train") == unit
                            and u.get("type") in ("compressor", "recip"))
            return inside[0] if inside else unit.split(":")[-1]
        return unit

    def equnr(self, unit: str) -> tuple[str, str] | None:
        floc = _floc(self.code, unit)
        r = self.equipment.get(floc)
        return (r["equnr"], floc) if r else None

    def what(self, unit: str) -> str:
        r = self.equipment.get(_floc(self.code, unit))
        return (r or {}).get("description") or unit


def _events(spec: dict, now: datetime) -> list[dict]:
    """The live events, grouped by their tag, that have started by now."""
    groups: dict[str, list[dict]] = {}
    for e in spec.get("events", []):
        if e.get("live"):
            groups.setdefault(e["live"], []).append(e)
    out = []
    for tag, evs in sorted(groups.items()):
        main = next((e for e in evs if e["kind"] in ("trip", "level", "drift", "fouling")), None) \
            or next((e for e in evs if e["kind"] == "vibration"), evs[0])
        start = datetime.fromisoformat(main.get("start") or main["points"][0][0])
        if start <= now:
            out.append({"tag": tag, "kind": tag.split("#")[1], "main": main, "all": evs, "start": start})
    return out


def _peak(e: dict) -> datetime:
    pts = e["points"]
    return datetime.fromisoformat(max(pts, key=lambda p: abs(float(p[1])))[0])


def rows(name: str, data_dir, now: datetime | None = None, spec: dict | None = None) -> list[dict]:
    """The live rows of dataset `name` that are due by `now`, in the export's own columns."""
    from . import live_events, process_model, relative_dates

    data_dir = Path(data_dir)
    if name not in DATASETS or not (data_dir / "process_model.json").is_file():
        return []
    spec = spec or process_model.read(data_dir)
    if not live_events.enabled(spec):
        return []
    now = now or _now()
    today = relative_dates.plant_today()
    site = _Site(data_dir, spec)
    out: list[dict] = []
    for ev in _events(spec, now):
        tag, kind, main = ev["tag"], ev["kind"], ev["main"]
        n = hashlib.sha256(tag.encode()).hexdigest()[:6].upper()
        unit = site.machine(main)
        what = site.what(unit)
        if kind == "trip":
            start, end = datetime.fromisoformat(main["start"]), datetime.fromisoformat(main["end"])
            hours = (end - start).total_seconds() / 3600
            rough = any(e["kind"] == "vibration" for e in ev["all"])
            why = "high vibration" if rough else "a process shutdown"
            lag = EC_LAG if _h(tag, "late") >= LATE else timedelta(hours=26 + 4 * _h(tag, "lag"))
            if name == "ec_downtime" and end + lag <= now:
                out.append({"event_id": f"{site.down_prefix}{n}", "object_code": site.code,
                            "start": _rel(start, today), "end": _rel(end, today), "reason_code": site.trip_code,
                            "lost_oil_bbl": str(round(site.lost["lost_oil_bbl"] * hours)),
                            "lost_gas_mscf": str(round(site.lost["lost_gas_mscf"] * hours)),
                            "comment": f"{unit} tripped on {why}; restarted after {hours:.1f} h"})
            if name == "oplogs":
                out.append({"entry_id": f"{site.log_prefix}{n}-1", "timestamp": _rel(start + timedelta(minutes=4), today),
                            "facility": site.facility, "author": "Control Room Operator",
                            "text": f"{unit} {what} tripped on {why}. Plant rates cut back, investigating with the "
                                    "field operator."})
                if end <= now:
                    out.append({"entry_id": f"{site.log_prefix}{n}-2", "timestamp": _rel(end + timedelta(minutes=6), today),
                                "facility": site.facility, "author": "Control Room Operator",
                                "text": f"{unit} restarted and loaded after {hours:.1f} h down. Rates back to normal."})
            if name == "sap_notifications" and (eq := site.equnr(unit)) \
                    and start + timedelta(minutes=30 + 60 * _h(tag, "notif")) <= now:
                out.append(_notification(site, n, eq, "M2", "2", f"{what} tripped on {why}", start, today, now))
                if _h(tag, "dup") < DUPLICATE:  # raised again by the field an hour later
                    out.append(_notification(site, n, eq, "M2", "2", f"{what} trip", start + timedelta(hours=1),
                                             today, now, copy=1))
        elif kind == "rough_running":
            peak = _peak(main)
            if name == "oplogs" and peak <= now:
                out.append({"entry_id": f"{site.log_prefix}{n}", "timestamp": _rel(peak, today),
                            "facility": site.facility, "author": "Field Operator",
                            "text": f"{unit} running rough, vibration well up on the local panel. Keeping an eye on it "
                                    "and told the control room."})
            if name == "sap_notifications" and _h(tag, "raise") < 0.5 and (eq := site.equnr(unit)) \
                    and peak + timedelta(hours=1) <= now:
                out.append(_notification(site, n, eq, "M1", "3", f"High vibration on {what}", peak, today, now))
        elif kind == "level":
            peak = _peak(main)
            high = float(max(main["points"], key=lambda p: abs(float(p[1])))[1]) > 0
            if name == "oplogs" and peak <= now:
                out.append({"entry_id": f"{site.log_prefix}{n}", "timestamp": _rel(peak, today),
                            "facility": site.facility, "author": "Control Room Operator",
                            "text": f"{'High' if high else 'Low'} level on {unit} {what}; took the level controller "
                                    "to manual and brought it back. Field operator checking the dump valve."})
        elif kind == "wander":
            peak = _peak(main)
            if name == "oplogs" and peak <= now and _h(tag, "log") < 0.5:
                out.append({"entry_id": f"{site.log_prefix}{n}", "timestamp": _rel(peak, today),
                            "facility": site.facility, "author": "Control Room Operator",
                            "text": f"{unit} off its normal value for a while; trend sent to the process engineer."})
        elif kind == "fouling":
            seen = ev["start"] + timedelta(days=3)
            if name == "sap_notifications" and seen <= now and (eq := site.equnr(unit)):
                out.append(_notification(site, n, eq, "M1", "3",
                                         f"Outlet temperature high on {what}, suspect fouling", seen, today, now))
    return out


def _notification(site: _Site, n: str, eq: tuple[str, str], kind: str, priority: str, text: str, at: datetime,
                  today: date, now: datetime, copy: int = 0) -> dict:
    """An SAP notification: outstanding (OSNO) when raised, completed (NOCO) two to ten days on, as the work it asked
    for is done. Some are raised against an old functional location with no equipment number (UNMATCHED)."""
    number = str(90_000_000 + int(n, 16) % 9_000_000 + copy)
    done = at + timedelta(days=2 + 8 * _h(n, copy, "done")) <= now
    if _h(n, copy, "unmatched") < UNMATCHED:  # the old numbering, before the site code went on the front
        eq = ("", eq[1].split("-", 1)[1])
    return {"qmnum": number, "notif_type": kind, "created": _rel(at, today, time=False), "equnr": eq[0],
            "floc": eq[1], "priority": priority, "short_text": text[:40], "damage_code": site.codes[0],
            "cause_code": site.codes[1], "status": "NOCO" if done else "OSNO"}


class _Live:
    """A dataset's connector with the facility's live rows added after its own."""

    def __init__(self, inner, extra: list[dict]):
        self._inner, self._extra = inner, extra
        self.live_rows = len(extra)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def fingerprint(self) -> str:
        keys = hashlib.sha256("|".join(sorted(str(sorted(r.items())) for r in self._extra)).encode()).hexdigest()[:16]
        return f"{self._inner.fingerprint()}+live:{keys}"

    def rows(self, since=None):
        yield from self._inner.rows(since)
        yield from self._extra


def wrap(name: str, connector):
    """`connector`, with the live rows of `name` added when the folder its export is in holds a live facility's
    process model and some are due."""
    path = getattr(connector, "path", None)
    if connector is None or name not in DATASETS or path is None:
        return connector
    try:
        extra = rows(name, Path(path).parent)
    except Exception:  # a broken model never stops a facility's own export from loading
        import logging

        logging.getLogger(__name__).exception("live rows for %s failed", name)
        return connector
    return _Live(connector, extra) if extra else connector
