# Glossary

**Asset pack.** What makes Fuzer fit one kind of asset (offshore, onshore, ...): tag grammar, entity kinds, limits,
and the source priorities and match thresholds that seed the source catalog's rules. See [overview](overview.md).

**Assertion.** One source's value for one field of one entity (re-read from its record; a reviewer's choice is
stored). Golden records are chosen from assertions.

**Board.** One ranked list of what needs attention: upsets, work items, opportunities and reviews, by value.

**Candidate.** What a source record says exists (a tag, a well, a pump), before identity resolution decides which
entity it is.

**Capability.** A tool an agent can call, declared once with the agents that use it. See [capabilities](capabilities.md).

**Connection.** How Fuzer reaches a system of record: a folder of exports, PI Web API or ADX, with secrets as
references. A system can have several. See [sources](sources.md).

**Connector.** Reads one source table in one format (CSV, Parquet, Delta, Excel, JSON, GeoJSON, a live system).

**Dataset.** One table or feed a system delivers: where it is, its key, how it loads (snapshot, incremental,
append), its schedule, owner and status (draft, active, paused, retired).

**Detection.** One thing the SRE found in the process data: an excursion, a stop, a step, a drift, a bad instrument.

**Entity.** One real-world thing (a tag, a pump, a well, a work order) with a stable Fuzer ID (`fz-...`), however
many sources describe it.

**Equipment.** The physical item installed at a tag, known by its serial number. Pumps get swapped; tags stay.

**Excursion.** A process value outside its alarm limits, integrity operating window or learned normal range while
its unit runs.

**Golden record.** An entity's best value for each field, chosen from its assertions by source priority (the
systems' rank, or the field's own rule).

**Instrument-health summary.** A week's bad instruments in one upset of kind `instrument`, listed by area.

**IOW.** Integrity operating window: a limit that protects equipment integrity rather than the process.

**Mapping.** YAML that says what one source table's rows mean: entities, fields, links and measurements. Kept as
versions in the source catalog (the latest approved one is in use); the files in `src/fuzer/mappings/` seed it.

**MCP.** Model Context Protocol: how Claude, in the app or in another client, calls Fuzer's tools.

**Profile.** What a load of a dataset looked like: per column its type, fill, distinct and top values. Compared
with the one before to catch a source changing shape.

**Project.** One asset's configuration: its pack, data folder, timezone and time-series source.

**Replay.** Running the detectors over history one window at a time, without letting them see later data, and
scoring them against what really happened. See [evals](evals.md).

**Review item.** A question for a person: an uncertain match, a held source (a changed schema or a failing
check), a suggested mapping, a reading by Claude to check.

**Run.** One load of a dataset that changed something: when, what triggered it, rows read, new, changed and
removed, and the mapping versions used.

**Source catalog.** The systems of record, their connections, datasets, mapping versions, runs, profiles,
checks and rules, as rows edited on the Sources page with history. See [sources](sources.md).

**Setting.** A configurable value (a threshold, a model, a schedule) stored in the database, global or per
project. See [settings](settings.md).

**SRE.** The scheduled loop that watches the process data: detect, group into upsets, diagnose, hand off.

**Standing.** Alarms already on when scanning began. They join an upset that starts near them but never open one.

**Tag.** A functional position in the plant, such as `20-P-1001B`. Equipment is installed at a tag.

**Upset.** Detections close in time and connected in the plant model, grouped into one. The earliest is its likely
first cause.

**Work item.** A step in a workflow process, assigned to an agent, a person or a role's queue.
