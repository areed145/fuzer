"""Daily oil capacity and allocation assembled from the dated asset graph."""

import math
from collections import defaultdict
from datetime import date, timedelta

UNIT = "Sm3/d"
DAYS = 14
WELLS = {"id": "reported_potential", "label": "Reported well potential", "category": "wells"}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _percentage(allocated, maximum):
    return _number(allocated / maximum * 100) if maximum > 0 else None


def calculate(reports: list[dict], limits: list[dict], end: date) -> dict:
    """Bounds must describe total facility oil throughput, not individual parallel branches.

    A daily report's potential is a fallback reference, not evidence of a physical bottleneck.
    Missing/ambiguous inputs stay missing; the period PE is a ratio of paired daily volumes.
    """
    if not date.min + timedelta(days=DAYS - 1) <= end < date.max:
        raise ValueError("end must allow fourteen daily samples and an exclusive following day")
    since = end - timedelta(days=DAYS - 1)
    daily, bounds = defaultdict(list), defaultdict(list)
    for row in reports:
        daily[str(row["date"])[:10]].append(row)
    for row in limits:
        bounds[str(row["date"])[:10]].append(row)
    points, catalog = [], {}
    total_allocated = total_max = 0.0
    paired = 0
    paired_values = []
    for offset in range(DAYS):
        day = (since + timedelta(days=offset)).isoformat()
        rows = daily[day]
        flags, source_ids = [], []
        allocated = potential = None
        if len(rows) == 1:
            row = rows[0]
            allocated = _number(row.get("allocated"))
            potential = _number(row.get("potential"))
            source_ids.append(row["id"])
            if allocated is None:
                flags.append("missing_or_invalid_allocation")
        else:
            flags.append("ambiguous_report" if rows else "missing_report")
            source_ids.extend(r["id"] for r in rows)
        candidates = defaultdict(list)
        invalid = False
        for row in bounds[day]:
            source_ids.append(row["record"])
            value = _number(row["value"])
            if value is None or row["unit"] != UNIT or row.get("qualifier"):
                invalid = True
                flags.append("invalid_capacity_limit")
                continue
            candidates[row["id"]].append(value)
            catalog[row["id"]] = {
                "id": row["id"], "label": row.get("label") or row["id"],
                "category": row.get("category") or "unknown",
            }
        if any(len(set(values)) != 1 for values in candidates.values()):
            invalid = True
            flags.append("ambiguous_capacity_limit")
        active = None
        binding = []
        maximum = None
        basis = "missing"
        if candidates and not invalid:
            maximum = min(values[0] for values in candidates.values())
            binding = sorted(key for key, values in candidates.items()
                             if math.isclose(values[0], maximum, rel_tol=1e-9, abs_tol=1e-9))
            basis = "declared_limits"
            # Plant limits say what it could carry; the wells may not offer that much.
            if potential is not None and potential <= maximum:
                binding = sorted({*binding, WELLS["id"]}) if potential == maximum else [WELLS["id"]]
                maximum = potential
                catalog[WELLS["id"]] = WELLS
            active = binding[0]
            if len(binding) > 1:
                flags.append("tied_constraints")
        elif not bounds[day] and potential is not None:
            maximum, basis = potential, "reported_potential"
            flags.append("physical_constraint_unknown")
        else:
            flags.append("missing_capacity")
        gap = maximum - allocated if maximum is not None and allocated is not None else None
        efficiency = _percentage(allocated, maximum) if gap is not None else None
        if gap is not None and maximum > 0 and efficiency is None:
            flags.append("derived_metric_overflow")
        if maximum == 0:
            flags.append("zero_capacity")
        if gap is not None:
            total_allocated += allocated
            total_max += maximum
            paired += 1
            paired_values.append((allocated, maximum))
            if gap < 0:
                flags.append("allocated_above_capacity")
        points.append({
            "date": day, "dynamic_max_sm3d": maximum, "allocated_sm3d": allocated,
            "reported_potential_sm3d": potential,
            "gap_sm3d": gap, "efficiency_pct": efficiency, "active_constraint_id": active,
            "binding_constraint_ids": binding, "capacity_basis": basis,
            "flags": sorted(set(flags)), "source_ids": sorted(set(source_ids)),
        })
    scale = max((max(a, m) for a, m in paired_values), default=0)
    period_efficiency = _percentage(
        sum(a / scale for a, _ in paired_values), sum(m / scale for _, m in paired_values),
    ) if scale else None
    warnings = []
    if _number(total_allocated) is None or _number(total_max) is None:
        warnings.append("A paired volume total exceeds numeric range and is null; period PE uses scaled daily volumes.")
    if period_efficiency is None and total_max > 0:
        warnings.append("Period PE exceeds numeric range and is null.")
    if any(p["capacity_basis"] == "reported_potential" for p in points):
        warnings.append("Reported oil potential is a fallback reference; its physical binding constraint is unknown.")
    if any(p["capacity_basis"] == "declared_limits" for p in points):
        warnings.append("Capacity is the minimum of declared whole-facility oil-equivalent bounds and reported well potential; coverage is not verified.")
    if any(p["flags"] and p["flags"] != ["physical_constraint_unknown"] for p in points):
        warnings.append("Data-quality flags are present. Missing values are not filled or treated as zero.")
    return {
        "since": since.isoformat(), "until": (end + timedelta(days=1)).isoformat(),
        "end": end.isoformat(), "step": "day", "unit": UNIT, "points": points,
        "constraints": [catalog[key] for key in sorted(catalog)],
        "summary": {"efficiency_pct": period_efficiency,
                    "allocated_sm3": _number(total_allocated), "dynamic_max_sm3": _number(total_max),
                    "paired_days": paired},
        "warnings": warnings,
    }


REPORTS = """
    select distinct p.fuzer_id as id, d.value #>> '{}' as date,
           a.value as allocated, v.value as potential, g.value as gas, x.value as gas_export
    from entity p join golden d on d.fuzer_id = p.fuzer_id and d.field = 'report_date'
    join edge e on e.src = p.fuzer_id and e.rel = 'reports_on' and e.dst = %(facility)s
    left join golden a on a.fuzer_id = p.fuzer_id and a.field = 'oil_sm3'
    left join golden v on v.fuzer_id = p.fuzer_id and v.field = 'oil_potential_sm3'
    left join golden g on g.fuzer_id = p.fuzer_id and g.field = 'gas_ksm3'
    left join golden x on x.fuzer_id = p.fuzer_id and x.field = 'gas_export_ksm3'
    where p.kind = 'production_day' and p.retired_at is null and p.merged_into is null
      and (e.valid_from is null or e.valid_from <= (d.value #>> '{}')::date)
      and (e.valid_to is null or e.valid_to > (d.value #>> '{}')::date)
"""
_NODE = """n.kind in ('area', 'tag', 'well', 'allocation_node')
              and n.retired_at is null and n.merged_into is null
              and (e.valid_from is null or e.valid_from <= s.day)
              and (e.valid_to is null or e.valid_to > s.day)"""
# Everything feeding the facility, plus the sales points it delivers to: those belong to no facility
# but takeaway still bounds it. Only sales points: other downstream nodes aren't this facility's.
NETWORK = f"""
    with recursive days(day) as (
        select generate_series(%(since)s::date, %(until)s::date - 1, interval '1 day')::date
    ), scope(day, id) as (
        select day, %(facility)s::text from days
        union
        select s.day, e.src from scope s join edge e on e.dst = s.id
        join entity n on n.fuzer_id = e.src
        where e.rel in ('part_of', 'located_at', 'flows_to') and {_NODE}
    ), network(day, id) as (
        select day, id from scope
        union
        select s.day, e.dst from scope s join edge e on e.src = s.id
        join entity n on n.fuzer_id = e.dst
        join golden g on g.fuzer_id = e.dst and g.field = 'ec_type' and g.value #>> '{{}}' = 'sales_point'
        where e.rel = 'flows_to' and {_NODE}
    )
"""


def latest_report_date(conn, facility: str) -> date | None:
    row = conn.execute("select max(date) as date from (" + REPORTS + ") r", {"facility": facility}).fetchone()
    return date.fromisoformat(row["date"]) if row["date"] else None


def declared_limits(conn, params: dict) -> list[dict]:
    """system_oil_capacity bounds on the network between `since` and `until`."""
    return conn.execute(
        NETWORK + """
        select s.day::text as date, m.fuzer_id as id, l.label, m.record, m.value, m.unit, m.qualifier, m.method,
               coalesce(c.value #>> '{}', t.value #>> '{}', 'unknown') as category
        from network s join measurement m on m.fuzer_id = s.id and m.at::date = s.day
        left join entity_label l on l.fuzer_id = s.id
        left join golden c on c.fuzer_id = s.id and c.field = 'constraint_category'
        left join golden t on t.fuzer_id = s.id and t.field = 'ec_type'
        where m.quantity = 'system_oil_capacity'
          and m.at >= %(since)s::timestamp and m.at < %(until)s::timestamp
        """, params,
    ).fetchall()


def production_efficiency(conn, facility: str, end: date | None = None) -> dict:
    """Read resolved reports and daily system-capacity measurements on the facility's graph."""
    facility_row = conn.execute(
        """select e.fuzer_id as id, coalesce(l.label, e.fuzer_id) as label
           from entity e left join entity_label l using (fuzer_id)
           where e.fuzer_id = %s and e.kind = 'facility'
             and e.retired_at is null and e.merged_into is null""", (facility,),
    ).fetchone()
    if not facility_row:
        raise ValueError("ref must resolve to a live facility")
    end = end or latest_report_date(conn, facility) or date.today()
    if not date.min + timedelta(days=DAYS - 1) <= end < date.max:
        raise ValueError("end must allow fourteen daily samples and an exclusive following day")
    since, until = end - timedelta(days=DAYS - 1), end + timedelta(days=1)
    params = {"facility": facility, "since": since.isoformat(), "until": until.isoformat()}
    reports = conn.execute(
        REPORTS + " and d.value #>> '{}' >= %(since)s and d.value #>> '{}' < %(until)s", params,
    ).fetchall()
    return {
        **calculate(reports, declared_limits(conn, params), end), "facility": dict(facility_row),
        "source": "Resolved production_day reports_on facility; dated asset graph and system_oil_capacity measurements",
    }
