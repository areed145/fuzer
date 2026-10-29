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
    fuzer_id text not null references entity (fuzer_id),
    primary key (kind, key, fuzer_id)
);
create index if not exists entity_key_fuzer_id on entity_key (fuzer_id);

create or replace view entity_label as
select fuzer_id, min(key) filter (where kind <> 'tag_noarea') as label
from entity_key
group by fuzer_id;

-- Every field value from every source. Reviewer choices use source = 'review'.
create table if not exists assertion (
    id          bigserial primary key,
    fuzer_id    text not null references entity (fuzer_id),
    field       text not null,
    value       jsonb not null,
    source      text not null,
    source_key  text,
    observed_at timestamptz not null default now()
);
create index if not exists assertion_fuzer_id on assertion (fuzer_id, field);

create table if not exists golden (
    fuzer_id text not null references entity (fuzer_id),
    field    text not null,
    value    jsonb not null,
    source   text not null,
    rule     text not null,
    primary key (fuzer_id, field)
);

create table if not exists edge (
    id         bigserial primary key,
    src        text not null references entity (fuzer_id),
    rel        text not null,
    dst        text not null references entity (fuzer_id),
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
    kind       text not null check (kind in ('match', 'conflict')),
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
