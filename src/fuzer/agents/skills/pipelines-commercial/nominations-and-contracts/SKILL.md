---
name: nominations-and-contracts
description: Read the customer and contract register (shippers, delivery points, daily volumes, rates and dates) against the flow measured at each delivery point, and what an outage costs each customer in barrels and dollars.
---

# Nominations and contracts

**Start here.** `customer_contracts()` lists each contract: its customer (a shipper or a utility), what it buys, the
delivery point it receives or delivers at, its daily volume (bbl/d), its rate ($/bbl) and the dates it runs. Quote a
contract by its key with its customer; a contract outside its dates is not running and loses nothing.

- **Against actual flow**: no nominations are loaded, so each contract's daily volume stands in for its nomination,
  and the answer says so. For "are we moving what the shippers contracted", add the daily volumes of the contracts
  running at each delivery point and set them beside the flow at that point's meter over the same days, read with
  `plot_trend([ref])` or `measurements(ref)`; `pipeline_segments(line)` names the meters at each end of the line.
  Quote both in bbl/d, the gap and the window. A meter that has drifted (a metering question for Production &
  Surveillance) makes the gap its error, not the shippers'.
- **What an outage costs**: `outage_impact(days)` gives each outage on the plan with each contract it hits (its
  delivery point down or left with no feed while the contract runs), the hours, barrels and dollars each loses, and
  by customer what each loses over the window, a customer hit at more than one separate time flagged. Quote the
  customer, the contracts, the barrels and the dollars with the window.

**Say what you cannot check.** No nominations, shipper balances, tariffs, invoices or penalty terms are loaded: a
loss is barrels at the contract's own rate, not a settled amount. Name the commercial system as what would settle it.

**Advises.** A contract's terms, a curtailment, a nomination change and what a customer is told are the commercial
lead's and the scheduler's decisions.
