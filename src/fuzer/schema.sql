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
