"""A keyless, read-only guided investigation assembled from the loaded register.

What it looks at comes from the facility's pack (`investigation:` in its bundle's pack.yaml), never from code:

    investigation:
      title: Why investigate train B's March trips?
      asset: 20-K-2200B          # a tag the register holds
      since: 2024-03-01          # the incident window, [since, until)
      until: 2024-04-01
      followup_until: 2024-05-01 # work orders started after the window and before this count as the response
"""

from dataclasses import dataclass
from datetime import datetime

from ..core.pack import Pack
from . import queries


class InvestigationUnavailable(LookupError):
    pass


@dataclass
class SourceRecord:
    source: str
    record_type: str
    key: str
    observed_at: datetime


@dataclass
class Evidence:
    id: str
    kind: str
    label: str
    at: str | None
    text: str | None
    lost_oil_sm3: float | None
    records: list[SourceRecord]


@dataclass
class AssetField:
    name: str
    value: str
    source: str


@dataclass
class Finding:
    text: str
    evidence_ids: list[str]


@dataclass
class Investigation:
    title: str
    since: str
    until: str
    asset_id: str
    asset_label: str
    asset_fields: list[AssetField]
    asset_records: list[SourceRecord]
    evidence: list[Evidence]
    findings: list[Finding]
    limitations: list[str]


def _records(conn, ids: list[str]) -> dict[str, list[SourceRecord]]:
    out = {id_: [] for id_ in ids}
    for r in conn.execute(
        """
        select c.fuzer_id, r.source, r.record_type, r.source_key, r.last_seen
        from crosswalk c join source_record r using (source, record_type, source_key)
        where c.fuzer_id = any(%s)
        order by r.source, r.record_type, r.source_key
        """, (ids,),
    ):
        out[r["fuzer_id"]].append(SourceRecord(r["source"], r["record_type"], r["source_key"], r["last_seen"]))
    return out


def _month(day: str) -> str:
    return datetime.fromisoformat(day).strftime("%B")


def guided_investigation(conn, pack: Pack) -> Investigation:
    """Use existing incident/maintenance queries; never generate data, rebuild or call a model."""
    spec = pack.investigation or {}
    if not spec.get("asset") or not spec.get("since") or not spec.get("until"):
        raise InvestigationUnavailable(f"{pack.facility} has no guided investigation in its bundle.")
    ref, since, until = spec["asset"], str(spec["since"]), str(spec["until"])
    followup = str(spec.get("followup_until") or until)
    month, next_month = _month(since), _month(until)
    asset_id = queries.resolve_ref(conn, pack, ref)
    active = conn.execute(
        "select 1 from entity where fuzer_id = %s and retired_at is null and merged_into is null", (asset_id,),
    ).fetchone() if asset_id else None
    asset = queries.entity(conn, asset_id) if active else None
    if not asset or asset["kind"] != "tag":
        raise InvestigationUnavailable("The investigation asset is not loaded in this register.")
    incidents = queries.incidents(conn, asset_id, since=since, until=until, limit=100)
    if not incidents["items"]:
        raise InvestigationUnavailable(f"The {month} {since[:4]} incidents are not available for {ref} in this register.")

    items = {}
    for incident in incidents["items"]:
        for row in queries.incident_timeline(conn, incident["id"]):
            if row["at"] and since <= row["at"] < until:
                items[row["id"]] = row
    # Directly linked operator logs remain useful before optional LLM enrichment creates events.
    for row in conn.execute(
        """
        select distinct en.fuzer_id as id, en.kind, l.label, t.value #>> '{}' as at,
               g.value #>> '{}' as text, null::numeric as lost_oil_sm3
        from edge e join entity en on en.fuzer_id = e.src
        join golden t on t.fuzer_id = en.fuzer_id and t.field = 'timestamp'
        join golden g on g.fuzer_id = en.fuzer_id and g.field = 'text'
        left join entity_label l on l.fuzer_id = en.fuzer_id
        where e.dst = %s and e.rel = 'mentions' and en.kind = 'log_entry'
          and en.retired_at is null and en.merged_into is null
          and t.value #>> '{}' >= %s and t.value #>> '{}' < %s
        order by at, id
        """, (asset_id, since, until),
    ):
        items[row["id"]] = row

    maintenance = queries.maintenance(conn, asset_id, since=since, limit=100)
    followup_notifications = set()
    for notification in maintenance["recent"]:
        if notification["created"] and notification["created"] < until:
            items[notification["id"]] = {
                "id": notification["id"], "kind": "notification", "label": notification["label"],
                "at": notification["created"], "text": notification["text"], "lost_oil_sm3": None,
            }
            for order in notification["work_orders"]:
                if order["start"] and until <= order["start"] < followup:
                    followup_notifications.add(notification["id"])
                    items[order["id"]] = {
                        "id": order["id"], "kind": "work_order", "label": order["label"],
                        "at": order["start"], "text": order["text"], "lost_oil_sm3": None,
                    }
    if not items:
        raise InvestigationUnavailable("The investigation's supporting records are not available.")
    records = _records(conn, [asset_id, *items])
    evidence = [
        Evidence(r["id"], r["kind"], r["label"] or r["id"], r["at"], r["text"],
                 float(r["lost_oil_sm3"]) if r["lost_oil_sm3"] is not None else None, records[r["id"]])
        for r in sorted(items.values(), key=lambda r: (r["at"] or "", r["kind"], r["id"]))
    ]
    downtime = [e for e in evidence if e.kind == "downtime"]
    orders = [e for e in evidence if e.kind == "work_order"]
    findings = []
    limitations = [
        (pack.data_notice or "Historical data, not live plant conditions.")
        + " Source observation times describe ingestion, not when the plant event happened.",
        "Links and coincident records support investigation; they do not prove a root cause or the effectiveness "
        "of a repair. This journey does not recommend operating the plant.",
    ]
    if downtime:
        findings.append(Finding(
            f"{len(downtime)} {month} downtime records are linked to {ref}. "
            "Read their comments for the recorded trip and lift-gas impact.",
            [e.id for e in downtime],
        ))
        if all(e.lost_oil_sm3 is not None for e in downtime):
            total = sum(e.lost_oil_sm3 for e in downtime)
            findings.append(Finding(
                f"Those records report {total:,.0f} Sm3 of lost oil in total "
                "(standard cubic metres, a volume, not a daily rate). "
                "This is the recorded loss, not an independently calculated causal allocation.",
                [e.id for e in downtime],
            ))
        else:
            limitations.append("Some downtime records have no lost-oil volume; a complete total cannot be reported.")
    else:
        limitations.append(f"No linked {month} downtime records are available; production impact is not established.")
    if orders:
        findings.append(Finding(
            f"The {next_month} maintenance response linked to {month} notifications: "
            + "; ".join(e.text or "Work order text unavailable" for e in orders)
            + ". This records the maintenance response, not proof that the issue was resolved.",
            [e.id for e in orders] + sorted(followup_notifications),
        ))
    else:
        limitations.append(f"No {next_month} work orders linked to {month} notifications were found.")
    if not findings:
        findings.append(Finding(
            "Supporting records are available, but downtime impact and maintenance follow-up are not established.",
            [e.id for e in evidence],
        ))
    if any(not e.records for e in evidence):
        limitations.append("Some derived evidence has no direct source crosswalk; inspect its record before relying on it.")
    fields = [
        AssetField(name.replace("_", " ").capitalize(), str(asset["golden"][name]["value"]),
                   asset["golden"][name]["source"])
        for name in ("description", "manufacturer", "model")
        if name in asset["golden"] and asset["golden"][name]["value"] is not None
    ]
    return Investigation(
        spec.get("title") or f"Why investigate {ref} in {month}?", since, until, asset_id, ref, fields,
        records[asset_id], evidence, findings, limitations,
    )
