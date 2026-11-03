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

## Live baseline, 2026-11-02 (14 questions)

The same eval after the agentic-ops work (#96–#107): five new questions (pump and choke curves, decline,
wall thickness, gas lift) and the new tools and skills. Claude Opus 5.5, orchestrator effort high,
specialists medium.

| | 2026-11-02 | 2026-10-29 |
|---|---|---|
| Questions | 14 | 9 |
| Expected facts named | 97.6% | 100% |
| Answers citing 3+ sources | 85.7% | 100% |
| No plant-operating instructions | 100% | 100% |
| Routed to the expected specialists | 100% | 100% |
| Expected chart kind drawn | 75% (3 of 4) | – |
| Cost per answer | $0.67 (total $9.31) | $0.55 (total $4.91) |
| Time per answer | 102 s (76 s without the wall thickness question) | 71 s |

| Domain | Specialists used | Facts | Citations | Charts | Tool calls | Time | Cost |
|---|---|---|---|---|---|---|---|
| curves: pump 10-P-1001A | maintenance, troubleshooter | 100% | 5 | pump curve, vibration | 30 | 80 s | $0.80 |
| curves: choke OK-J12-02 | maintenance, production | 100% | 8 | choke, custom | 40 | 82 s | $0.74 |
| charts: decline OK-J07-03 | production | 100% | 5 | measurements, custom (**not decline**) | 10 | 84 s | $0.40 |
| charts: wall thickness | maintenance | **67%** (missed OK-J03-02) | **2** | wall ranking, measurements | 26 | **442 s** | $0.59 |
| troubleshooting | maintenance, troubleshooter | 100% | 12 | trend | 33 | 74 s | $0.74 |
| production efficiency | production | 100% | 8 | bar ×2 | 8 | 48 s | $0.31 |
| production surveillance | production | 100% | 10 | bar ×2, custom | 14 | 75 s | $0.42 |
| constraint management | maintenance, production | 100% | 10 | bar ×2 | 61 | 165 s | $1.60 |
| production engineering | production | 100% | **2** | measurements, bar | 21 | 68 s | $0.46 |
| reliability | maintenance | 100% | 8 | bar ×2 | 17 | 66 s | $0.53 |
| work orders | maintenance | 100% | 3 | custom | 8 | 52 s | $0.46 |
| turnarounds | maintenance | 100% | 8 | none | 7 | 48 s | $0.42 |
| gas lift | production | 100% | 6 | gas lift allocation | 14 | 56 s | $0.41 |
| across domains | maintenance, production, troubleshooter | 100% | 5 | bar | 52 | 92 s | $1.44 |

Compared with 2026-10-29, on the 9 questions both runs asked: cost per answer rose from $0.55 to $0.71 ($4.91 to $6.37 in total).
The two broad questions rose the most: constraint management from $0.85 to $1.60 (61 tool calls against 40)
and across domains from $1.02 to $1.44. Answers drew charts but never looked at one (`look_at_figure`, 0 times).

To look into:
- **Decline:** the production agent drew the well's measurements and a custom chart instead of
  `plot_decline`. The skill should send decline and forecast questions to `plot_decline`.
- **Wall thickness:** 442 s, it missed OK-J03-02 and it cited 2 sources. Something in that path is slow
  (wall-loss calc, measurement reads or a retry) and the ranking leaves a line out.
- **Citations:** two answers cited fewer than 3 sources (wall thickness, wells that lost rate).

## SRE detection baseline (#103)

`uv run fuzer --pack okan eval --ops --detector limits --since 2024-04-15 --until 2025-12-15`: the #101 excursions
(alarm limits, IOWs and learned normal ranges, counted while units run) replayed in 6-hour windows over
20 months of Okan, scored against the process model's injected events. Run 2026-11-01. This is the
stand-in that the #96 detectors have to beat.

| | |
|---|---|
| Upsets | 41 |
| Found | 39 (recall 0.95) |
| Detections | 5,619 |
| False alarms | 1,022 (11.7 a week) |
| Precision | 0.57 |
| Windows | 2,436, about 0.5 s each |

| Kind | Upsets | Found | Median hours to detect |
|---|---|---|---|
| trip | 7 | 7 | 3.0 |
| shutdown | 1 | 1 | 0.0 |
| shut_in | 18 | 16 | 4.0 |
| discharge_temperature | 5 | 5 | 1.2 |
| level | 2 | 2 | 3.1 |
| erratic (bad instrument) | 1 | 1 | 28 |
| restriction (hydrate) | 2 | 2 | 1.2 |
| valve_failed_open | 2 | 2 | 2.5 |
| choke_wear | 2 | 2 | 225 |
| head_loss | 1 | 1 | 3,348 |

Recall is high because limits on many points catch most upsets eventually. Each slow upset (head loss,
choke wear, the bad instrument) is "found" only once it has pushed a value past a limit, days to months
in. Most false alarms are learned-range alarms on wellhead temperatures swinging day to night. The #96
detectors should find the slow ones in hours to days from the calcs (pump and choke against their
curves) and the signal checks (instrument health, drift).

### SRE detectors (#96, before grouping)

`uv run fuzer --pack okan eval --ops --detector sre --since 2025-01-01 --until 2025-10-01`, run 2026-11-02:
all six detectors (`sre.py`) over nine months of Okan.

| | SRE detectors | limits baseline (Apr 2024 – Dec 2025) |
|---|---|---|
| Upsets found | 21 of 21 | 39 of 41 |
| Pump head loss found after | 12 h | 3,348 h |
| Choke wear found after | 162 h | 225 h |
| Trips found after (median) | 3.2 h | 3.0 h |
| False alarms a week | 37.7 | 11.7 |
| Precision | 0.65 | 0.57 |
| Seconds per window | 2.5 | 0.5 |

False alarms by detector, in two January 2025 weeks:
- steps: 8 a week;
- instruments: 0.5 a week;
- excursions and stops: none.

Over the nine months, drifts and models raise 1 false alarm between them. Almost all the false alarms are
steps, and most of those are many points stepping together on one cause (every well on jacket J03 at
08:00 on 9 January 2025). Steps are building blocks for grouping (#96b), which makes them one upset, so
the next score counts upsets rather than detections.

With grouping, replayed on the same periods (2026-11-02):
- **4–10 Nov 2024:** each train C trip is one upset, starting from its cause (20-V-2001's high level),
  with 1.2 false upsets a week. The one false upset is a single model detection.
- **1–14 Jan 2025:** the 17 false-alarm detections, mostly the J03 steps, fall into upsets an event
  explains, so no false upsets.

## Full ops eval, live (#103b), 2026-11-02

`uv run fuzer --pack okan eval --ops --full --live`, with the default window: the first injected trip at least 90 days
into the data, 10–17 June 2024 (the train A trip, caused by 20-K-2300A's discharge temperature). Live
diagnosis on Claude Opus 5.5. It passed every gate in 16 minutes.

| | |
|---|---|
| Upsets found | 3 of 3: trip in 3 h, discharge temperature in 6 h, well shut-in in 4 h |
| Grouping | the trip is one upset, first cause 20-K-2300A; 1 false upset a week |
| Diagnosis | 5 diagnosed for $3.05 ($0.61 each, about 1.5 min each); 2 matched an injected upset and both named the cause, with citations (too few to gate) |
| Daily reports | 7 days, every check passed; the trip day leads with the trip; no operating instructions |
| Opportunities | 3 gas lift moves; gains within 0.04% of a hand calculation |

Single detections ran at 15 a week (precision 0.78). Grouping made 203 upsets, 182 of them instrument
upsets that the plant's instrument events explain. They aren't false, but they are too many to show an
operator one by one (#141). The one false upset is a WHP alarm already on when the window starts (#142).

An earlier run on 22–29 February 2024 had no injected upsets in its window and started only 50 days into the
data, too early for 90 days of learned normal ranges, so it scored nothing useful. The eval now warns about a window with no upsets
and picks a scorable one by default.

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
