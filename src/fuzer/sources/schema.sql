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

-- Sources (#225): the systems of record, how to reach them, what each dataset is and how it loads, as rows
-- (sources/catalog.py). The built-in mapping files only seed a fresh database (`fuzer sources seed`).
-- One catalog per database, like the records it loads.
create table if not exists source_system (
    id          bigserial primary key,
    name        text not null unique,           -- what source_record.source says: sap, pi, ec, maximo, ...
    kind        text,                           -- erp | historian | allocation | documents | gis | lab | logs | other
    description text,
    owner       text,                           -- who answers for the system; alerts go to them (#225)
    steward     text,                           -- who looks after its data in Fuzer
    timezone    text,                           -- how the source writes dates and numbers, applied before mapping
    encoding    text,
    decimal     text,
    date_format text,
    rank        integer,                        -- survivorship: a lower rank wins a field (pack priority order)
    enabled     boolean not null default true,
    notes       text,                           -- reliability, known issues
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- How to reach a system. Secrets are references (env:NAME), never values. A system can have several.
create table if not exists source_connection (
    id             bigserial primary key,
    system_id      bigint not null references source_system (id) on delete cascade,
    name           text not null,
    type           text not null,               -- folder | pi_web_api | adx (sources/connections.py)
    config         jsonb not null default '{}',
    secrets        jsonb not null default '{}', -- name -> reference
    enabled        boolean not null default true,
    last_tested_at timestamptz,
    last_test      jsonb,                       -- each step of the last test with its outcome and timing
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    unique (system_id, name)
);

-- One table or feed: where it is, how it loads, and what it held at its last load.
create table if not exists source_dataset (
    id            bigserial primary key,
    name          text not null unique,
    system_id     bigint not null references source_system (id),
    connection_id bigint references source_connection (id) on delete set null,
    record_type   text,                         -- tables: the record type its rows land as
    reader        text,                         -- nested exports: the named reader (pi_af, ec_network, ...)
    location      text,                         -- where it is on its connection (default: its name)
    connector     text,                         -- csv | parquet | delta | excel | json | geojson (default: by file)
    key           text[],                       -- record key columns, joined by ">"
    watermark     text,                         -- append: rows at or after the last value are read
    load_mode     text not null default 'snapshot' check (load_mode in ('snapshot', 'incremental', 'append')),
    drift_policy  text not null default 'hold_breaking'
                  check (drift_policy in ('hold_any', 'hold_breaking', 'ignore')),
    status        text not null default 'active' check (status in ('draft', 'active', 'paused', 'retired')),
    schedule      text,                         -- cron; empty: loads with refresh_all and the new-exports sensor
    expected_hours real,                        -- freshness: a new delivery within this long, else stale
    owner         text,
    notes         text,
    seeded        jsonb,                        -- the values the seed gave it: a later seed updates only those
    loaded_with   text,                         -- state at its last load: connector kind, fingerprint, schema,
    fingerprint   text,                         -- watermark position, rows
    schema        jsonb,
    position      jsonb,
    rows          bigint,
    loaded_at     timestamptz,
    checked_at    timestamptz,                  -- last looked at (unchanged loads too)
    last_run_id   bigint,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

-- Before #225 the load state was its own table; seeding moves it onto source_dataset.
do $$
begin
    if exists (select 1 from information_schema.tables where table_schema = current_schema()
               and table_name = 'dataset_state' and table_type = 'BASE TABLE') then
        alter table dataset_state rename to dataset_state_old;
    end if;
end $$;
create or replace view dataset_state as
    select name as dataset, loaded_with as connector, fingerprint, schema, position, rows, loaded_at
    from source_dataset where fingerprint is not null;

-- Mapping versions: what each column means, per source and record type. The latest approved version is the
-- one in use; drafts wait for approval, and a rollback approves a copy of an older one (parent_id).
create table if not exists mapping_version (
    id          bigserial primary key,
    source      text not null,
    record_type text not null,
    dataset     text,
    yaml        text not null,
    approved_by text,
    approved_at timestamptz not null default now()
);
alter table mapping_version add column if not exists dataset_id bigint references source_dataset (id) on delete set null;
alter table mapping_version add column if not exists status text not null default 'approved';
alter table mapping_version add column if not exists parent_id bigint;
alter table mapping_version add column if not exists author text;
alter table mapping_version add column if not exists note text;
alter table mapping_version add column if not exists seed_sha text;  -- a version from the built-in files: their hash
alter table mapping_version add column if not exists created_at timestamptz not null default now();
alter table mapping_version alter column approved_at drop not null;
alter table mapping_version drop constraint if exists mapping_version_status_check;
alter table mapping_version add constraint mapping_version_status_check
    check (status in ('draft', 'approved', 'retired'));
create index if not exists mapping_version_type on mapping_version (source, record_type, id desc);

-- One row per load of a dataset that changed (unchanged ones only touch source_dataset.checked_at).
create table if not exists source_run (
    id          bigserial primary key,
    dataset_id  bigint references source_dataset (id) on delete set null,
    dataset     text not null,
    trigger     text not null default 'manual', -- manual | schedule | sensor | api | seed
    status      text not null,                  -- done | held | failed
    started_at  timestamptz not null default now(),
    finished_at timestamptz,
    connector   text,
    fingerprint text,
    schema      jsonb,
    position    jsonb,
    mappings    jsonb,                          -- the mapping version ids in use, by source/record type
    rows_read   bigint,
    inserted    bigint,
    updated     bigint,
    unchanged   bigint,
    removed     bigint,
    error       text,
    detail      jsonb,                          -- held: the schema drift; failed: what broke
    seconds     real
);
create index if not exists source_run_of on source_run (dataset_id, id desc);

-- A profile of each load (sources/quality.py): per column type, fill %, distinct count, top values and samples;
-- the key's duplicates and empties; what changed since the profile before (drift).
create table if not exists source_profile (
    id         bigserial primary key,
    dataset_id bigint not null references source_dataset (id) on delete cascade,
    run_id     bigint,
    taken_at   timestamptz not null default now(),
    rows       bigint,
    sampled    boolean not null default false,
    columns    jsonb not null,
    key        jsonb,
    drift      jsonb
);
create index if not exists source_profile_of on source_profile (dataset_id, id desc);

-- Expectations per dataset and their results: load checks run before a load is applied (a failing blocking one
-- holds it), freshness and coverage are judged by the health summary and after rebuilds.
create table if not exists source_check (
    id          bigserial primary key,
    dataset_id  bigint not null references source_dataset (id) on delete cascade,
    kind        text not null check (kind in ('key_unique', 'not_null', 'range', 'accepted', 'row_count', 'freshness',
                                              'coverage')),
    column_name text,
    params      jsonb not null default '{}',
    blocking    boolean not null default false,
    enabled     boolean not null default true,
    note        text,
    created_by  text,
    created_at  timestamptz not null default now()
);
create table if not exists source_check_result (
    id       bigserial primary key,
    check_id bigint not null references source_check (id) on delete cascade,
    run_id   bigint,
    at       timestamptz not null default now(),
    ok       boolean not null,
    value    double precision,
    detail   jsonb
);
create index if not exists source_check_result_of on source_check_result (check_id, id desc);

-- Per source after each rebuild (90 days kept): link coverage, how its records matched, what it shares with
-- other sources, golden fields won, open reviews.
create table if not exists source_match_stat (
    id       bigserial primary key,
    built_at timestamptz not null default now(),
    source   text not null,
    stats    jsonb not null
);
create index if not exists source_match_stat_of on source_match_stat (source, id desc);

-- The source quality scorecard (P2.6, sources/scorecard.py): each dataset's four measures, 0-100, kept with every
-- rebuild for 90 days so each has a trend; a drop raises a source_quality_drop case for its owner.
create table if not exists source_score (
    id           bigserial primary key,
    dataset_id   bigint not null references source_dataset (id) on delete cascade,
    at           timestamptz not null default now(),
    completeness real,
    validity     real,
    timeliness   real,
    agreement    real
);
create index if not exists source_score_of on source_score (dataset_id, id desc);

-- Survivorship and matching rules (sources/rules.py): a field's own priority and conflict review, an entity
-- kind's match thresholds. The default priority is the systems' rank. Seeded from the pack.
create table if not exists source_rule (
    id         bigserial primary key,
    scope      text not null check (scope in ('field', 'match')),
    name       text not null,
    config     jsonb not null,
    updated_by text not null,
    updated_at timestamptz not null default now(),
    unique (scope, name)
);

-- Every change to the catalog: who, when, what it was and became, and why.
create table if not exists source_audit (
    id        bigserial primary key,
    at        timestamptz not null default now(),
    by        text not null,
    object    text not null,                    -- system | connection | dataset | mapping | check | rule
    object_id bigint,
    name      text,
    action    text not null,                    -- create | update | delete | approve | rollback | pause | ...
    old       jsonb,
    new       jsonb,
    reason    text
);
-- #459: a repeated key holds the load (hold), or the load keeps the latest row per key and lists the others
alter table source_dataset add column if not exists duplicates text not null default 'hold'
    check (duplicates in ('hold', 'keep_latest'));
create index if not exists source_audit_of on source_audit (object, object_id, id desc);

-- Reference curves per equipment model (OEM pump curves, choke characteristics): from vendor CSV
-- exports, and from datasheets Claude read once an engineer accepted them (curve_digitised). Rebuilt
-- with the graph.
create table if not exists equipment_curve (
    model_id  text not null,              -- equipment_model entity (or an equipment item, for its own compressor map)
    source    text not null,              -- 'oem' (vendor export) or 'datasheet:<doc number>'
    curve     text,                       -- pump | choke | compressor
    series    text not null,              -- head, efficiency, npshr, power, cv, head@<speed %>, efficiency@<speed %>,
                                          -- surge, or a rating (bep, por, cv_max, rated_speed, design, ...)
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

-- The work a system brings (#465, sources/workflows.py): its webhook's shared secret (a reference, env:NAME, never
-- the value), and the rules that turn its loaded records into exceptions. Rules run after every load.
alter table source_system add column if not exists webhook_secret text;
create table if not exists source_exception_rule (
    id          bigserial primary key,
    system      text not null,
    name        text not null,
    dataset     text not null,
    type        text not null,                  -- the exception type raised (usually one the system's pack brought)
    config      jsonb not null default '{}',    -- when, clear_when, key, refs, title, priority, value_usd
    enabled     boolean not null default true,
    updated_by  text not null,
    updated_at  timestamptz not null default now(),
    unique (system, name)
);

-- How the work a system raised closes out upstream when the field finishes it (#466, ops/closeout.py): none, draft
-- (a case on closeout_role's Inbox to approve first) or send. Unset: none, except SAP, which drafts to the planners.
alter table source_system add column if not exists closeout text check (closeout in ('none', 'draft', 'send'));
alter table source_system add column if not exists closeout_role text;

-- Aliases a steward recorded (#476, sources/aliases.py): a value a system writes, as another name for an entity
-- (by one of its keys, so it survives re-numbering). Laid over the pack's aliases on each rebuild; matched by
-- exceptions as they are raised. Retired, not deleted, so the decision log keeps them.
create table if not exists entity_alias (
    id          bigserial primary key,
    value       text not null,
    norm        text not null,                  -- lower case, single spaces: what is matched
    kind        text not null,                  -- the entity key it names: tag, well, equipment, ...
    key         text not null,
    fuzer_id    text,                           -- the entity when it was recorded
    source      text,                           -- the system whose value it was, when known
    created_by  text not null,
    created_at  timestamptz not null default now(),
    reason      text,
    retired_at  timestamptz,
    retired_by  text
);
create unique index if not exists entity_alias_current on entity_alias (norm) where retired_at is null;

-- What a system takes back from Fuzer (P5.7, ops/writeback.py), from its source pack: accepts (kinds), approvers,
-- kinds.<kind>.{approvers, cadence}, adapter (export | demo_csv) and limit per run. Unset: the queue's defaults.
alter table source_system add column if not exists writeback jsonb;
