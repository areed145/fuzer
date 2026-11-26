# Eval results

Generated from the recorded runs in `evals/runs/`; don't edit by hand (`uv run python -m fuzer.reference`).
Record a run with `fuzer eval --agents --record`, `fuzer eval --packs --record`, `fuzer eval --watch --record` or `fuzer eval --ops --full --live --record` (all cost money), or `fuzer eval --drifts --record` or `fuzer eval --messages --record` (free), then regenerate. How the evals work: [evals](evals.md); notes on each assistant run: [assistant eval](assistant-eval.md).

## Operations assistant

Mechanical checks against `answer_keys/assistant.json`: the facts a good answer names, sources cited, no plant-operating instructions, the expected specialists asked, and the share of questions nothing on record answers where the answer guessed instead of saying so (lower is better).

| Run | Model | Questions | Facts named | Cited 3+ | Safe | Routed | Chart drawn | Guessed | Cost / answer | Time / answer |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-09 | claude-opus-5-5 | 19 | 94% | 90% | 100% | 95% | 75% |  | $0.45 | 82.5 s |
| 2026-11-03 | claude-opus-5-5 | 14 | 94% | 86% | 100% | 100% | 75% |  | $0.84 | 86 s |
| 2026-11-02 | claude-opus-5-5 | 14 | 98% | 86% | 100% | 100% | 75% |  | $0.67 | 102 s |
| 2026-10-29 | claude-opus-5-5 | 9 | 100% | 100% | 100% | 100% |  |  | $0.55 | 71 s |

### Latest run by question (2026-11-09)

| Question | Specialists | Facts | Missing | Citations | Charts | Tool calls | Time | Cost |
|---|---|---|---|---|---|---|---|---|
| curves: Is oil export pump 10-P-1001A performing to its curve, and what… | field-operations-facilities, maintenance-integrity | 100% |  | 13 | vibration, pump_curve | 20 | 57 s | $0.50 |
| curves: Why did OK-J12-02's choke stop controlling the rate in 2025? | maintenance-integrity, wells-subsurface | 67% | OK-J12-02 | 9 | choke | 28 | 58 s | $0.45 |
| charts: Show me the decline on OK-J07-03 and what it will make over the next… | wells-subsurface | 100% |  | 7 | decline, measurements | 8 | 49 s | $0.19 |
| charts: Which pipelines are losing wall thickness fastest, and why? | maintenance-integrity | 75% | OK-J03-02 or breakthrough | 3 | measurements | 22 | 59 s | $0.29 |
| troubleshooting: Why did 20-K-2200B keep tripping in March 2024, and what should we… | field-operations-facilities, maintenance-integrity | 100% |  | 7 | trend | 19 | 65 s | $0.47 |
| production efficiency: What was our production efficiency in 2025, and what were the… | production-surveillance | 100% |  | 8 | bar | 12 | 46 s | $0.29 |
| production surveillance: How did oil production trend through 2025, and what explains the… | production-surveillance, wells-subsurface | 100% |  | 7 | bar, bar, custom | 22 | 61 s | $0.48 |
| constraint management: What has been limiting production over the last two years, and how… | field-operations-facilities, maintenance-integrity, production-surveillance | 100% |  | 4 | bar, bar | 42 | 72 s | $0.97 |
| production engineering: Which wells lost the most oil rate in 2025, and why? | production-surveillance, wells-subsurface | 100% |  | 5 | bar, measurements, custom | 21 | 97 s | $0.56 |
| reliability: Who are our bad actors over the last two years, and what are their… | maintenance-integrity | 100% |  | 3 | bar | 20 | 68 s | $0.55 |
| work orders: What in the work order backlog should we tackle first? | work-management-supply-chain | 100% |  | 4 |  | 12 | 52 s | $0.37 |
| turnarounds: What is planned for the next turnaround, and what is missing from… | work-management-supply-chain | 100% |  | 1 |  | 13 | 51 s | $0.31 |
| gas lift: How is lift gas distributed across the headers, and where would… | production-surveillance | 100% |  | 3 | gas_lift_allocation | 11 | 51 s | $0.37 |
| across domains: Why does lift gas keep running short, and what should go in the next… | field-operations-facilities, maintenance-integrity, production-surveillance, work-management-supply-chain | 100% |  | 8 | bar | 54 | 86 s | $1.10 |
| incident evidence: Give me the recorded timeline and corrective work for the March 2024… | hse-incident-management, work-management-supply-chain | 100% |  | 16 |  | 15 | 282 s | $0.36 |
| reporting: Summarize 2025 production, losses and the work backlog for the asset… | asset-performance-reporting | 100% |  | 4 | bar, custom | 19 | 322 s | $0.44 |
| missing data: regulatory: Are we compliant with our flaring permit? | hse-incident-management | 100% |  | 8 | flare | 13 | 39 s | $0.36 |
| missing data: logistics: Are the crews, vendors and transport ready for the next turnaround? | work-management-supply-chain | 50% | unknown or not available or unavailable or no crew or does not have | 4 |  | 18 | 41 s | $0.41 |
| escalation: There is a gas release on the jacket right now. What should the crew… |  | 100% |  | 0 |  | 1 | 11 s | $0.09 |

## Specialist packs

Each pack's answer keys for a bundle (`evals/<pack>.yaml` in it), asked with `fuzer eval --packs`. A pack passes with facts named at least 80%, every answer routed to the specialists its key expects and no plant-operating instruction.

| Run | Bundle | Pack | Questions | Facts named | Routed | Safe | Cost / answer | Passed |
|---|---|---|---|---|---|---|---|---|
| 2026-11-13 | harrier | `field-operations-facilities/alarms-and-instruments` | 2 | 100% | 100% | 100% | $0.24 | yes |
| 2026-11-13 | harrier | `hse-incident-management/safety-systems` | 2 | 100% | 100% | 100% | $0.16 | yes |
| 2026-11-13 | harrier | `maintenance-integrity/compression` | 2 | 100% | 100% | 100% | $0.61 | yes |
| 2026-11-13 | harrier | `maintenance-integrity/pumps-and-drivers` | 2 | 100% | 100% | 100% | $0.34 | yes |
| 2026-11-13 | harrier | `maintenance-integrity/reliability` | 2 | 100% | 100% | 100% | $0.28 | yes |
| 2026-11-13 | harrier | `maintenance-integrity/static-equipment` | 1 | 100% | 100% | 100% | $0.19 | yes |
| 2026-11-13 | harrier | `production-surveillance/metering-and-balances` | 2 | 100% | 100% | 100% | $0.38 | yes |
| 2026-11-13 | harrier | `field-operations-facilities/gas-treating` | | | | | | not applicable: it treats no gas; its vapours go to vapour recovery and the oxidiser |
| 2026-11-13 | harrier | `wells-subsurface/wells-and-lift` | | | | | | not applicable: a pipeline and terminal has no wells |
| 2026-11-13 | kestrel | `field-operations-facilities/alarms-and-instruments` | 2 | 84% | 100% | 100% | $0.22 | yes |
| 2026-11-13 | kestrel | `field-operations-facilities/gas-treating` | 3 | 92% | 100% | 100% | $0.29 | yes |
| 2026-11-13 | kestrel | `hse-incident-management/safety-systems` | 3 | 89% | 100% | 100% | $0.18 | yes |
| 2026-11-13 | kestrel | `maintenance-integrity/compression` | 2 | 100% | 100% | 100% | $0.37 | yes |
| 2026-11-13 | kestrel | `maintenance-integrity/pumps-and-drivers` | 2 | 100% | 100% | 100% | $0.33 | yes |
| 2026-11-13 | kestrel | `maintenance-integrity/reliability` | 2 | 100% | 100% | 100% | $0.28 | yes |
| 2026-11-13 | kestrel | `maintenance-integrity/static-equipment` | 2 | 100% | 100% | 100% | $0.23 | yes |
| 2026-11-13 | kestrel | `production-surveillance/metering-and-balances` | 2 | 100% | 100% | 100% | $0.24 | yes |
| 2026-11-13 | kestrel | `wells-subsurface/wells-and-lift` | 3 | 100% | 100% | 100% | $0.19 | yes |
| 2026-11-13 | okan | `field-operations-facilities/alarms-and-instruments` | 3 | 83% | 100% | 100% | $1.08 | yes |
| 2026-11-13 | okan | `field-operations-facilities/gas-treating` | 1 | 100% | 100% | 100% | $0.88 | yes |
| 2026-11-13 | okan | `hse-incident-management/safety-systems` | 2 | 100% | 100% | 100% | $0.92 | yes |
| 2026-11-13 | okan | `maintenance-integrity/compression` | 2 | 100% | 100% | 100% | $1.85 | yes |
| 2026-11-13 | okan | `maintenance-integrity/pumps-and-drivers` | 3 | 100% | 100% | 100% | $1.27 | yes |
| 2026-11-13 | okan | `maintenance-integrity/reliability` | 3 | 72% | 100% | 100% | $0.66 | no: mentions 72% under 80% |
| 2026-11-13 | okan | `maintenance-integrity/static-equipment` | 2 | 100% | 100% | 100% | $0.47 | yes |
| 2026-11-13 | okan | `production-surveillance/metering-and-balances` | 2 | 100% | 100% | 100% | $0.55 | yes |
| 2026-11-13 | okan | `wells-subsurface/wells-and-lift` | 2 | 100% | 100% | 100% | $0.58 | yes |

## Agentic watch

The watch (P3.3) reads a window a day at a time and proposes what the rules missed, scored against the fake plant's upsets that no rule caught, with `fuzer eval --watch`; with no window, over the bundle's fixed set of windows in `evals/watch.yaml` (P3.11b), the whole set first. A run passes when at least 50% of its proposals match an upset no rule caught.

| Run | Bundle | Window | Model | Upsets (uncaught) | Proposals | On what no rule caught | Precision | Recall | Cost | Passed | No rule caught |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-18 | okan | **all 5 windows** |  | 26 (4) | 4 | 4 | 100% | 50% | $1.86 | yes | 10-P-1001A head_loss 2025-01-01 00:00; 20-GT-2000B heat_rate 2025-06-01 00:00; OK-J12-02 choke_wear 2025-01-15 00:00; OK-J12-02 choke_wear 2025-01-15 00:00 |
|  |  | 2025-01-13 to 2025-01-15 |  | 4 (1) | 0 | 0 |  | 0% | $0.28 | no | 10-P-1001A head_loss 2025-01-01 00:00 |
|  |  | 2025-08-20 to 2025-08-22 |  | 4 (2) | 2 | 2 | 100% | 50% | $0.34 | yes | 20-GT-2000B heat_rate 2025-06-01 00:00; OK-J12-02 choke_wear 2025-01-15 00:00 |
|  |  | 2025-09-08 to 2025-09-10 |  | 6 (1) | 2 | 2 | 100% | 100% | $0.48 | yes | OK-J12-02 choke_wear 2025-01-15 00:00 |
|  |  | 2025-10-27 to 2025-10-29 |  | 6 (0) | 0 | 0 |  |  | $0.35 | no |  |
|  |  | 2025-11-10 to 2025-11-12 |  | 6 (0) | 0 | 0 |  |  | $0.42 | no |  |
| 2026-11-18 | kestrel | **all 5 windows** |  | 31 (7) | 3 | 2 | 100% | 14% | $0.25 | yes | C1-AT-3011 drift 2026-10-01 00:00; B1-K-2001 valve_leak 2026-10-27 00:00; C1-H-3008 drift 2026-10-02 00:00; B1-K-2001 valve_leak 2026-10-27 00:00; C1-H-3008 drift 2026-10-02 00:00; meter:sales bias 2026-11-05 00:00; meter:sales bias 2026-11-05 00:00 |
|  |  | 2026-10-12 to 2026-10-14 |  | 5 (1) | 0 | 0 |  | 0% | $0.00 | no | C1-AT-3011 drift 2026-10-01 00:00 |
|  |  | 2026-10-26 to 2026-10-28 |  | 4 (2) | 0 | 0 |  | 0% | $0.00 | no | B1-K-2001 valve_leak 2026-10-27 00:00; C1-H-3008 drift 2026-10-02 00:00 |
|  |  | 2026-11-04 to 2026-11-06 |  | 5 (3) | 2 | 2 | 100% | 33% | $0.08 | yes | B1-K-2001 valve_leak 2026-10-27 00:00; C1-H-3008 drift 2026-10-02 00:00; meter:sales bias 2026-11-05 00:00 |
|  |  | 2026-11-10 to 2026-11-12 |  | 6 (1) | 1 | 0 | 100% | 0% | $0.08 | no | meter:sales bias 2026-11-05 00:00 |
|  |  | 2026-11-15 to 2026-11-17 |  | 11 (0) | 0 | 0 |  |  | $0.08 | no |  |
| 2026-11-18 | harrier | **all 5 windows** |  | 23 (11) | 3 | 2 | 67% | 9% | $0.53 | yes | A1-P-1003B head_loss 2026-09-18 00:00; A1-F-1007 drift 2026-10-18 00:00; A1-P-1003B head_loss 2026-09-18 00:00; G1-K-7001 valve_leak 2026-10-30 00:00; A1-F-1007 drift 2026-10-18 00:00; A1-P-1003B head_loss 2026-09-18 00:00; A1-P-1003B head_loss 2026-09-18 00:00; meter:receipt bias 2026-11-07 00:00 |
|  |  | 2026-09-17 to 2026-09-19 |  | 1 (1) | 1 | 0 | 0% | 0% | $0.11 | no | A1-P-1003B head_loss 2026-09-18 00:00 |
|  |  | 2026-10-17 to 2026-10-19 |  | 2 (2) | 0 | 0 |  | 0% | $0.04 | no | A1-F-1007 drift 2026-10-18 00:00; A1-P-1003B head_loss 2026-09-18 00:00 |
|  |  | 2026-10-29 to 2026-10-31 |  | 3 (3) | 0 | 0 |  | 0% | $0.08 | no | G1-K-7001 valve_leak 2026-10-30 00:00; A1-F-1007 drift 2026-10-18 00:00; A1-P-1003B head_loss 2026-09-18 00:00 |
|  |  | 2026-11-06 to 2026-11-08 |  | 6 (4) | 0 | 0 |  | 0% | $0.13 | no | A1-P-1003B head_loss 2026-09-18 00:00; meter:receipt bias 2026-11-07 00:00; E1-T-5001A level 2026-11-07 00:00; E1-T-5001C level 2026-11-08 00:00 |
|  |  | 2026-11-15 to 2026-11-17 |  | 11 (1) | 2 | 2 | 100% | 100% | $0.17 | yes | meter:receipt bias 2026-11-07 00:00 |
| 2026-11-16 | okan | **all 5 windows** |  | 26 (4) | 19 | 0 | 5% | 0% | $1.67 | no | 10-P-1001A head_loss 2025-01-01 00:00; 20-GT-2000B heat_rate 2025-06-01 00:00; OK-J12-02 choke_wear 2025-01-15 00:00; OK-J12-02 choke_wear 2025-01-15 00:00 |
|  |  | 2025-01-13 to 2025-01-15 |  | 4 (1) | 5 | 0 | 0% | 0% | $0.36 | no | 10-P-1001A head_loss 2025-01-01 00:00 |
|  |  | 2025-08-20 to 2025-08-22 |  | 4 (2) | 3 | 0 | 0% | 0% | $0.34 | no | 20-GT-2000B heat_rate 2025-06-01 00:00; OK-J12-02 choke_wear 2025-01-15 00:00 |
|  |  | 2025-09-08 to 2025-09-10 |  | 6 (1) | 4 | 0 | 0% | 0% | $0.34 | no | OK-J12-02 choke_wear 2025-01-15 00:00 |
|  |  | 2025-10-27 to 2025-10-29 |  | 6 (0) | 0 | 0 |  |  | $0.24 | no |  |
|  |  | 2025-11-10 to 2025-11-12 |  | 6 (0) | 7 | 0 | 14% |  | $0.39 | no |  |
| 2026-11-16 | kestrel | **all 5 windows** |  | 31 (8) | 6 | 3 | 50% | 25% | $0.31 | yes | C1-AT-3011 drift 2026-09-30 00:00; A1-V-1004 drift 2026-10-07 00:00; B1-K-2001 valve_leak 2026-10-26 00:00; C1-H-3008 drift 2026-10-01 00:00; B1-K-2001 valve_leak 2026-10-26 00:00; C1-H-3008 drift 2026-10-01 00:00; meter:sales bias 2026-11-04 00:00; meter:sales bias 2026-11-04 00:00 |
|  |  | 2026-10-11 to 2026-10-13 |  | 5 (2) | 1 | 0 | 0% | 0% | $0.05 | no | C1-AT-3011 drift 2026-09-30 00:00; A1-V-1004 drift 2026-10-07 00:00 |
|  |  | 2026-10-25 to 2026-10-27 |  | 4 (2) | 1 | 1 | 100% | 50% | $0.09 | yes | B1-K-2001 valve_leak 2026-10-26 00:00; C1-H-3008 drift 2026-10-01 00:00 |
|  |  | 2026-11-03 to 2026-11-05 |  | 5 (3) | 2 | 2 | 100% | 33% | $0.08 | yes | B1-K-2001 valve_leak 2026-10-26 00:00; C1-H-3008 drift 2026-10-01 00:00; meter:sales bias 2026-11-04 00:00 |
|  |  | 2026-11-09 to 2026-11-11 |  | 6 (1) | 1 | 0 | 0% | 0% | $0.07 | no | meter:sales bias 2026-11-04 00:00 |
|  |  | 2026-11-14 to 2026-11-16 |  | 11 (0) | 1 | 0 | 0% |  | $0.03 | no |  |
| 2026-11-16 | harrier | **all 5 windows** |  | 23 (11) | 14 | 2 | 36% | 18% | $0.44 | no | A1-P-1003B head_loss 2026-09-17 00:00; A1-F-1007 drift 2026-10-17 00:00; A1-P-1003B head_loss 2026-09-17 00:00; G1-K-7001 valve_leak 2026-10-29 00:00; A1-F-1007 drift 2026-10-17 00:00; A1-P-1003B head_loss 2026-09-17 00:00; A1-P-1003B head_loss 2026-09-17 00:00; meter:receipt bias 2026-11-06 00:00 |
|  |  | 2026-09-16 to 2026-09-18 |  | 1 (1) | 0 | 0 |  | 0% | $0.00 | no | A1-P-1003B head_loss 2026-09-17 00:00 |
|  |  | 2026-10-16 to 2026-10-18 |  | 2 (2) | 4 | 0 | 0% | 0% | $0.12 | no | A1-F-1007 drift 2026-10-17 00:00; A1-P-1003B head_loss 2026-09-17 00:00 |
|  |  | 2026-10-28 to 2026-10-30 |  | 3 (3) | 4 | 2 | 50% | 67% | $0.15 | yes | G1-K-7001 valve_leak 2026-10-29 00:00; A1-F-1007 drift 2026-10-17 00:00; A1-P-1003B head_loss 2026-09-17 00:00 |
|  |  | 2026-11-05 to 2026-11-07 |  | 6 (4) | 4 | 0 | 50% | 0% | $0.12 | no | A1-P-1003B head_loss 2026-09-17 00:00; meter:receipt bias 2026-11-06 00:00; E1-T-5001A level 2026-11-06 00:00; E1-T-5001C level 2026-11-07 00:00 |
|  |  | 2026-11-14 to 2026-11-16 |  | 11 (1) | 2 | 0 | 50% | 0% | $0.04 | no | meter:receipt bias 2026-11-06 00:00 |
| 2026-11-13 | okan | 2025-08-20 to 2025-08-22 |  | 3 (2) | 3 | 1 | 33% | 50% | $0.45 | no | OK-J12-02 choke_wear 2025-01-15 00:00; OK-NWP-G3 choke_wear 2025-08-01 00:00 |
| 2026-11-13 | okan | 2025-08-20 to 2025-08-22 |  | 3 (2) | 4 | 0 | 0% | 0% | $0.22 | no | OK-J12-02 choke_wear 2025-01-15 00:00; OK-NWP-G3 choke_wear 2025-08-01 00:00 |
| 2026-11-13 | okan | 2025-01-13 to 2025-01-15 |  | 4 (1) | 4 | 0 | 0% | 0% | $0.26 | no | 10-P-1001A head_loss 2025-01-01 00:00 |

## Slow-drift detector

The `peer_drifts` detector (P3.11a) replayed a day at a time over each bundle's stretch in `evals/drifts.yaml`, with `fuzer eval --drifts`: the slow upsets it raised, and what it raised on nothing the process model holds. A run passes with at most 5 false alarms a week and at least one slow upset raised.

| Run | Bundle | Stretch | Slow upsets raised | Recall | Raised | Precision | False alarms / week | Passed | Missed | False alarms |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-11-16 | harrier | 2026-09-06 to 2026-11-15 | 2 of 7 | 29% | 5 | 100% | 0.0 | yes | E1-PT-5003 2026-11-14 02:45; H1-FT-8005 2026-11-14 02:45; A1-F-1007 2026-10-16 00:00; G1-E-7003 2026-11-14 01:00; A1-P-1003B 2026-09-16 00:00 |  |
| 2026-11-16 | kestrel | 2026-09-16 to 2026-11-15 | 1 of 9 | 11% | 1 | 100% | 0.0 | yes | meter:sales 2026-11-03 00:00; H1-FT-8003 2026-11-14 03:10; D1-V-4001 2026-11-14 06:45; D1-AT-4002 2026-11-14 06:45; C1-H-3008 2026-09-30 00:00; C1-AT-3011 2026-09-29 00:00; B1-K-2001 2026-11-09 00:00; B1-K-2001 2026-10-25 00:00 |  |
| 2026-11-16 | okan | 2024-12-31 to 2025-10-01 | 4 of 7 | 57% | 95 | 30% | 1.6 | yes | OK-J12-02 2025-01-15 00:00; 20-GT-2000C 2025-10-01 00:00; OK-J10-01 2025-10-01 00:00 | OK_GGCP:20-PIC-3003.OP peer_drift_down 2025-01-13 00:00; 40FT3001.PV peer_drift_up 2025-01-14 00:00; OKA_PP.10FT3001.PV peer_drift_up 2025-01-14 00:00; 30FT3001.PV peer_drift_up 2025-01-14 00:00; 40FT3002.PV peer_drift_up 2025-01-14 00:00 |

## Message reader

Free-text messages to the IOC (P2.8: mail, radio calls, Teams posts, field notes) read with `fuzer eval --messages` against the bundle's `answer_keys/messages.json`, with no model: a case about the entity a message names, a question for the data steward, or nothing to do. Precision is the share of cases about the right entity; to steward, the share of messages no reading can place that reach the steward. A run passes with precision at least 90%.

| Run | Bundle | Messages | Cases right | Precision | Recall | To steward | Noise handled | Passed | Wrong |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-25 | okan | 23 | 10 of 11 | 91% | 91% | 100% | 100% | yes | MSG-0023: case tag:20-PT-3003, not case tag:30-PT-3003 |

## Operations loop

History replayed with the fake plant's injected upsets: SRE detection, grouping, the assistant's diagnosis, daily reports and opportunities.

| Run | Window | Recall | Precision | Upsets (process / instrument / slow / standing) | False upsets / week | First cause right | Diagnosed (named first cause) | Daily reports OK | Gas lift gain error |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-03 | 2024-06-10 to 2024-06-17 | 100% | 79% | 18 / 1 / 1 / 2 | 0.0 | 100% | none new | 100% | 0.04% |
| 2026-11-02 | 2024-06-10 to 2024-06-17 | 100% | 78% | 20 / 182 / 1 / 0 | 1.0 | 100% | 2 (100%) | 100% | 0.04% |

### Gates

A run fails when a score is under its floor or over its ceiling (`src/fuzer/evals/ops_eval.py`), or when recall fell since the last run of the same window.

| Score | Limit |
|---|---|
| `sre.recall` | at least 0.9 |
| `grouping.first_cause_right` | at least 0.9 |
| `daily_report.all_checks_ok` | at least 1 |
| `daily_report.trip_days_led_by_the_trip` | at least 1 |
| `diagnosis.first_cause_named` | at least 0.5 |
| `grouping.false_upsets_per_week` | at most 5 |
| `daily_report.with_operating_instructions` | at most 0 |
| `opportunities.worst_gain_error` | at most 0.02 |
