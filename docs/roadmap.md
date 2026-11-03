# Roadmap

What's planned. What's done is in the code and the closed issues.

## Roadmap

### Done (on fake data)

Core pipeline, review queue, REST API, web UI (browse, entity detail with graph,
ontology, flow network, review), MCP server, `medium` fake asset, Claude reading
operator logs and scanned P&IDs, snapshot ingest, Dagster jobs.

### Next

The roadmap is tracked in GitHub: one [milestone](https://github.com/areed145/fuzer/milestones) per phase and
one issue per item (label [`roadmap`](https://github.com/areed145/fuzer/issues?q=label%3Aroadmap)). Each issue
has its scope, a "done when" checklist and the issues it depends on. The
sections below explain the thinking behind the bigger items.

1. **[Prove it on real data](https://github.com/areed145/fuzer/milestone/1)**: real exports for one area (#9), tag
   grammar on real names (#10), Claude reader accuracy (#11), Azure (#12),
   Entra ID (#13), Dagster in Azure (#14).
2. **[Live process data](https://github.com/areed145/fuzer/milestone/2)**: PI Web API (#15), ADX (#16), process values
   around an event (#17).
3. **[Equipment models & maintenance](https://github.com/areed145/fuzer/milestone/3)**: equipment models and classes,
   ISO 14224 (#18); SAP notifications and work orders (#19). Models come first
   because a manual describes a model, not one pump.
4. **[Operational history & conversations](https://github.com/areed145/fuzer/milestone/4)**: EC DAY001 daily reports
   (#20), EC downtime and deferment (#21), Teams threads (#22), incidents (#23),
   lessons learned (#24).
5. **[Documents & knowledge](https://github.com/areed145/fuzer/milestone/5)**: ingest (#25), linking (#26), scoped
   search (#27), revisions (#28), group-based access (#29), quality (#30).
6. **[Troubleshooting assistant](https://github.com/areed145/fuzer/milestone/6)**: #31.
7. **[Later](https://github.com/areed145/fuzer/milestone/7)**: P&ID flow connectivity (#32), source record history
   (#33), spares and BOM (#34), onshore asset pack (#35), Splink matcher (#36),
   graph DB read replica (#37).
8. **[Source framework](https://github.com/areed145/fuzer/milestone/8)** (section 20): connector contract and
   lake-table connector (#57), mapping files and generic per-shape extractors
   (#58), schema profiling and suggested mappings (#59), geospatial shape and
   PostGIS (#60), pipeline stationing and well depths (#61), measurements (#62),
   DEXPI and CFIHOS handover readers (#63).

### Operational history and conversations

Goal: the record of what actually happened on the asset, and how people fixed
it, is linked to the equipment and wells involved and can be searched next to
the manuals.

- **DAY001 daily reports (EC)**: one per day: production and injection figures,
  downtime, and free-text comments. Figures stay in EC / ADX (Fuzer keeps a
  pointer); comments are read by Claude like operator logs, into links and
  events.
- **Downtime and deferment history (EC)**: structured records (start, end, well
  or facility, reason code, lost volume) plus comments. Each becomes a
  `downtime` event linked to its wells and, from the comment, to the equipment
  that caused it. Lost volume on each event makes it possible to rank problems
  by cost, and to check reason codes against what the comment actually says.
- **Teams channels (Microsoft Graph)**: only channels the asset team chooses
  (e.g. operations troubleshooting, upset recovery). A thread (first message +
  replies) is the unit: Claude links it to tags, wells and incidents, and
  summarises what the problem was, what was tried and what fixed it. Fuzer stores
  the link back to the Teams message and the summary, not a second copy of
  everyone's chat.
- **Lessons learned**: those summaries become *suggested* troubleshooting
  steps for a model or tag. An engineer reviews them before they appear next to
  the official guides, so tribal knowledge turns into reviewed knowledge.
- **Care needed with chat**: Graph's channel-message permissions need tenant
  admin consent; people's names and anything personal should be minimised or
  redacted; retention and legal-hold rules apply; and access should follow
  channel membership. Worth agreeing with IT, legal and the asset team before
  building.

### Documents and knowledge

Goal: every manual, guide and procedure is linked to the entities it applies
to, and searchable in the context of an entity ("how do I reset the seal
flush on 20-P-1001B?").

- **Types**: OEM manuals and datasheets, site troubleshooting guides, operating
  procedures and work instructions, inspection reports and certificates, MOCs.
- **Ingest**: PDFs and Office files from a document system (SharePoint / an
  EDMS), with document number, revision, status and effective date. Text layer
  first, OCR (Azure Document Intelligence) for scans. Split into sections that
  keep their headings, page numbers and figures.
- **Link at the right level**: an OEM manual applies to an equipment *model*
  (and through it to every pump of that model); a site guide applies to tags,
  systems or areas; a procedure may apply to an equipment *class*. Claude reads
  each document's front matter and sections, and picks from register candidates
  (models, tags, classes) the way it does for logs, with low-confidence links
  sent to the review queue.
- **One search across knowledge**: the same scoped search covers manuals,
  guides, reviewed lessons learned, Teams thread summaries and past incidents,
  each labelled by how authoritative it is (OEM manual vs. a chat thread).
- **Section-level links**: sections are linked to the components, failure
  modes and tags they discuss, so search can return the right page, not just the
  right document.
- **Search**: full-text plus embeddings (pgvector) over sections, always scoped
  by the graph (the entity, its model, its class, its area), returning cited
  passages with page numbers. Exposed in the UI (a Documents tab per entity and a
  viewer with highlighted passages) and as an MCP tool.
- **Revisions**: superseded revisions are kept but not served by default;
  links follow the current revision.
- **Access**: manuals can be licensed or confidential, so this is where role- or
  group-based access (currently "everyone sees everything") would start.
- **Quality**: answer-key sets for document linking and for search, measured
  like the P&ID reader; reviewer feedback on links and search results.

## What the first slice includes

- Fake data generator: a messy offshore asset across all sources.
- Tag and well grammars, phrase aliases.
- Ingest → resolve → golden → graph pipeline on Postgres.
- Review queue with pinned decisions.
- REST API: search, entity detail, lookup by source key or free text, dated
  flow tracing, review.

Since added: React web UI (browse, entity detail with graph tab, ontology,
flow network, review); a read-only MCP server for AI agents; a `medium` fake
asset (~1/10 real size) with COPY-based rebuilds (~4 s) and background
rebuilds after review answers; Claude reading operator logs into links and
`event` entities, and reading tags off scanned P&IDs (tiled images, grammar +
register check, findings for misreads and missing tags), all cached and never
called during rebuilds; snapshot ingest that retires entities whose records
disappear; Dagster jobs (per-source assets, export-folder sensor, hourly /
daily schedules, opt-in nightly Claude run, data-quality checks, failure
webhook).

Not yet: Entra ID login, Azure deployment.
