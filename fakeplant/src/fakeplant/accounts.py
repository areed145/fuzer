"""Okan's production accounting and cost ledger: the books the operational figures are reconciled against (#372).

Production accounting closes each month on the custody meters, so its volumes sit a little under the sum of the
daily allocated figures (shrinkage and meter factors). Two stories:

    2025-08  oil is booked 3.2 % under the daily sum: the export meter was proved mid-month and its factor applied
             to the whole month in accounting, while the daily reports kept the old factor
    2025-12  the month isn't closed: its volumes are preliminary

The cost ledger books operating cost by cost element each month in USD, except logistics, which a local contractor
bills in NGN. Its own Random leaves every other export untouched.
"""

import random
from collections import defaultdict

Row = dict[str, str]

ELEMENTS = [  # cost element, cost centre, monthly USD around
    ("Maintenance", "OKAN-OPS-MNT", 1_450_000),
    ("Chemicals", "OKAN-OPS-CHM", 380_000),
    ("Personnel", "OKAN-OPS-PER", 2_100_000),
    ("Well services", "OKAN-OPS-WEL", 520_000),
]
LOGISTICS_NGN = 410_000_000  # a month's marine logistics, billed in NGN
SHRINK = {"oil": 0.004, "gas": 0.006}  # accounting under the daily sum, in a normal month
PROVED = {("2025-08", "oil"): 0.032}  # the export meter's proving, applied to the whole month in accounting
OPEN = "2025-12"  # not closed yet


def _end(month: str, days: list[str]) -> str:
    return max(d for d in days if d.startswith(month))


def ledger(daily: list[dict], field: str, seed: int) -> dict[str, list[Row]]:
    rng = random.Random(seed + 40)
    sums: dict[tuple[str, str], float] = defaultdict(float)
    days = [d["report_date"] for d in daily]
    for d in daily:
        sums[(d["report_date"][:7], "oil")] += float(d["oil_sm3"])
        sums[(d["report_date"][:7], "gas")] += float(d["gas_ksm3"])
    months = sorted({m for m, _ in sums})
    volumes = []
    for m in months:
        start, end = f"{m}-01", _end(m, days)
        if m == OPEN:  # the books close on the calendar month, so its last day is the month's
            end = f"{m}-31"
        for product, unit in (("oil", "Sm3"), ("gas", "ksm3")):
            off = PROVED.get((m, product), SHRINK[product]) + rng.uniform(-0.002, 0.002)
            volumes.append({"entry_id": f"PA-{m}-{product.upper()}", "period": m, "period_start": start,
                            "period_end": end, "facility": field, "product": product,
                            "volume": f"{sums[(m, product)] * (1 - off):.0f}", "unit": unit,
                            "status": "preliminary" if m == OPEN else "final",
                            "comment": "Export meter proved 2025-08-14; new factor applied to the month."
                            if (m, product) in PROVED else ""})
    costs = []
    for m in months:
        if m == OPEN:
            continue  # the ledger hasn't closed December
        for element, centre, around in ELEMENTS:
            costs.append({"entry_id": f"GL-{m}-{centre}", "period": m, "period_start": f"{m}-01",
                          "period_end": _end(m, days), "facility": field, "cost_element": element,
                          "cost_centre": centre, "amount": f"{around * rng.uniform(0.85, 1.15):.2f}",
                          "currency": "USD"})
        costs.append({"entry_id": f"GL-{m}-OKAN-OPS-LOG", "period": m, "period_start": f"{m}-01",
                      "period_end": _end(m, days), "facility": field, "cost_element": "Logistics",
                      "cost_centre": "OKAN-OPS-LOG", "amount": f"{LOGISTICS_NGN * rng.uniform(0.9, 1.1):.2f}",
                      "currency": "NGN"})
    return {"acct_volumes": volumes, "acct_cost_ledger": costs}
