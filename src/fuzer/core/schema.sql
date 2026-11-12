-- Platform core's tables. Every module keeps the tables it owns in its own schema.sql, applied in the order of
-- resources.SCHEMA_MODULES, core first.

-- Which schema this database is at (the hash of every module's schema.sql): init_schema re-runs them only when
-- one changed, since their migrations take exclusive locks on busy tables.
create table if not exists schema_version (
    id         int primary key check (id = 1),
    hash       text not null,
    applied_at timestamptz not null default now()
);

-- Shared by every project in the instance: accounts and access, the project registry and settings live in
-- the `fuzer` schema; a project's register lives in its own schema (`public` for a database's first project,
-- `proj_<name>` for the others), where everything below without a schema name is created. Connections search the project schema first, then `fuzer`, so queries name tables unqualified.
-- A database from before the split has these tables in public; they move here in place (no data copy).
create schema if not exists fuzer;
do $$
declare t text;
begin
    foreach t in array array['access_account', 'access_role', 'access_membership', 'access_session', 'access_login',
                             'access_event', 'setting', 'setting_history', 'project'] loop
        if to_regclass('public.' || t) is not null and to_regclass('fuzer.' || t) is null then
            execute format('alter table public.%I set schema fuzer', t);
        end if;
    end loop;
end $$;

-- Sign-in identities are independent of workflow routing roles.
create table if not exists fuzer.access_account (
    id text primary key,
    tenant_id text not null,
    object_id text not null,
    display_name text not null,
    enabled boolean not null default false,
    owner boolean not null default false,
    created_at timestamptz not null default now(),
    unique (tenant_id, object_id),
    check (not owner or enabled)
);
create table if not exists fuzer.access_role (
    name text primary key,
    description text not null,
    permissions text[] not null default '{}'
);
create table if not exists fuzer.access_membership (
    account_id text references fuzer.access_account(id) on delete cascade,
    role text references fuzer.access_role(name),
    primary key (account_id, role)
);
create table if not exists fuzer.access_session (
    token_hash text primary key,
    account_id text not null references fuzer.access_account(id) on delete cascade,
    csrf text not null,
    expires_at timestamptz not null
);
create index if not exists access_session_account on fuzer.access_session(account_id);
-- Application roles in a project other than the database's first one (whose roles are access_membership).
-- Owners span every project; anyone else sees a project only through a grant here.
create table if not exists fuzer.access_project_grant (
    account_id text references fuzer.access_account(id) on delete cascade,
    project    text not null,
    role       text references fuzer.access_role(name),
    primary key (account_id, project, role)
);
create table if not exists fuzer.access_login (
    token_hash text primary key,
    flow jsonb not null,
    expires_at timestamptz not null
);
create table if not exists fuzer.access_event (
    id bigserial primary key,
    actor text not null,
    action text not null,
    data jsonb not null default '{}',
    at timestamptz not null default now()
);
-- The project an access event concerns (R.5); null for a deployment-wide one such as a sign-in.
alter table fuzer.access_event add column if not exists project text;
create index if not exists access_event_project on fuzer.access_event (project, at);

-- Settings (#131): values people set, per project ('project:<name>') or for all ('global'); the registry
-- in core/settings.py holds each setting's type, default and range. Every change is kept.
create table if not exists fuzer.setting (
    scope      text not null,
    key        text not null,
    value      jsonb not null,
    updated_by text not null,
    updated_at timestamptz not null default now(),
    primary key (scope, key)
);
create table if not exists fuzer.setting_history (
    id     bigserial primary key,
    scope  text not null,
    key    text not null,
    old    jsonb,
    new    jsonb,
    by     text not null,
    reason text,
    at     timestamptz not null default now()
);
create index if not exists setting_history_key on fuzer.setting_history (key, id);

-- Projects (#131): what one name selects (pack, data folder, mappings, time-series provider, timezone);
-- a shipped seed bundle of the same name is the fallback when there is no row.
create table if not exists fuzer.project (
    name       text primary key,
    config     jsonb not null,
    updated_by text not null,
    updated_at timestamptz not null default now()
);

-- The data model as data (#241): entity kinds and link types. core/ontology.yaml seeds the core rows; a
-- person's edit (edited) is kept over a later change to the file. from_kinds / to_kinds empty means any kind.
create table if not exists ontology_kind (
    name        text primary key,
    label       text not null,
    description text not null default '',
    "group"     text,
    colour      text,
    label_field text,
    status      text not null default 'custom' check (status in ('core', 'custom', 'draft')),
    edited      boolean not null default false,
    updated_by  text not null,
    updated_at  timestamptz not null default now()
);
create table if not exists ontology_relation (
    name        text primary key,
    label       text not null,
    description text not null default '',
    view        text,
    dated       boolean not null default false,
    from_kinds  text[] not null default '{}',
    to_kinds    text[] not null default '{}',
    status      text not null default 'custom' check (status in ('core', 'custom', 'draft')),
    edited      boolean not null default false,
    updated_by  text not null,
    updated_at  timestamptz not null default now()
);
create table if not exists ontology_audit (
    id     bigserial primary key,
    at     timestamptz not null default now(),
    by     text not null,
    object text not null,                       -- kind | relation
    name   text not null,
    action text not null,                       -- create | update | delete | rename
    old    jsonb,
    new    jsonb,
    reason text
);
create index if not exists ontology_audit_of on ontology_audit (object, name, id desc);

-- The event outbox (core/events.py): what happened, written in the transaction that made the change, then handed
-- to subscribers by the work tick. A handler that fails leaves its event pending, with the error, to retry.
create table if not exists event_outbox (
    id           bigserial primary key,
    topic        text not null,                  -- exception.raised, exception.cleared, ...
    ref          text,                           -- what it is about: exception:<id>, work_item:<id>, ...
    payload      jsonb not null default '{}',
    created_at   timestamptz not null default now(),
    delivered_at timestamptz,
    attempts     integer not null default 0,
    last_error   text
);
create index if not exists event_outbox_pending on event_outbox (id) where delivered_at is null;

-- Human gates as policy (P5.10, #619; core/gates.py). The fixed gates are code and never configurable; a confidence
-- gate's thresholds (per asset criticality) and the safety case types are data here, over the shipped defaults.
create table if not exists gate_policy (
    kind       text primary key,
    settings   jsonb not null default '{}',
    updated_by text,
    updated_at timestamptz not null default now()
);
-- Every automatic or agent decision a gate saw: its confidence, the threshold applied and whether the gate fired
-- (stopped it for a person), with the reason a person reads on the case.
create table if not exists gate_decision (
    id              bigserial primary key,
    kind            text not null,
    actor           text not null,
    ref             text,
    item_id         bigint,
    confidence      double precision,
    threshold       double precision,
    criticality     text,
    could_not_check text[] not null default '{}',
    fired           boolean not null,
    reason          text not null,
    at              timestamptz not null default now()
);
create index if not exists gate_decision_item on gate_decision (item_id, id);
