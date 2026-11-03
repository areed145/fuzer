# Glossary

**Asset pack.** What makes Fuzer fit one kind of asset (offshore, onshore, ...): tag grammar, entity kinds, source
priorities and limits. See [overview](overview.md).

**Assertion.** One source's value for one field of one entity. Golden records are chosen from assertions.

**Board.** One ranked list of what needs attention: upsets, work items, opportunities and reviews, by value.

**Candidate.** What a source record says exists (a tag, a well, a pump), before identity resolution decides which
entity it is.

**Capability.** A tool an agent can call, declared once with the agents that use it. See [capabilities](capabilities.md).

**Connector.** Reads one source table in one format (CSV, Parquet, Delta, Excel, JSON, a live system).

**Detection.** One thing the SRE found in the process data: an excursion, a stop, a step, a drift, a bad instrument.

**Entity.** One real-world thing (a tag, a pump, a well, a work order) with a stable Fuzer ID (`fz-...`), however
many sources describe it.

**Equipment.** The physical item installed at a tag, known by its serial number. Pumps get swapped; tags stay.

**Excursion.** A process value outside its alarm limits, integrity operating window or learned normal range while
its unit runs.

**Golden record.** An entity's best value for each field, chosen from its assertions by source priority.

**Instrument-health summary.** A week's bad instruments in one upset of kind `instrument`, listed by area.

**IOW.** Integrity operating window: a limit that protects equipment integrity rather than the process.

**Mapping.** A YAML file that says what one source table's rows mean: entities, fields, links and measurements.

**MCP.** Model Context Protocol: how Claude, in the app or in another client, calls Fuzer's tools.

**Project.** One asset's configuration: its pack, data folder, mappings, timezone and time-series source.

**Replay.** Running the detectors over history one window at a time, without letting them see later data, and
scoring them against what really happened. See [evals](evals.md).

**Review item.** A question for a person: an uncertain match, a held source, a reading by Claude to check.

**Setting.** A configurable value (a threshold, a model, a schedule) stored in the database, global or per
project. See [settings](settings.md).

**SRE.** The scheduled loop that watches the process data: detect, group into upsets, diagnose, hand off.

**Standing.** Alarms already on when scanning began. They join an upset that starts near them but never open one.

**Tag.** A functional position in the plant, such as `20-P-1001B`. Equipment is installed at a tag.

**Upset.** Detections close in time and connected in the plant model, grouped into one. The earliest is its likely
first cause.

**Work item.** A step in a workflow process, assigned to an agent, a person or a role's queue.
