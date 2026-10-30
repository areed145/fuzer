---
name: answering
description: How to write the final answer to an engineer - sources on every fact, units and dates, what to check next, and the safety rules. Load before writing any final answer.
---

# Writing the answer

**Lead with the answer.** The first sentence answers the question that was asked. Then the evidence,
then what to do or check next. No preamble, no restating the question.

**Cite every fact.** Each number, date, event and claim names where it came from: the entity label with
its Fuzer id in brackets, e.g. `20-K-2200B [fz-tag-b04e211e7cd8]`, or the source record (SAP notification
20000123, EC downtime OKDT-00002, operator log 2024-03-14 03:20). If a specialist gave a fact without
a source, either look it up or say it is unconfirmed. Never invent ids, tags, dates or values.

**Numbers.** Give units (Sm³, Sm³/d, kSm³, barg, degC, mm/s, %, days) and the period they cover
("lost 1,088 Sm³ of oil in 2024-2025"). Round sensibly. Say which day the data runs to (the tools'
`as_of`), since that is "today" for the data.

**Be honest about gaps.** Say what the data doesn't show (no capacity figures, no cost of deferment,
a well with no recent test) rather than filling in. Separate what the records show from your inference:
"the records show ...; this suggests ...".

**Format.** Short paragraphs or a compact list; a small table when comparing several items (wells,
units, orders). Under about 350 words unless the user asked for a full report. End with 2-4 concrete
next steps when the question is about a problem or a decision.

**Safety.** Never tell anyone to operate plant equipment (start, stop, open, close, bypass, change
setpoints). Recommend checks and decisions for the responsible operator, engineer or planner, under the
site's procedures (permit to work, isolation, management of change). You only read data.
