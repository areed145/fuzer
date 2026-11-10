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
-- Away and covered (#workflows): while a person is away, steps assigned to them go to their delegate (or back to
-- the queue), and auto-assignment skips them.
alter table work_person add column if not exists away_until timestamptz;
alter table work_person add column if not exists delegate text;
alter table work_person add column if not exists note text;

-- Overarching people (#497): specialists and monitoring staff whose work cuts across fields. Shared by every project
-- in the instance; their roles count in each project they cover (`projects` empty: every project), on top of any
-- roles that project's own roster gives them. Routing roles only: whether they may open a project is access's call.
create table if not exists fuzer.work_person_cover (
    name       text primary key,
    roles      text[] not null default '{}',
    projects   text[] not null default '{}',
    updated_by text,
    updated_at timestamptz not null default now()
);

-- Roles are data (workflow/roles.py): person roles hold steps people do, agent roles steps agents do. A role's
-- default SLA applies to steps that set none; when a step's SLA passes it moves to the step's escalateTo, else
-- the role's escalate_to (chained up to three hops, each with that role's SLA). Assignment: queue (whoever claims
-- it), round_robin or least_loaded among the role's members who aren't away.
create table if not exists work_role (
    name          text primary key,
    kind          text not null check (kind in ('person', 'agent')),
    description   text,
    default_sla   text,                         -- ISO 8601 duration
    escalate_to   text,
    assignment    text not null default 'queue' check (assignment in ('queue', 'round_robin', 'least_loaded')),
    last_assigned text,                         -- round_robin: who got the last one
    updated_by    text,
    updated_at    timestamptz not null default now()
);

-- The domain agents (#284) on databases that already had roles. An empty table is left alone: roles.refresh seeds
-- every built-in role the first time, and it only does that while the table is empty.
insert into work_role (name, kind, description, updated_by)
select v.name, 'agent', v.description, 'seed'
from (values
    ('production-surveillance', 'Production trends, deferment, constraints and gas lift'),
    ('wells-subsurface', 'Well history, tests and intervention candidates'),
    ('field-operations-facilities', 'Diagnoses upsets from the graph and process data'),
    ('maintenance-integrity', 'Maintenance history, integrity, drafts notifications'),
    ('work-management-supply-chain', 'Work backlog, job readiness and spares'),
    ('hse-incident-management', 'Incident evidence for accountable HSE review'),
    ('asset-performance-reporting', 'Cross-domain summaries')
) as v (name, description)
where exists (select 1 from work_role)
on conflict do nothing;

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

-- What the assistant proposes: it never writes to a queue. A draft belongs to whoever asked and
-- becomes at most one work item, when that person confirms it.
create table if not exists work_draft (
    id           text primary key,
    title        text not null,
    role         text not null,
    summary      text not null,
    evidence     text[] not null,
    subjects     text[] not null default '{}',
    by_user      text not null,
    item_id      bigint references work_item (id),
    confirmed_by text,
    confirmed_at timestamptz,
    created_at   timestamptz not null default now()
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
