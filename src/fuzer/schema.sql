-- Which schema.sql this database is at (its hash): init_schema re-runs this file only when it changed, since its
-- migrations take exclusive locks on busy tables.
create table if not exists schema_version (
    id         int primary key check (id = 1),
    hash       text not null,
    applied_at timestamptz not null default now()
);

-- Raw records exactly as received from each source.
create table if not exists source_record (
    source      text not null,
    record_type text not null,
    source_key  text not null,
    payload     jsonb not null,
    first_seen  timestamptz not null default now(),
    last_seen   timestamptz not null default now(),
    primary key (source, record_type, source_key)
);

-- One row per Fuzer ID. IDs are never reused; merged IDs point at the winner.
create table if not exists entity (
    fuzer_id    text primary key,
    kind        text not null,
    created_at  timestamptz not null default now(),
    merged_into text references entity (fuzer_id)
);

-- Set when no source record maps to an entity any more (the ID is kept and revived if one returns).
alter table entity add column if not exists retired_at timestamptz;

create table if not exists id_event (
    id       bigserial primary key,
    event    text not null check (event in ('merge', 'split')),
    fuzer_id text not null,
    other_id text,
    at       timestamptz not null default now()
);

-- Source record -> Fuzer ID.
create table if not exists crosswalk (
    source      text not null,
    record_type text not null,
    source_key  text not null,
    fuzer_id    text not null references entity (fuzer_id),
    method      text not null,
    confidence  real not null,
    updated_at  timestamptz not null default now(),
    primary key (source, record_type, source_key)
);
create index if not exists crosswalk_fuzer_id on crosswalk (fuzer_id);

-- Canonical keys (tag key, serial, well key, ...) -> Fuzer ID.
create table if not exists entity_key (
    kind     text not null,
    key      text not null,
    fuzer_id text not null,
    primary key (kind, key, fuzer_id)
);
create index if not exists entity_key_fuzer_id on entity_key (fuzer_id);

-- One display label per entity, kept by every rebuild (a table, not a view: computing it from
-- entity_key on every query cost seconds at 300k entities).
do $$
begin
    if exists (select 1 from pg_views where viewname = 'entity_label') then
        drop view entity_label;
    end if;
end
$$;
create table if not exists entity_label (
    fuzer_id text primary key,
    label    text not null
);
create index if not exists entity_label_label on entity_label (label);

-- Summaries computed once per rebuild (e.g. the ontology), read many times.
create table if not exists register_summary (
    name       text primary key,
    value      jsonb not null,
    updated_at timestamptz not null default now()
);

-- Reviewer choices (source = 'review'). What each source says is re-derived from its records on demand.
create table if not exists assertion (
    id          bigserial primary key,
    fuzer_id    text not null,
    field       text not null,
    value       jsonb not null,
    source      text not null,
    source_key  text,
    observed_at timestamptz not null default now()
);
create index if not exists assertion_fuzer_id on assertion (fuzer_id, field);

create table if not exists golden (
    fuzer_id text not null,
    field    text not null,
    value    jsonb not null,
    source   text not null,
    rule     text not null,
    primary key (fuzer_id, field)
);

create table if not exists edge (
    id         bigserial primary key,
    src        text not null,
    rel        text not null,
    dst        text not null,
    valid_from date,
    valid_to   date,
    sources    text[] not null,
    confidence real not null,
    props      jsonb,
    unique nulls not distinct (src, rel, dst, valid_from)
);
create index if not exists edge_src on edge (src, rel);
create index if not exists edge_dst on edge (dst, rel);

create table if not exists review_item (
    id         bigserial primary key,
    kind       text not null,  -- match, conflict, schema
    subject    text not null,
    payload    jsonb not null,
    status     text not null default 'open' check (status in ('open', 'decided', 'stale')),
    decision   jsonb,
    decided_by text,
    decided_at timestamptz,
    created_at timestamptz not null default now(),
    unique (kind, subject)
);

-- Reviewer match decisions; applied on every rerun.
create table if not exists pinned_link (
    left_ref   text not null,
    right_ref  text not null,
    rule       text not null check (rule in ('must', 'cannot')),
    decided_by text,
    decided_at timestamptz not null default now(),
    primary key (left_ref, right_ref)
);

-- Fast "contains" search over values, keys and source keys when pg_trgm is available
-- (Docker / Azure Postgres). Search still works without it, just slower.
do $$
begin
    if exists (select 1 from pg_available_extensions where name = 'pg_trgm') then
        create extension if not exists pg_trgm;
        -- Rebuilds sync golden (only changed rows touch the index), so a full index is cheap to keep.
        drop index if exists golden_value_trgm;
        drop index if exists golden_search_trgm;
        create index if not exists golden_text_trgm on golden using gin ((value::text) gin_trgm_ops);
        create index if not exists entity_key_trgm on entity_key using gin (key gin_trgm_ops);
        create index if not exists entity_label_trgm on entity_label using gin (label gin_trgm_ops);
        create index if not exists crosswalk_key_trgm on crosswalk using gin (source_key gin_trgm_ops);
    end if;
end
$$;

-- Cached Claude reads of free text (operator logs). Rebuilds use these and never call the model;
-- an entry is re-read only when its text changes (or on request).
create table if not exists llm_extraction (
    source         text not null,
    source_key     text not null,
    text_hash      text not null,
    model          text not null,
    prompt_version text not null,
    result         jsonb not null,
    created_at     timestamptz not null default now(),
    primary key (source, source_key)
);

-- These tables are rebuilt in full by every rebuild, which guarantees their IDs exist; per-row
-- foreign-key checks on hundreds of thousands of rows cost more than they protect.
alter table assertion drop constraint if exists assertion_fuzer_id_fkey;
alter table golden drop constraint if exists golden_fuzer_id_fkey;
alter table edge drop constraint if exists edge_src_fkey;
alter table edge drop constraint if exists edge_dst_fkey;
alter table entity_key drop constraint if exists entity_key_fuzer_id_fkey;
-- Older versions also stored every source value here; only reviewer choices are kept now.
delete from assertion where source <> 'review';

-- What the knowledge search reads: document sections and incidents, with the IDs each is linked to
-- (what its document applies to, what it mentions, its unit) for scoping. Kept by every rebuild.
create table if not exists knowledge_index (
    fuzer_id  text primary key,
    kind      text not null,
    document  text,
    title     text,
    doc_type  text,
    status    text,
    heading   text,
    page      int,
    body      text not null,
    scope_ids text[] not null default '{}',
    tsv       tsvector generated always as (
        setweight(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(heading, '')), 'A')
        || setweight(to_tsvector('english', body), 'B')) stored
);
create index if not exists knowledge_tsv on knowledge_index using gin (tsv);
create index if not exists knowledge_scope on knowledge_index using gin (scope_ids);
alter table knowledge_index add column if not exists failure_modes text[] not null default '{}';
create index if not exists knowledge_modes on knowledge_index using gin (failure_modes);

-- Earlier versions of source records: written by ingest when a record changes or disappears.
create table if not exists source_record_history (
    id          bigserial primary key,
    source      text not null,
    record_type text not null,
    source_key  text not null,
    payload     jsonb not null,
    first_seen  timestamptz not null,
    replaced_at timestamptz not null default now(),
    change      text not null check (change in ('updated', 'removed'))
);
create index if not exists source_record_history_key on source_record_history (source, record_type, source_key, replaced_at);

-- Lessons learned: drafted from incidents and their corrective work orders, owned by engineers once
-- reviewed. Rebuilds refresh a draft's evidence and numbers, never reviewed wording or decisions.
create table if not exists lesson (
    id          bigserial primary key,
    scope_kind  text not null,   -- equipment_model or equipment_class
    scope_key   text not null,
    failure_mode text not null,
    title       text not null,
    draft       text not null,   -- suggested steps, regenerated
    text        text,            -- the engineer's wording once edited / approved
    evidence    jsonb not null default '[]',
    stats       jsonb not null default '{}',
    status      text not null default 'suggested' check (status in ('suggested', 'approved', 'rejected')),
    decided_by  text,
    decided_at  timestamptz,
    updated_at  timestamptz not null default now(),
    unique (scope_kind, scope_key, failure_mode)
);

-- Engineers' own ordering of a sample of work (fuzer ids, most valuable first), to calibrate the value
-- score (#100) against: value.calibrate reports how well the score's ranking agrees.
create table if not exists value_ranking (
    id         bigserial primary key,
    items      jsonb not null,
    by_user    text not null default 'anonymous',
    comment    text,
    created_at timestamptz not null default now()
);

-- Engineers' ratings of what Fuzer suggests (briefs, reading, lessons), to measure and improve it.
create table if not exists feedback (
    id         bigserial primary key,
    target     text not null,          -- what was rated, e.g. brief:<fuzer_id> or reading:<fuzer_id>
    useful     boolean not null,
    comment    text,
    by_user    text not null default 'anonymous',
    created_at timestamptz not null default now()
);

alter table review_item drop constraint if exists review_item_kind_check;

-- What each dataset looked like when last loaded: an unchanged fingerprint skips it, a changed schema
-- is compared column by column, and position is the watermark for append-only lake tables.
create table if not exists dataset_state (
    dataset     text primary key,
    connector   text not null,
    fingerprint text not null,
    schema      jsonb,
    position    jsonb,
    rows        bigint not null default 0,
    loaded_at   timestamptz not null default now()
);

-- Mappings approved in the review queue (suggested for a new table, as is or edited). The latest
-- version per source and record type is used, after the built-in files.
create table if not exists mapping_version (
    id          bigserial primary key,
    source      text not null,
    record_type text not null,
    dataset     text,
    yaml        text not null,
    approved_by text,
    approved_at timestamptz not null default now()
);
create index if not exists mapping_version_type on mapping_version (source, record_type, id desc);

-- Where things are: geometry of every entity that has one (GIS, latitude / longitude), with its
-- bounding box and centre for map and nearby lookups. Rebuilt with the register.
create table if not exists geo_index (
    fuzer_id  text primary key,
    kind      text not null,
    geom_type text not null,
    minx      double precision not null,
    miny      double precision not null,
    maxx      double precision not null,
    maxy      double precision not null,
    cx        double precision not null,
    cy        double precision not null,
    geometry  jsonb not null
);
create index if not exists geo_index_box on geo_index (minx, maxx, miny, maxy);
create index if not exists geo_index_kind on geo_index (kind);

-- Questions to the operations assistant and its answers, kept so engineers can rate them (feedback
-- target answer:<id>) and the answers can be reviewed against the data later.
create table if not exists assistant_answer (
    id              text primary key,
    conversation_id text not null,
    question        text not null,
    answer          text,
    error           text,
    specialists     text[] not null default '{}',
    tools           jsonb not null default '[]',
    usage           jsonb not null default '{}',
    seconds         numeric,
    by_user         text not null default 'anonymous',
    created_at      timestamptz not null default now()
);

-- Sparse, manual measurements landed from sources (well tests, lab samples, inspection thickness
-- readings), unlike historian values, which stay in PI / ADX. Rebuilt from source records with the
-- graph; values are in the units of units.yaml.
create table if not exists measurement (
    record     text not null,              -- source record: <source>:<record_type>:<key>
    quantity   text not null,
    fuzer_id   text not null,              -- what was measured
    at         timestamp not null,
    value      double precision not null,
    unit       text,
    qualifier  text,                       -- "<" / ">": beyond a detection limit
    reported   text,                       -- as the source gave it, when converted
    method     text,
    location   text,                       -- where on it: a CML, a sample point
    primary key (record, quantity)
);
create index if not exists measurement_of on measurement (fuzer_id, quantity, at);

-- Operating limits per PI point (#101): alarm setpoints (PI AF limit traits, DCS alarm exports) and
-- integrity operating windows (IOW registers), dated, with the source record they came from. Rebuilt
-- from source records with the graph; values are in the units of units.yaml.
create table if not exists point_limit (
    record          text not null,              -- source record: <source>:<record_type>:<key>[#n]
    limit_type      text not null,              -- hihi | hi | lo | lolo
    point_id        text not null,              -- the timeseries entity
    source          text not null,              -- pi_af | dcs | iow | ...
    basis           text not null,              -- alarm | iow
    value           double precision not null,
    unit            text,
    reported        text,                       -- as the source gave it, when converted
    level           text,                       -- IOW class (critical / standard / informational) or alarm priority
    allowed_minutes double precision,           -- IOW: time allowed outside before it must be acted on
    enabled         boolean not null default true,
    valid_from      timestamp,
    valid_to        timestamp,
    note            text,
    primary key (record, limit_type)
);
create index if not exists point_limit_of on point_limit (point_id);

-- Normal ranges learned from history (#101) per point and operating mode of its unit: used for points
-- with no limits from a source, and to check the ones that have them. Written by `fuzer limits learn`.
create table if not exists point_normal (
    point_id    text not null,
    mode        text not null,                  -- running | startup | stopped | transition | any
    p01         double precision not null,
    p50         double precision not null,
    p99         double precision not null,
    n           integer not null,
    share       double precision not null,      -- of the learning window spent in this mode
    since       timestamp not null,
    until       timestamp not null,
    learned_at  timestamptz not null default now(),
    primary key (point_id, mode)
);

-- Times a point was outside a limit while its unit was running (#101): what the SRE (#96) raises, and
-- for IOWs past their allowed time, the integrity work item opened for them (#97).
create table if not exists excursion (
    id               bigserial primary key,
    point_id         text not null,
    record           text not null,             -- the limit's record ('learned' for learned ranges)
    limit_type       text not null,
    basis            text not null,             -- alarm | iow | learned
    level            text,
    limit_value      double precision not null,
    start            timestamp not null,
    "end"            timestamp,                 -- null while still outside at the end of the scan
    peak             double precision not null,
    minutes_outside  double precision not null,
    allowed_minutes  double precision,
    work_item_id     bigint,
    found_at         timestamptz not null default now(),
    unique (point_id, record, limit_type, start)
);
create index if not exists excursion_open on excursion (start desc);

-- Replays of the operations detectors against a fake plant's injected events (#103, replay.py): one row
-- per `fuzer eval --ops`, so a change can be compared with the run before it.
create table if not exists ops_eval (
    id        bigserial primary key,
    detector  text not null,
    since     text not null,
    until     text not null,
    scores    jsonb not null,
    run_at    timestamptz not null default now()
);

-- Charts the operations assistant made (plots/): the Plotly figure as built, what the agent was told
-- about it, and the records it came from. A figure is a snapshot of the data when it was asked.
create table if not exists assistant_figure (
    id          text primary key,
    kind        text not null,
    title       text not null,
    params      jsonb not null default '{}',
    spec        jsonb not null,
    summary     jsonb not null default '{}',
    sources     text[] not null default '{}',
    by_source   text not null default 'fuzer',  -- 'assistant' when the agent supplied the numbers
    created_at  timestamptz not null default now()
);

-- Reference curves per equipment model (OEM pump curves, choke characteristics): from vendor CSV
-- exports, and from datasheets Claude read once an engineer accepted them (curve_digitised). Rebuilt
-- with the graph.
create table if not exists equipment_curve (
    model_id  text not null,              -- equipment_model entity
    source    text not null,              -- 'oem' (vendor export) or 'datasheet:<doc number>'
    curve     text,                       -- pump | choke
    series    text not null,              -- head, efficiency, npshr, power, cv, or a rating (bep, por, cv_max, ...)
    x         double precision not null,
    y         double precision,
    x_unit    text,
    y_unit    text,
    record    text,
    primary key (model_id, source, series, x)
);

-- Curves Claude read off datasheets, accepted in the review queue; the rebuild lands them in equipment_curve.
create table if not exists curve_digitised (
    document_key text primary key,        -- the datasheet's source record key
    doc_number   text not null,
    model_key    text not null,           -- MANUFACTURER|MODEL
    result       jsonb not null,          -- {curve, series: {name: [[x, y]]}, ratings: {name: [x, y]}, units}
    accepted_by  text not null,
    accepted_at  timestamptz not null default now()
);

-- Work engine (#97): BPMN definitions with versions, running processes, their steps (tokens), the work
-- items people and agents see, and an append-only history of everything that happened to them.
create table if not exists work_definition (
    id           bigserial primary key,
    process_key  text not null,                 -- the bpmn:process id
    version      int not null,
    name         text,
    xml          text not null,                 -- the whole .bpmn file
    state        text not null default 'draft', -- draft | checked | published | superseded
    problems     jsonb not null default '[]',
    created_by   text not null,
    created_at   timestamptz not null default now(),
    published_by text,
    published_at timestamptz,
    unique (process_key, version)
);
create index if not exists work_definition_published on work_definition (process_key, version desc) where state = 'published';

-- form-js task forms, by the key a userTask names in fuzer:form, with versions. A task shows the latest.
create table if not exists work_form (
    key         text not null,
    version     int not null,
    schema      jsonb not null,                 -- the form-js schema (plus `fuzer.bulk`: bulk actions)
    created_by  text not null,
    created_at  timestamptz not null default now(),
    primary key (key, version)
);

-- People by name and the roles they hold (everyone sees everything; Entra groups later).
create table if not exists work_person (
    name  text primary key,
    roles text[] not null default '{}'
);

create table if not exists work_item (
    id          bigserial primary key,
    type        text not null,
    title       text not null,
    subjects    text[] not null default '{}',   -- fuzer ids the work is about
    state       text not null default 'open',   -- open | done | cancelled, or a free state on items without a process
    outcome     text,                           -- the end event reached
    step        text,                           -- the open steps' names
    priority    double precision,               -- #100 score
    assignee    text,                           -- a person, when one has it
    role        text,                           -- the role or agent whose queue it is in
    due         timestamptz,
    created_by  text not null,
    source      text,
    payload     jsonb not null default '{}',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    closed_at   timestamptz
);
create index if not exists work_item_open on work_item (state, priority desc nulls last, id);
create index if not exists work_item_type on work_item (type, state);
create index if not exists work_item_assignee on work_item (assignee, due) where closed_at is null;
create index if not exists work_item_role on work_item (role, due) where closed_at is null;
create index if not exists work_item_due on work_item (due) where closed_at is null;
create index if not exists work_item_subjects on work_item using gin (subjects);

create table if not exists work_process (
    id              bigserial primary key,
    definition_id   bigint not null references work_definition (id),
    process_key     text not null,
    item_id         bigint not null references work_item (id),
    parent_token    bigint,                     -- the call activity step that started it
    correlation_key text,
    state           text not null default 'running',  -- running | completed | terminated | cancelled
    outcome         text,
    data            jsonb not null default '{}',
    started_by      text not null,
    started_at      timestamptz not null default now(),
    ended_at        timestamptz
);
create index if not exists work_process_item on work_process (item_id);
create index if not exists work_process_correlation on work_process (process_key, correlation_key) where state = 'running';

-- One row per step a process reached. Rows stay once done, so they are also the path taken.
create table if not exists work_token (
    id              bigserial primary key,
    process_id      bigint not null references work_process (id),
    item_id         bigint not null,
    element         text not null,
    name            text,
    kind            text not null,              -- auto | user | agent | timer | message | join | call | fix | end
    state           text not null,              -- ready | waiting | done | cancelled
    flow            text,                       -- the sequence flow it arrived on
    role            text,
    assignee        text,
    requires_human  boolean not null default false,
    due_at          timestamptz,
    escalated_at    timestamptz,
    message_name    text,
    correlation_key text,
    attached_to     bigint,                     -- boundary event: the activity step it watches
    cycles_left     int,
    run_after       timestamptz,
    claimed_by      text,
    lease_until     timestamptz,
    attempts        int not null default 0,
    checkpoint      jsonb,
    error           text,
    created_at      timestamptz not null default now(),
    finished_at     timestamptz,
    finished_by     text
);
create index if not exists work_token_open on work_token (process_id) where state in ('ready', 'waiting');
create index if not exists work_token_agent on work_token (run_after, id) where state = 'waiting' and kind = 'agent';
create index if not exists work_token_due on work_token (due_at) where state = 'waiting' and kind in ('timer', 'user');
create index if not exists work_token_message on work_token (message_name, correlation_key) where state = 'waiting' and kind = 'message';
create index if not exists work_token_assignee on work_token (assignee) where state = 'waiting';
create index if not exists work_token_role on work_token (role) where state = 'waiting';
create index if not exists work_token_attached on work_token (attached_to) where state = 'waiting';
create index if not exists work_token_process on work_token (process_id, id);

create table if not exists work_event (
    id            bigserial primary key,
    item_id       bigint,
    process_id    bigint,
    token_id      bigint,
    definition_id bigint,
    kind          text not null,   -- created, started, step_started, assigned, claimed, released, completed, commented,
                                   -- handed_off, escalated, timer_fired, message, agent_result, agent_failed,
                                   -- state_changed, completed_process, published, migrated, ...
    actor         text not null,   -- a person's name, agent:<role> or system
    element       text,
    data          jsonb not null default '{}',
    at            timestamptz not null default now()
);
create index if not exists work_event_item on work_event (item_id, id);
create index if not exists work_event_process on work_event (process_id, id);

-- Agentic SRE runs (#96): each scheduled scan's window; the next run starts where the last done one ended.
create table if not exists sre_run (
    id          bigserial primary key,
    since       timestamp not null,
    until       timestamp not null,
    status      text not null,                  -- running | done | failed
    detections  integer,
    seconds     double precision,
    started_at  timestamptz not null default now(),
    finished_at timestamptz
);

-- What the SRE's detectors found in the process data (sre.py): an ongoing one is extended, not raised again.
create table if not exists detection (
    id          bigserial primary key,
    point_id    text not null,
    unit_id     text,
    detector    text not null,                  -- excursions | stops | steps | drifts | instruments | models
    key         text not null,                  -- what was found: alarm:hi, stopped, step, instrument:noisy, ...
    start       timestamp not null,
    "end"       timestamp,                      -- null while still going at the last run
    magnitude   double precision,
    evidence    jsonb not null default '{}',
    first_run   bigint,
    last_run    bigint,
    found_at    timestamptz not null default now()
);
create index if not exists detection_of on detection (point_id, detector, key, start);
create index if not exists detection_recent on detection (start desc);

-- Detections grouped by time and plant topology (#96b): one upset per cause, its earliest detection the
-- likely first cause. Regrouped after each scan; an upset keeps its id (and status, once a person sets it)
-- while its detections stay together.
create table if not exists upset (
    id              bigserial primary key,
    kind            text not null,              -- process | instrument | slow
    status          text not null default 'detected', -- detected | confirmed | resolved | false_alarm
    start           timestamp not null,
    "end"           timestamp,
    first_detection bigint,
    first_unit      text,
    units           text[] not null default '{}',
    detections      integer not null default 0,
    updated_at      timestamptz not null default now()
);
alter table detection add column if not exists upset_id bigint;
create index if not exists upset_recent on upset (start desc);
alter table upset add column if not exists work_item_id bigint;
alter table upset add column if not exists priority_usd double precision;
alter table upset add column if not exists diagnosis jsonb;

-- Detection patterns an engineer called a false alarm (#96): the SRE doesn't hand them off again.
create table if not exists sre_suppress (
    point_id    text not null,
    detector    text not null,
    key         text not null,
    upset_id    bigint,
    note        text,
    created_at  timestamptz not null default now(),
    primary key (point_id, detector, key)
);

-- The work board (#99): every outstanding piece of work as one ranked card, rebuilt by board.refresh.
create table if not exists board_card (
    key           text primary key,               -- the leading record: incident:<id>, upset:<id>, work_item:<id>, ...
    kind          text not null,
    title         text not null,
    text          text,
    unit_id       text,
    area_id       text,
    records       text[] not null default '{}',   -- every record on the card (one piece of work, several systems)
    value_usd     double precision,               -- #100 score; null when not valued
    effective_usd double precision,               -- after any pinned value
    reason        text,
    breakdown     jsonb not null default '{}',
    rank          integer not null,
    prev_rank     integer,
    moved         text,                           -- why it moved since the last refresh
    pinned        boolean not null default false,
    item_id       bigint,                         -- its workflow item, if any
    first_seen    timestamp not null,
    updated_at    timestamp not null
);
create index if not exists board_card_rank on board_card (rank);
create index if not exists board_card_area on board_card (area_id, rank);
create index if not exists board_card_unit on board_card (unit_id, rank);

-- A person's pinned rank or value for a card, by record key: survives refreshes and rebuilds.
create table if not exists board_pin (
    key        text primary key,
    rank       integer,
    value_usd  double precision,
    reason     text not null,
    pinned_by  text not null,
    pinned_at  timestamptz not null default now()
);

create table if not exists board_history (
    key        text not null,
    rank       integer not null,
    value_usd  double precision,
    at         timestamptz not null default now()
);
create index if not exists board_history_key on board_history (key, at);

-- Projects created on the board, with scope, owner, dates and expected benefit.
create table if not exists board_project (
    id          bigserial primary key,
    title       text not null,
    unit_id     text,
    owner       text,
    due         date,
    benefit_usd double precision,
    effort_usd  double precision,
    scope       text,
    status      text not null default 'open',
    created_by  text not null,
    created_at  timestamptz not null default now()
);

-- Daily operations reports (#98): one row per version of a production day's report. A new version when
-- the day's facts change (late records), with what changed.
create table if not exists daily_ops_report (
    id          bigserial primary key,
    day         date not null,
    version     integer not null,
    facts       jsonb not null,                 -- the outline as tools worked it out, each fact with its citations
    fingerprint text not null,
    markdown    text not null,
    checks      jsonb not null default '{}',    -- uncited lines, citations and numbers not in the facts
    drafted_by  text not null,                  -- template | claude
    usage       jsonb,                          -- tokens and cost of Claude's draft
    seconds     double precision,
    changes     jsonb,                          -- against the previous version
    item_id     bigint,
    created_at  timestamptz not null default now(),
    unique (day, version)
);

-- Debottleneck options (#107): ranked proposals against back-pressure, kept from the last scan so the
-- work board (#99) and the value score (#100) can read them without re-running the hydraulics.
create table if not exists debottleneck_option (
    id         bigserial primary key,
    scanned_at timestamptz not null default now(),
    as_of      text not null,
    rank       integer not null,
    kind       text not null,
    target     text,
    action     text not null,
    gain_sm3d  double precision not null,
    detail     jsonb not null
);

-- The opportunity scan (#102): gains left on the table, one row per opportunity under a stable key. A decided
-- one (rejected, accepted, project) isn't raised again unless its evidence or gain changes materially.
create table if not exists opportunity (
    key         text primary key,
    kind        text not null,
    title       text not null,
    target      text,
    gain_sm3d   double precision,
    value_usd   double precision,
    detail      jsonb not null,
    fingerprint text not null,
    item_id     bigint,
    status      text not null default 'open',    -- open | rejected | accepted | project | gone
    reason      text,
    decided_by  text,
    decided_at  timestamptz,
    scanned_at  timestamptz not null default now()
);
alter table detection add column if not exists seen_until timestamp;  -- the end of the last window that saw it

-- Studies (#106): a correlation or clustering run kept with its parameters, top results and figure, so people can
-- re-run it, an agent can cite it and a work item can attach it. Big ones queue for the background job.
create table if not exists study (
    id          bigserial primary key,
    kind        text not null,                  -- correlate | correlation_matrix | modes | entities | events | spe
    params      jsonb not null,
    status      text not null default 'queued', -- queued | running | done | failed
    result      jsonb,
    summary     text,
    figure_id   text,
    error       text,
    created_by  text not null,
    created_at  timestamptz not null default now(),
    finished_at timestamptz
);
create index if not exists study_queue on study (status, id);

-- Settings (#131): values people set, per project ('project:<name>') or for all ('global'); the registry
-- in core/settings.py holds each setting's type, default and range. Every change is kept.
create table if not exists setting (
    scope      text not null,
    key        text not null,
    value      jsonb not null,
    updated_by text not null,
    updated_at timestamptz not null default now(),
    primary key (scope, key)
);
create table if not exists setting_history (
    id     bigserial primary key,
    scope  text not null,
    key    text not null,
    old    jsonb,
    new    jsonb,
    by     text not null,
    reason text,
    at     timestamptz not null default now()
);
create index if not exists setting_history_key on setting_history (key, id);

-- Projects (#131): what one name selects (pack, data folder, mappings, time-series provider, timezone);
-- projects/<name>.yaml in the repository is the fallback when there is no row.
create table if not exists project (
    name       text primary key,
    config     jsonb not null,
    updated_by text not null,
    updated_at timestamptz not null default now()
);
