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
alter table assistant_answer add column if not exists images text[] not null default '{}';  -- assistant_image sha256s
alter table assistant_answer add column if not exists recalled bigint[] not null default '{}';  -- assistant_fact ids given
create index if not exists assistant_answer_conversation on assistant_answer (conversation_id, created_at);

-- The assistant's conversations (#93): each message as MAF wrote it, in order. `json`, not `jsonb`, so a
-- message comes back exactly as stored (Claude checks its thinking blocks byte for byte). Images are kept
-- once per conversation and referenced from the messages.
create table if not exists assistant_conversation (
    id         text primary key,
    by_user    text not null default 'anonymous',
    title      text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists assistant_conversation_recent on assistant_conversation (by_user, updated_at desc);

create table if not exists assistant_message (
    conversation_id text not null references assistant_conversation (id) on delete cascade,
    seq             int not null,
    message         json not null,
    created_at      timestamptz not null default now(),
    primary key (conversation_id, seq)
);

create table if not exists assistant_image (
    conversation_id text not null references assistant_conversation (id) on delete cascade,
    sha256          text not null,
    media_type      text not null,
    data            bytea not null,
    created_at      timestamptz not null default now(),
    primary key (conversation_id, sha256)
);

-- Facts the assistant learned from answers rated useful, or statements an engineer marked "remember this"
-- (#93). Dated in the plant (valid_from / valid_to) and in Fuzer (learned_at). A newer fact that contradicts
-- one ends it (status 'ended', invalidated_by); it is never deleted, except by an engineer.
create table if not exists assistant_fact (
    id             bigserial primary key,
    text           text not null,
    subject        text not null default '',
    entity_ids     text[] not null,
    valid_from     date,
    valid_to       date,
    learned_at     timestamptz not null default now(),
    episode        text references assistant_answer (id) on delete set null,
    sources        jsonb not null default '[]',
    status         text not null default 'current' check (status in ('current', 'ended')),
    invalidated_by bigint references assistant_fact (id),
    by_user        text not null default 'anonymous',
    edited_by      text,
    ended_by       text,
    ended_at       timestamptz,
    updated_at     timestamptz not null default now()
);
create index if not exists assistant_fact_entities on assistant_fact using gin (entity_ids);
create index if not exists assistant_fact_episode on assistant_fact (episode);
