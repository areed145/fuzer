"""Maintenance records that say less, or something else, than they should (#890): the same fault raised twice,
a notification filed on the wrong place, and an order closed without a word on what was done.

- **Duplicates**: two notifications on the same item, created within `DUPLICATE_DAYS` of each other, for the
  same damage code or with mostly the same words. The later one is listed as the duplicate.
- **Misfiled**: a notification on an area or system that holds items, whose text names one of them (by its
  description, or its tag typed short, such as K2200B); or one on an item whose text names its twin's tag
  instead (2200A on a notification filed on 2200B). Each comes with what the text points at.
- **Closed without confirmation**: closed orders whose confirmation text is blank or a word or two such as
  "done". Only where the export carries confirmation texts at all.

Each finding is a question for the maintenance planner, not proof: two notifications on one item within days
can be two faults, and a text can mention a neighbour without being about it.
"""

import re
from collections import defaultdict
from datetime import date

from ..graph import operations as ops

DUPLICATE_DAYS = 3
SAME_WORDS = 0.5
CLOSED = ("TECO", "CLSD")
TRIVIAL = re.compile(r"^\s*(done|ok|okay|complete[d]?|job (done|complete[d]?)|as per (wo|work order|plan)|n/?a|-)\W*$", re.IGNORECASE)
SHORT_TAG = re.compile(r"\b([A-Z]{1,4})-?(\d{4}[A-Z]?)\b")
STOP = {"on", "of", "the", "a", "an", "to", "in", "at", "for", "and", "again", "by", "raised", "shift", "night"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOP and len(w) > 1}


def _day(v) -> date | None:
    s = str(v or "")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _short(label: str) -> str:
    """A tag as it's typed short: 20-K-2200B -> K2200B."""
    parts = label.split("-")
    return "".join(parts[1:]) if len(parts) >= 3 else label.replace("-", "")


def check(conn) -> dict:
    notes = ops._rows(conn, "notification")
    ids = [n["id"] for n in notes]
    on: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute(
        """select c.src, c.dst, t.kind, l.label from edge c join entity t on t.fuzer_id = c.dst
           left join entity_label l on l.fuzer_id = c.dst where c.rel = 'concerns' and c.src = any(%s)""", (ids,)):
        on[r["src"]].append(r)
    # Tags (with their description and installed items' descriptions) by the place they sit under.
    tags = {r["id"]: r for r in conn.execute(
        """select t.fuzer_id as id, l.label, coalesce(gd.value #>> '{}', '') as description
           from entity t left join entity_label l using (fuzer_id)
           left join golden gd on gd.fuzer_id = t.fuzer_id and gd.field = 'description'
           where t.kind = 'tag' and t.retired_at is null and t.merged_into is null""")}
    items = defaultdict(set)
    for r in conn.execute(
        """select i.dst as tag, coalesce(gd.value #>> '{}', '') as description from edge i
           left join golden gd on gd.fuzer_id = i.src and gd.field = 'description' where i.rel = 'installed_at'"""):
        items[r["tag"]].add(r["description"].lower())
    under = defaultdict(list)
    for r in conn.execute("select src, dst from edge where rel = 'part_of' and src = any(%s)", (list(tags),)):
        under[r["dst"]].append(r["src"])
    by_short = defaultdict(list)
    for t in tags.values():
        if t["label"]:
            by_short[_short(t["label"])].append(t)

    def tag_of(n):
        return next((x for x in on[n["id"]] if x["kind"] == "tag"), None)

    def item_of(n):
        return next((x["dst"] for x in on[n["id"]] if x["kind"] in ("equipment", "tag")), None)

    duplicates = []
    by_item = defaultdict(list)
    for n in notes:
        if (item := item_of(n)) and _day(n["f"].get("created")):
            by_item[item].append(n)
    for group in by_item.values():
        group.sort(key=lambda n: (_day(n["f"]["created"]), n["label"]))
        for i, b in enumerate(group):
            for a in group[:i]:
                gap = (_day(b["f"]["created"]) - _day(a["f"]["created"])).days
                if gap > DUPLICATE_DAYS:
                    continue
                wa, wb = _words(a["f"].get("short_text")), _words(b["f"].get("short_text"))
                overlap = len(wa & wb) / max(1, min(len(wa), len(wb)))
                same_code = a["f"].get("damage_code") and a["f"].get("damage_code") == b["f"].get("damage_code")
                if same_code or overlap >= SAME_WORDS:
                    duplicates.append({"id": b["id"], "notification": b["label"], "duplicate_of": a["label"],
                                       "duplicate_of_id": a["id"], "days_apart": gap, "same_damage_code": bool(same_code),
                                       "shared_words": round(overlap, 2), "text": b["f"].get("short_text")})
                    break

    misfiled = []
    for n in notes:
        text = n["f"].get("short_text") or ""
        places = [x for x in on[n["id"]] if x["kind"] not in ("equipment", "tag")]
        tag = tag_of(n)
        if tag is None and places:
            # On a place: which of the tags under it does the text name?
            words = _words(text)
            hits = []
            for p in places:
                for tid in under[p["dst"]]:
                    descs = {tags[tid]["description"].lower(), *items[tid]} - {""}
                    if any(d and _words(d) <= words for d in descs) or _short(tags[tid]["label"] or "") in text:
                        hits.append(tags[tid]["label"])
            if hits:
                misfiled.append({"id": n["id"], "notification": n["label"], "kind": "area", "filed_on": places[0]["label"],
                                 "text_names": sorted(set(hits)), "text": text})
            continue
        if tag is None:
            continue
        own = _short(tag["label"] or "")
        for m in SHORT_TAG.finditer(text):
            typed = m.group(1) + m.group(2)
            if typed != own and typed in by_short and own[:-1] == typed[:-1]:
                misfiled.append({"id": n["id"], "notification": n["label"], "kind": "twin", "filed_on": tag["label"],
                                 "text_names": sorted(t["label"] for t in by_short[typed]), "text": text})
                break

    orders = ops._rows(conn, "work_order")
    exported = any("confirmation" in o["f"] for o in orders)
    closed = [o for o in orders if o["f"].get("status") in CLOSED]
    no_text = [{"id": o["id"], "order": o["label"], "finish": o["f"].get("finish"), "confirmation": o["f"].get("confirmation") or "",
                "text": o["f"].get("text")}
               for o in closed if exported and (not (o["f"].get("confirmation") or "").strip()
                                                or TRIVIAL.match(o["f"].get("confirmation") or ""))]
    return {
        "duplicates": duplicates, "misfiled": misfiled,
        "closed_without_confirmation": no_text if exported else None,
        "counts": {"notifications": len(notes), "duplicates": len(duplicates), "misfiled": len(misfiled),
                   "closed_orders": len(closed), "closed_without_confirmation": len(no_text) if exported else None},
        "basis": f"duplicates: same item, created within {DUPLICATE_DAYS} days, same damage code or at least "
                 f"{SAME_WORDS:.0%} of the words shared; misfiled: filed on a place whose text names an item under it, "
                 "or on an item whose text names its twin; closed without confirmation: closed orders whose "
                 "confirmation is blank or a word such as 'done'"
                 + ("" if exported else " (not checked: the work order export carries no confirmation texts)"),
    }
