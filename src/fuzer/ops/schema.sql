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

-- Each point's usual step-to-step spread over the days before `day` (sre.baselines): the SRE's instrument
-- checks read it from the historian once a day, not every window (#203). usual null: too few readings.
create table if not exists point_baseline (
    point_id    text primary key,
    day         date not null,
    step        integer not null,               -- minutes between the values it was worked out from
    days        integer not null,               -- how many days before `day` it covers
    usual       double precision,
    computed_at timestamptz not null default now()
);

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

-- Exceptions (core/exceptions.py): anything that should draw the IOC's attention and be worked, from any source,
-- once per open condition (type and the source's own key), matched to the entities it is about and routed to the
-- case that works it. open: the condition holds; cleared: it went away; closed: its case finished while it held.
create table if not exists exception_record (
    id           bigserial primary key,
    type         text not null,
    key          text not null,
    title        text not null,
    source       text,                           -- the module or system that raised it
    state        text not null default 'open',   -- open | cleared | closed
    priority     double precision,
    value_usd    double precision,
    subjects     text[] not null default '{}',   -- fuzer ids it was matched to
    unmatched    text[] not null default '{}',   -- references that matched no entity, kept visible
    evidence     jsonb not null default '{}',
    item_id      bigint,                         -- the case that works it
    raised_count integer not null default 1,
    first_seen   timestamptz not null default now(),
    last_seen    timestamptz not null default now(),
    closed_at    timestamptz,
    updated_at   timestamptz not null default now()
);
create unique index if not exists exception_record_open on exception_record (type, key) where state = 'open';
create index if not exists exception_record_subjects on exception_record using gin (subjects);
-- Source problems opened as work items before the exception contract become its open exceptions, so the next
-- health check updates them instead of opening a second case.
insert into exception_record (type, key, title, source, priority, evidence, item_id, first_seen, last_seen)
    select 'source_issue', (w.payload->>'dataset') || ':' || (w.payload->>'problem'), w.title, 'sources', w.priority,
           w.payload, w.id, w.created_at, w.updated_at
    from work_item w
    where w.type = 'source_issue' and w.state not in ('done', 'cancelled') and w.payload ? 'dataset'
      and not exists (select 1 from exception_record e where e.item_id = w.id)
    on conflict do nothing;
-- Upsets and IOW breaches handed off before they were exceptions become open ones, so the next scan feeds the
-- process already working them instead of starting another.
insert into exception_record (type, key, title, source, priority, value_usd, subjects, evidence, item_id, first_seen,
                              last_seen)
    select 'upset', 'upset:' || u.id, w.title, 'sre', w.priority, u.priority_usd, w.subjects,
           coalesce(w.payload->'start', '{}'), w.id, w.created_at, w.updated_at
    from upset u join work_item w on w.id = u.work_item_id
    where w.state not in ('done', 'cancelled') and not exists (select 1 from exception_record e where e.item_id = w.id)
    on conflict do nothing;
insert into exception_record (type, key, title, source, priority, subjects, evidence, item_id, first_seen, last_seen)
    select 'iow_breach', w.payload->'start'->>'iow', w.title, 'limits', w.priority, w.subjects, w.payload->'start',
           w.id, w.created_at, w.updated_at
    from work_item w
    where w.type = 'integrity' and w.state not in ('done', 'cancelled') and w.payload->'start' ? 'iow'
      and not exists (select 1 from exception_record e where e.item_id = w.id)
    on conflict do nothing;

-- Exception types a deployment defines as data (ops/exceptions.define), so other systems (SAP notifications, SCADA
-- alarms, a reporting app) raise them over the API without code. Types declared in code are not stored here.
create table if not exists exception_kind (
    name                text primary key,
    label               text not null,
    role                text,                    -- the queue a case goes to
    process             text,                    -- or a published BPMN process to start / feed
    case_type           text,
    audience            text[] not null default '{ioc}',
    close_case_on_clear boolean not null default true,
    description         text,
    updated_by          text not null,
    updated_at          timestamptz not null default now()
);

-- The integrated plan (ops/plan.py): every planned activity from every source on one timeline (SAP work orders,
-- exceptions the IOC planned, well interventions, turnaround tasks, logistics), matched to the assets it touches,
-- so the IOC sees clashes on an asset or a crew and work that could ride along with a shutdown.
create table if not exists plan_item (
    id             bigserial primary key,
    kind           text not null,                  -- maintenance | inspection | well_intervention | turnaround | ...
    title          text not null,
    source         text,                           -- the system or module that proposed it
    source_key     text,                           -- its identity there (an SAP order number): proposals are idempotent
    subjects       text[] not null default '{}',   -- fuzer ids it touches
    area_id        text,                           -- the area of its first located subject
    start_at       timestamptz not null,
    end_at         timestamptz not null,
    status         text not null default 'proposed', -- proposed | scheduled | in_progress | done | cancelled
    crew           text,                           -- a person or role:<role>
    crew_hours     double precision,
    needs_shutdown boolean not null default false,
    deferment_usd  double precision,               -- production at risk while it runs
    exception_id   bigint,                         -- the exception it works, if any
    notes          text,
    created_by     text not null,
    updated_by     text not null,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    check (end_at >= start_at)
);
create unique index if not exists plan_item_source on plan_item (source, source_key) where source_key is not null;
create index if not exists plan_item_window on plan_item (start_at, end_at);
create index if not exists plan_item_subjects on plan_item using gin (subjects);
