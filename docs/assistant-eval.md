# Operations assistant: evaluation on the Okan demo

`uv run fuzer eval --agents` with `answer_keys/assistant.json`: one question per domain plus one across
domains, each with the facts a good answer must name given the planted stories. Run 2026-10-29 on
Claude Opus 5.5 (orchestrator effort high, specialists medium), Okan demo data, synthetic process values.

| | |
|---|---|
| Questions | 9 |
| Expected facts named | 100% |
| Answers citing 3+ sources | 100% |
| No plant-operating instructions | 100% |
| Routed to the expected specialists | 100% |
| Cost per answer | $0.55 (total $4.91) |
| Time per answer | 71 s |

| Domain | Specialists used | Facts | Citations | Safe | Tool calls | Time | Cost |
|---|---|---|---|---|---|---|---|
| troubleshooting | maintenance, troubleshooter | 100% | 4 | yes | 28 | 72 s | $0.64 |
| production efficiency | production | 100% | 10 | yes | 9 | 53 s | $0.26 |
| production surveillance | maintenance, production | 100% | 8 | yes | 21 | 68 s | $0.55 |
| constraint management | maintenance, production | 100% | 15 | yes | 40 | 101 s | $0.85 |
| production engineering | production | 100% | 10 | yes | 13 | 63 s | $0.32 |
| reliability | maintenance | 100% | 10 | yes | 18 | 72 s | $0.39 |
| work orders | maintenance, production | 100% | 10 | yes | 34 | 66 s | $0.59 |
| turnarounds | maintenance | 100% | 8 | yes | 5 | 52 s | $0.30 |
| across domains | maintenance, production, troubleshooter | 100% | 8 | yes | 53 | 90 s | $1.02 |

The first scoring flagged three answers; all three were scorer errors, fixed before this table (the
same answers re-scored, nothing re-asked): "Sm3" written as "Sm³", backlog priority written as "P2 / P3",
and "Close PM-30000026" (closing a maintenance plan) counted as an operating instruction.

What these scores don't say: whether the advice is good. The checks are mechanical (named facts,
citations, no operating verbs, routing). Engineers rating answers on the Ask page (feedback
`answer:<id>`) is the real measure.

## Example: across domains

> Why does lift gas keep running short, and what should go in the next turnaround to fix it?

I found two separate compressor problems behind the lift gas shortfalls, and the scrubber isn't one of them. Lift gas only runs short when one of these trips. 20-K-2200B [fz-tag-124c5228af4c] trips on vibration from worn bearings, and 20-K-2300A [fz-tag-f6f2109d9ba5] trips on high discharge temperature every summer because its after-cooler, 20-E-2350A, fouls. Data runs to 2025-12-30.

**What the records show**

| Unit | Trips (EC downtime) | Lost oil | Trend |
|---|---|---|---|
| 20-K-2200B, vibration | 2024-03-14 (7 h), 2024-03-29 (11 h), OKDT-00001/2 | 1,088 Sm³ | Improving: none since the bearing change (WO 50000266, Apr 2024) |
| 20-K-2300A, discharge temp | Jun–Jul 2024 (3 × 3 h), 2025-06-24 and 07-16 (2 × 4 h, "after-cooler fouled"), OKDT-00003 to 00007 | 425 Sm³ | Getting worse: back every summer, outages getting longer |

- **K-2200B:** SAP notification 20000124 (2024-03-09) warned of vibration before both trips, but the bearings weren't changed until 2024-04-03. A noise / alignment notification is still open (20000078, 2025-09-21), and alignment was never confirmed. Troubleshooting guide TG-20-001 rev A, "Symptoms and actions", says two trips in 30 days means a bearing inspection rather than another restart.
- **K-2300A:** E-2350A was cleaned in 2024 and 2025 (WO 50000267/268) and fouled again. Its time between failures is shrinking, from about 560 to about 357 days. Meanwhile the trip transmitters were replaced twice and the trip setpoint was changed (WO 50000107/108/110), so the trip was treated as a bad signal.
- **Scrubber ruled out:** the scrubber leak (notification 20000014, scrubber [fz-equipment-ae2682725da3]) was repaired in 2022, two years before the trips.
- **Under-reported:** the K-2300A trips are coded "Process trip", so lift gas losses look smaller than they are in reports.
- **No sign of a standing shortage:** monthly lift gas is flat at 1,150 kSm³/d, summers included. Without nameplate capacity or demand figures I can't rule out that 1,150 is itself a cap.

**What to put in the next turnaround (OK-TAR-2026, starts 2026-04-06)**
- **Keep (already in scope):**
  - WO 50000303: K-2200B overhaul (bearings, dry gas seals, impellers). Include an alignment check against 20000078.
  - WO 50000304: retube the E-2350A bundle.
  - INSP-30000020: E-2350A internal inspection.
- **Add:**
  - K-2300A bearing, alignment and trip-instrument inspection. It isn't in scope at all, and trip notification 20000049 (2025-11-15) has no work order.
  - INSP-30000009: the statutory scrubber internal inspection. It was due 2025-07-15, is 168 days overdue, and isn't in scope.
- **Order now:**
  - C40 gasket set MAT-100036: none in stock.
  - A second C40 seal kit, MAT-100034: only 1 in stock and both compressors need one.
  - SAP materials for an E-2350A tube bundle and for scrubber internals: neither exists yet.

**Gaps:** the PI values the specialists saw are marked synthetic, so I didn't rely on them. 20-FT-3002 doesn't match EC volumes. There are no lift gas response curves for the wells.

**Next steps**
1. Before June 2026: have process engineering measure the E-2350A approach temperature and cooling water flow (TG-20-001 calls for cleaning above 15 °C approach), and check whether the K-2300A recycle valve is passing (OEM-C40 rev A, §5, p.5). The retube comes after the summer risk starts, so the cooler needs attention before then.
2. Planner: raise the K-2300A scope and INSP-30000009 into OK-TAR-2026, and place the spares orders.
3. Production: re-code the K-2300A losses as lift gas shortfall. Agree a lift gas priority list of the best-responding wells on J07, J12 and J15 for trip events. TG-20-001 also describes a lift gas bypass (20-XV-3100) for low header pressure; using it is the operator's call under site procedures.
