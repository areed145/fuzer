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

-- "Not this link" (P2.1c, graph/contextualized.py): a source's statement of a link a steward said is wrong. The edge
-- build leaves that source's statement out on every rebuild (another source stating the same link keeps it).
create table if not exists unlinked (
    source     text not null,
    src        text not null,
    rel        text not null,
    dst        text not null,
    decided_by text not null,
    decided_at timestamptz not null default now(),
    reason     text,
    primary key (source, src, rel, dst)
);

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
-- A steward's merge or split (#476) writes several pins at once; they share a batch so one undo reverses it.
alter table pinned_link add column if not exists batch text;
alter table pinned_link add column if not exists reason text;

-- Standing answers (P2.3c): one answer for a group of like questions that also decides new ones as they arrive.
create table if not exists review_rule (
    id           bigserial primary key,
    group_key    text not null,
    kind         text not null,
    decision     text not null,
    source       text,
    value        jsonb,
    set_by       text not null,
    set_at       timestamptz not null default now(),
    withdrawn_by text,
    withdrawn_at timestamptz
);
create unique index if not exists review_rule_standing on review_rule (group_key) where withdrawn_at is null;

-- Every pair the matcher scored on the last rebuild, with what it did (P2.2): what calibration measures against labels.
create table if not exists match_score (
    kind        text not null,
    left_ref    text not null,
    right_ref   text not null,
    sources     text[] not null,
    score       double precision not null,
    outcome     text not null check (outcome in ('merged', 'review', 'ambiguous', 'rejected')),
    primary key (kind, left_ref, right_ref)
);

-- The last calibration per entity kind and source (P2.2): measured precision at each threshold, the calibrated
-- probability for a score, and the thresholds it recommends.
create table if not exists match_calibration (
    kind        text not null,
    source      text not null,
    result      jsonb not null,
    labels      text not null,
    measured_at timestamptz not null default now(),
    measured_by text,
    primary key (kind, source)
);

-- What each source says on a field a steward chose the value of, from the last rebuild (P2.4): how a correction
-- sent back to a source is seen to land.
create table if not exists reviewed_value (
    fuzer_id   text not null,
    field      text not null,
    source     text not null,
    source_key text,
    value      jsonb
);
create index if not exists reviewed_value_key on reviewed_value (fuzer_id, field);

-- Records a steward said are new items, not ones the register already has (P2.1b, graph/contextualized.py): they no
-- longer count as unmatched, so one answer settles a source's whole group.
create table if not exists source_new_record (
    source      text not null,
    record_type text not null,
    source_key  text not null,
    decided_by  text not null,
    decided_at  timestamptz not null default now(),
    reason      text,
    primary key (source, record_type, source_key)
);

-- Fast "contains" search over values, keys and source keys when pg_trgm is available
-- (Docker / Azure Postgres). Search still works without it, just slower.
do $$
declare ops text;
begin
    if exists (select 1 from pg_available_extensions where name = 'pg_trgm') then
        -- One copy per database, in the shared schema (older databases have it in public); the operator class
        -- is named with its schema so every project's schema can use it.
        create extension if not exists pg_trgm schema fuzer;
        select extnamespace::regnamespace::text into ops from pg_extension where extname = 'pg_trgm';
        -- Rebuilds sync golden (only changed rows touch the index), so a full index is cheap to keep.
        drop index if exists golden_value_trgm;
        drop index if exists golden_search_trgm;
        execute format('create index if not exists golden_text_trgm on golden using gin ((value::text) %I.gin_trgm_ops)', ops);
        execute format('create index if not exists entity_key_trgm on entity_key using gin (key %I.gin_trgm_ops)', ops);
        execute format('create index if not exists entity_label_trgm on entity_label using gin (label %I.gin_trgm_ops)', ops);
        execute format('create index if not exists crosswalk_key_trgm on crosswalk using gin (source_key %I.gin_trgm_ops)', ops);
    end if;
end
$$;

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

-- What each historian point means for the asset it measures (P1.4, graph/roles.py): one role from core/roles.py,
-- proposed with every rebuild from the point's description, instrument letters, units and the equipment it is on.
create table if not exists measurement_role (
    point_id   text primary key,            -- the timeseries entity
    role       text not null,
    asset      text,                        -- the equipment or well it is a role of (not the instrument)
    confidence double precision not null,   -- 0-1; below core.roles.CONFIDENT a steward confirms it
    basis      jsonb not null               -- what said so, in words
);
create index if not exists measurement_role_of on measurement_role (asset, role);
-- A steward's role for a point (P1.4b): wins over the proposal on every rebuild.
create table if not exists measurement_role_pin (
    point_id   text primary key,
    role       text not null,
    decided_by text not null,
    decided_at timestamptz not null default now()
);

-- #242: the EC DAY001 record of a day's allocated production is the `production_day` kind ("daily report" is the
-- drafted narrative in daily_ops_report). Renamed in place so Fuzer IDs stay; idempotent.
update entity set kind = 'production_day' where kind = 'daily_report';
update entity_key set kind = 'production_day' where kind = 'daily_report';
update ontology_kind set name = 'production_day' where name = 'daily_report'
    and not exists (select 1 from ontology_kind where name = 'production_day');
delete from ontology_kind where name = 'daily_report' and status = 'core';
update ontology_relation set from_kinds = array_replace(from_kinds, 'daily_report', 'production_day'),
    to_kinds = array_replace(to_kinds, 'daily_report', 'production_day')
    where 'daily_report' = any(from_kinds) or 'daily_report' = any(to_kinds);
update register_summary set value = replace(value::text, '"daily_report"', '"production_day"')::jsonb
    where name = 'ontology' and value::text like '%"daily_report"%';

-- Values a steward set across many entities at once (#476: one spelling for a manufacturer, say). Each row keeps what
-- the golden value was, so the batch can be undone; the value itself is held as a 'review' assertion, which wins on
-- every rebuild.
create table if not exists steward_fix (
    id          bigserial primary key,
    batch       text not null,
    fuzer_id    text not null,
    field       text not null,
    old_value   jsonb,
    old_source  text,
    old_rule    text,
    new_value   jsonb not null,
    created_by  text not null,
    created_at  timestamptz not null default now(),
    reason      text,
    undone_at   timestamptz,
    undone_by   text
);
create index if not exists steward_fix_batch on steward_fix (batch);

-- Claude's proposals for the steward (#476): a question built from the register (which entity an unresolved value
-- means, whether two records are one item, which of the sources' values is right, whether two spellings are one
-- value), Claude's pick from its numbered options with a confidence, reason and evidence, and what a person did with
-- it. Cached by the question's hash and the prompt version; nothing is applied until someone accepts it.
create table if not exists steward_proposal (
    id             bigserial primary key,
    kind           text not null,
    subject        text not null,
    question       jsonb not null,
    input_hash     text not null,
    answer         jsonb,
    model          text,
    prompt_version text,
    status         text not null default 'open' check (status in ('open', 'accepted', 'rejected', 'stale')),
    decided_by     text,
    decided_at     timestamptz,
    result         jsonb,
    created_at     timestamptz not null default now(),
    unique (kind, subject)
);
