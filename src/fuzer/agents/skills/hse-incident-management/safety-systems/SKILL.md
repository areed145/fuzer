---
name: safety-systems
description: Safety systems and relief - whether each trip, interlock and shutdown is in service, proof tested within its interval and demanded no more than designed, and which equipment relieved into the flare during an episode, for any plant with a safeguard register (ISO 14224 classes VA, IP, FG).
---

# Safety systems and relief

This is the safety systems and relief specialist pack: the playbook below, and `pack.yaml` beside it naming the tools,
methods, detectors, charts and measurement roles it uses and the eval it is held to. The practice is IEC 61511 for
safety instrumented functions (operation and maintenance, clause 16), API 520 and 521 for relief and flare, and
API 14C for the safety analysis of production facilities; each answer names the basis it used. A safeguard gives the
protection it claims only while it is in service, tested and demanded as often as its risk assessment assumed: check
all three before saying it holds.

**Start here.** For "is this item protected" or "what is wrong with our safeguards", run `safeguard_status(ref)` on the
item (it widens to the rest of its train by the piping), or with no ref for the whole plant. Quote each safeguard by
its register id and function, with what it protects.

- **In service** (`bypasses`): a bypass in place takes the protection away. One past its expiry is overdue: quote how
  long, who authorised it and its change record, or that there is none. Several bypasses on one train add up: say so.
- **Tested** (`proof_test`, `proof_tests_overdue`): each safeguard's last proof test against the interval on the
  register (IEC 61511-1 16.3). Overdue means the protection is not shown to work; no test on file counts as overdue.
  Quote the interval, the last test and how many days past due.
- **Demanded as designed** (`demand_rate`, `demands`): the last year of the site's demand log against the demand rate
  the register says it was designed for, or the low demand boundary when it gives none. More real demands than that,
  with at least two, means the initiating event is more frequent than the risk assessment assumed: find what keeps
  creating the demand. A failed demand is the most serious finding here: say it first. A spurious trip is not a
  demand, but repeated ones are a reliability problem with the instrument and a reason people bypass it.
- **Changes**: open change records on the item. A safeguard changed or bypassed without one is a management of change
  gap.

**Where flare gas came from** (`flare_relief`): `flare_attribution()` gives each flare episode with the records around
it and, for a flare flow point, the item whose pressure rose ahead of it and the relief and vent devices it relieved
through (from the piping into the flare system). `plot_flare_attribution()` shows the episodes. The relieving item
says where, not why: the downtime, log entries and detections around the episode are the why. A relief valve that
lifted needs its inspection under API 520 before it is trusted again: say so when one is named.

**Already in the queue.** The `bypass_overdue`, `proof_test_overdue` and `safeguard_demands` detectors raise these as
work, and `flare_events` raises each episode. Say when the safeguard you are asked about already has one.

**Say what you cannot check.** Fuzer holds no safety requirements specification, PFD calculation, SIL verification,
proof test procedure or relief valve sizing: a demand rate is read against the rate on the register, nothing more.
Name them as the process safety engineer's next step.

**Escalate to the process safety engineer** when a safeguard failed on a demand, is demanded more than designed, or
is both bypassed and overdue for its test. A good answer names the safeguard, what it protects, which of the three
checks it fails with its numbers and dates, and the recommendation (put it back in service, test it, or review the
risk assessment behind it). Advises: removing a bypass, extending a test interval, changing a trip setting and any
change to the safety instrumented system are the accountable engineer's decision under management of change.
