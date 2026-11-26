---
name: options-and-tradeoffs
description: How to weigh the options on a case - dispatch now, ride along with the next visit, shelve, escalate, keep on watch - on one scale every time, recommend one with the factor that decided it, and name what is not known instead of guessing. Load whenever there is a choice to make on a case.
---

# Options and trade-offs

Every specialist and the orchestrator weigh a choice on a case the same way, so the person deciding gets one
answer whoever speaks.

**Read the options, don't build them.** Call `case_options(case)`. It puts each option on one scale: `hours` and
`by` (when it is done), `deferred` (bbl, Mscf and $ deferred until then), `risk` (the risk of waiting, from its
assets' criticality or a hazard on record), `ready` and `blockers` (crew, permit, parts), `clashes` and `unknown`.
Quote those figures as given, with their units and `basis`. Never work out a deferral, a risk level or a readiness
of your own, and never weigh barrels against risk on a scale of your own.

**Recommend one.** Lead with the option's label, then one line naming the factor that decided it: the tool's
`recommended`, `reason` and `deciding` (deferred, risk, readiness, or brief when the specialist brief proposes keeping it on watch). If your own evidence points to another
option, lead with that one, name the recommended one beside it, and say which factor you weigh differently and
why, citing the record the tool does not see. Without such a record, recommend what the tool recommends. When `recommended` is empty, say its `reason`: the
blocker is the reader's to clear, and no option is recommended. Never recommend Shelve: it leaves the work undone,
and the tool never recommends it either. A person may still choose it.

**Name what is not known.** Say each line of `unknown` in your answer, by its words before the colon
(criticality, what it defers) and why it is not known. A risk the tool gives no level for is not known: say that,
never low. A closed option (`available` false) is out, with its `unavailable` reason.

**When there are no options.** `applies` false: say `why_not` and stop. The person decides: they take an option on
the case.
