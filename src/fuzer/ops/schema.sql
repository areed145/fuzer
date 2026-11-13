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

-- Each run of a registered detector (core/detectors.py): what it found, or why it did not apply on this asset.
create table if not exists detector_run (
    id       bigserial primary key,
    detector text not null,
    status   text not null,                     -- ran | not_applicable | failed
    note     text,                              -- "not applicable: missing X", or the failure
    found    integer,
    seconds  double precision,
    since    timestamp,
    until    timestamp,
    at       timestamptz not null default now()
);
create index if not exists detector_run_of on detector_run (detector, id desc);

-- One run of a detector tier's job (P3.2): what it cost, for the Jobs page.
create table if not exists detector_tier_run (
    id      bigserial primary key,
    tier    text not null,                      -- 5min | 30min | hourly | 6h | daily (ops/cadence.py)
    until   timestamp not null,
    seconds double precision not null,
    ran     integer not null,
    failed  integer not null,
    at      timestamptz not null default now()
);
create index if not exists detector_tier_run_at on detector_tier_run (tier, at desc);

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
-- The IOC merged this upset's case into another upset's (one cascade, one case).
alter table upset add column if not exists merged_into bigint;

-- Detection patterns an engineer called a false alarm (#96): an upset matching one is closed as a false alarm only
-- where the auto-close gate allows (P5.10, core/gates.py); otherwise it reaches a person saying why.
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

-- Regulatory and emissions reports (P7.6, #954): one row per version of a template's report for a period. A new
-- version when its figures change (a late or corrected record); each figure keeps the rows it is made of and their
-- basis (measured, estimated, missing). Approval and submission are recorded on the version they were for.
create table if not exists regulatory_report (
    id           bigserial primary key,
    template     text not null,
    period       text not null,                 -- YYYY-MM
    version      integer not null,
    status       text not null default 'draft', -- draft | approved | submitted
    sections     jsonb not null,                -- [{title, figures: [{key, label, value, unit, basis, rows}]}]
    marked       jsonb not null default '[]',   -- the figures resting on estimated or missing data
    coverage     jsonb not null,                -- since, through, partial
    fingerprint  text not null,
    drafted_by   text not null,
    item_id      bigint,                        -- the review case
    approved_by  text,
    approved_at  timestamptz,
    submitted    jsonb,                         -- how it went out, by whom and when
    created_at   timestamptz not null default now(),
    unique (template, period, version)
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
-- One event reported by several systems is one case (#464): a later exception about the same asset joins the open
-- one already worked (its host), sharing its case, instead of opening another.
alter table exception_record add column if not exists joined_to bigint;
create index if not exists exception_record_joined on exception_record (joined_to) where joined_to is not null;
create index if not exists exception_record_subjects on exception_record using gin (subjects);
-- Shelved (P3.5c): a standing condition a person set aside with a reason, off the open list until the expiry passes.
alter table exception_record add column if not exists shelved_until timestamptz;
alter table exception_record add column if not exists shelved_by text;
alter table exception_record add column if not exists shelved_reason text;
-- Photos the field attached to a report (P5.3b): resized on the phone, kept with the exception they were taken for.
create table if not exists field_photo (
    id           bigserial primary key,
    exception_id bigint not null,
    by           text not null,
    at           timestamptz not null default now(),
    mime         text not null,
    data         bytea not null
);
create index if not exists field_photo_exception on field_photo (exception_id);
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

alter table exception_kind add column if not exists joins boolean not null default true;  -- may join another's case
alter table exception_kind add column if not exists source_system text;  -- the system whose source pack brought it (#465)
alter table exception_kind add column if not exists due_hours text;  -- its due policy: priority -> hours, "1:24,2:72" (#509)

-- Suppression (P3.5b, ops/suppression.py): a window on some assets in which their detections are recorded and
-- counted here instead of opening cases. Planned work and shutdowns come from plan_item; a person can add one.
create table if not exists suppression (
    id         bigserial primary key,
    subjects   text[] not null,
    start_at   timestamptz not null,
    end_at     timestamptz not null,
    reason     text not null,
    created_by text not null,
    created_at timestamptz not null default now(),
    check (end_at > start_at)
);
create table if not exists suppressed_finding (
    id          bigserial primary key,
    type        text not null,
    key         text not null,
    title       text not null,
    source      text,
    subjects    text[] not null default '{}',
    evidence    jsonb not null default '{}',
    value_usd   double precision,
    window_kind text not null,                    -- planned_work | shutdown | person | muted (a provider, P3.8b)
    window_id   bigint not null,                  -- the plan_item or suppression it fell in (0 when muted)
    reason      text not null,
    raised      integer not null default 1,
    first_seen  timestamptz not null default now(),
    last_seen   timestamptz not null default now(),
    unique (type, key, window_kind, window_id)
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
-- who last moved it by hand: a source's next proposal keeps a person's dates instead of resetting them
alter table plan_item add column if not exists moved_by text;
create unique index if not exists plan_item_source on plan_item (source, source_key) where source_key is not null;
create index if not exists plan_item_window on plan_item (start_at, end_at);
create index if not exists plan_item_subjects on plan_item using gin (subjects);

-- Close-outs back to the source (#466, ops/closeout.py): what the field did, drafted as an update for the system that
-- raised the work (complete the record, or a follow-up when the field could not do it), approved on the planner's
-- Inbox (or sent directly) and published as source.update for the system's adapter.
create table if not exists source_closeout (
    id            bigserial primary key,
    exception_id  bigint not null,
    system        text not null,
    record        text not null,                   -- the system's own key (an SAP notification number)
    action        text not null,                   -- complete | follow_up
    draft         jsonb not null,                  -- text, status, short_text, ... as sent
    state         text not null default 'draft',   -- draft | sent | discarded
    item_id       bigint,                          -- the planner's case, when it was drafted for approval
    created_by    text not null,
    created_at    timestamptz not null default now(),
    decided_by    text,
    decided_at    timestamptz,
    applied_at    timestamptz                      -- the demo applied it to the system's export
);
create index if not exists source_closeout_exception on source_closeout (exception_id);
-- Why a sent close-out wasn't applied to the demo's export file (setting off, no export holds the record, ...).
alter table source_closeout add column if not exists applied_note text;

-- Shift handovers (#467, ops/handover.py): drafted from live work at each shift change, the outgoing lead's notes,
-- and the incoming lead's acknowledgement (also on its case's history).
create table if not exists shift_handover (
    id                bigserial primary key,
    shift_start       timestamptz not null,
    shift_end         timestamptz not null unique,     -- the shift change it hands over at
    facts             jsonb not null,                  -- sections: {name: [{text, cites}]}, more: {name: n}
    state             text not null default 'draft',   -- draft | handed_over (notes added) | acknowledged
    item_id           bigint,                          -- its case on the ops lead queue
    created_by        text not null,
    drafted_at        timestamptz not null default now(),
    notes             text,
    notes_by          text,
    notes_at          timestamptz,
    acknowledged_by   text,
    acknowledged_at   timestamptz,
    acknowledged_note text
);

-- The approved write-back queue (P5.7, #634; ops/writeback.py): every change Fuzer proposes to a source system, and
-- the only way one is written. drafted -> awaiting_approval -> approved -> sent -> confirmed, or failed (back to the
-- approver with the source's reason) or rejected. `idem_key` makes a proposal, and a send, happen once.
create table if not exists writeback (
    id            bigserial primary key,
    system        text not null,
    kind          text not null,      -- notification, safety_notification, order, master_data, annotation, ...
    record        text,               -- the system's own key, when the change is to an existing record
    action        text not null default 'update',
    payload       jsonb not null default '{}',
    idem_key      text not null unique,
    state         text not null default 'drafted'
                  check (state in ('drafted', 'awaiting_approval', 'approved', 'sent', 'confirmed', 'failed', 'rejected')),
    drafted_by    text not null,
    drafted_at    timestamptz not null default now(),
    approver_role text,
    item_id       bigint,             -- the approver's case
    source_ref    text,               -- what proposed it (closeout:12, review:4, ...)
    approved_by   text,
    approved_at   timestamptz,
    due_at        timestamptz,        -- when its cadence lets it go
    sent_at       timestamptz,
    confirmed_at  timestamptz,
    failed_at     timestamptz,
    attempts      integer not null default 0,
    error         text,
    reason        text
);
create index if not exists writeback_due on writeback (due_at) where state = 'approved';
create index if not exists writeback_item on writeback (item_id);
create index if not exists writeback_ref on writeback (source_ref);
create table if not exists writeback_event (
    id            bigserial primary key,
    writeback_id  bigint not null,
    state         text not null,
    by            text not null,
    note          text,
    at            timestamptz not null default now()
);
create index if not exists writeback_event_row on writeback_event (writeback_id, id);

-- Corrections that flow back to a source (P2.4): what a steward's answer said was wrong in a source, the change
-- or case drafted for its owner, and how it came back.
create table if not exists source_correction (
    id             bigserial primary key,
    review_item_id bigint not null,
    kind           text not null,      -- master_data | drawing_correction | correction | field_check
    system         text not null default '',
    fuzer_id       text,
    field          text,
    wrong          jsonb,              -- what the source says
    right_value    jsonb,              -- what the steward said is right
    writeback_id   bigint,             -- the drafted change, when the source takes write-backs
    item_id        bigint,             -- the owner's case, when it doesn't (or a field check)
    state          text not null default 'open' check (state in ('open', 'done', 'returned')),
    outcome        text,
    created_at     timestamptz not null default now(),
    closed_at      timestamptz
);
create index if not exists source_correction_item on source_correction (review_item_id);

-- Structured close-out (P6.1a, #655): what a closed case was, in controlled terms.
create table if not exists case_disposition (
    item_id      bigint primary key,
    disposition  text not null,          -- real | false_alarm | duplicate | data_problem
    failure_mode text,                   -- ISO 14224 codes from standards/iso14224.yaml
    mechanism    text,
    cause        text,
    activity     text,
    outcome      text,                   -- fixed | contained | not_fixed | monitoring
    note         text,
    duplicate_of bigint,
    suggested    jsonb not null default '{}',   -- what the form was pre-filled with
    by           text not null,
    at           timestamptz not null default now()
);

-- Re-check and repeats (P6.1b, #656): did the fix hold, and a problem that came back inside the window.
alter table case_disposition add column if not exists recheck_at timestamptz;
alter table case_disposition add column if not exists recheck_item bigint;      -- the "did the fix hold?" case
alter table case_disposition add column if not exists held boolean;
alter table case_disposition add column if not exists held_note text;
alter table case_disposition add column if not exists held_by text;
alter table case_disposition add column if not exists held_at timestamptz;
create table if not exists case_repeat (
    again      bigint primary key,      -- the case (or re-check) that showed the problem came back
    first      bigint not null,         -- the case reopened as a repeat
    at         timestamptz not null default now()
);

-- Operator rounds (P5.4, ops/rounds.py): one run of each round per shift, for the operator role, and what was read
-- or checked on it. A run past its shift's end is done (every point taken), partly done or missed.
create table if not exists round_run (
    id           bigserial primary key,
    round        text not null,
    shift_start  timestamptz not null,
    shift_end    timestamptz not null,
    role         text not null default 'operator',
    points       integer not null,
    state        text not null default 'open',   -- open | done | partly_done | missed
    taken_by     text,
    closed_at    timestamptz,
    unique (round, shift_start)
);
create index if not exists round_run_open on round_run (state, shift_end);
create table if not exists round_reading (
    id            bigserial primary key,
    run_id        bigint not null references round_run (id),
    point         text not null,                 -- the round point's fuzer id
    value         double precision,
    passed        boolean,                       -- a check: passed or failed; a reading: inside its limits
    note          text,
    by            text not null,
    at            timestamptz not null default now(),
    exception_id  bigint,                        -- what an out-of-limit reading or a failed check raised
    unique (run_id, point)
);

-- The agentic watch (P3.3a, ops/watch.py): what the watch agent proposed for an area over a window, that no detector
-- raised. In shadow (wave 4) a proposal is kept and scored, never routed.
create table if not exists watch_proposal (
    id         bigserial primary key,
    area       text not null,                  -- the process area's fuzer id
    since      timestamp not null,
    until      timestamp not null,
    subject    text,                           -- what it is about, as the evidence names it
    start      timestamp,
    "end"      timestamp,
    why        text not null,                  -- why it is unusual
    ruled_out  text,                           -- what it ruled out
    evidence   jsonb not null default '[]',    -- the ids it cites (points, detections, log entries, notifications)
    cited      boolean not null default false, -- whether every id it cites was in what it was shown
    mode       text not null default 'shadow',
    model      text,
    run_at     timestamptz not null default now()
);
create index if not exists watch_proposal_window on watch_proposal (since, until);
-- A person's verdict on a proposal, and the pattern a confirmed one is read into (P3.4, #951): the role of the point it
-- names, its equipment class and which way the value moved against the week before.
alter table watch_proposal add column if not exists verdict text;          -- confirmed | rejected
alter table watch_proposal add column if not exists verdict_by text;
alter table watch_proposal add column if not exists verdict_at timestamptz;
alter table watch_proposal add column if not exists point_id text;
alter table watch_proposal add column if not exists role text;
alter table watch_proposal add column if not exists class text;
alter table watch_proposal add column if not exists direction text;      -- high | low
alter table watch_proposal add column if not exists shift_z double precision;
alter table watch_proposal add column if not exists minutes integer;
alter table watch_proposal add column if not exists event_kind text;     -- on replay: the injected event it matched
alter table watch_proposal add column if not exists pattern text;        -- <class>:<role>:<direction>

-- A deterministic rule drafted from a pattern of confirmed proposals (P3.4): its spec, its replay score, and who
-- published it. Published rules run as the promoted_rules window detector.
create table if not exists promoted_rule (
    id           bigserial primary key,
    pattern      text not null,
    spec         jsonb not null,                -- role, class, direction, z, minutes, baseline_days
    examples     jsonb not null default '[]',   -- the confirmed proposals it was fitted on
    score        jsonb,                         -- its replay score, when the asset has a fake plant's events
    status       text not null default 'draft', -- draft | published | retired
    drafted_by   text not null,
    drafted_at   timestamptz not null default now(),
    published_by text,
    published_at timestamptz,
    reason       text
);
create unique index if not exists promoted_rule_live on promoted_rule (pattern) where status in ('draft', 'published');
alter table promoted_rule add column if not exists retired_by text;
alter table promoted_rule add column if not exists retired_at timestamptz;
