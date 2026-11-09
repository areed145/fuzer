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
