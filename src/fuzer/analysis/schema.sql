-- Operating limits per PI point (#101): alarm setpoints (PI AF limit traits, DCS alarm exports) and
-- integrity operating windows (IOW registers), dated, with the source record they came from. Rebuilt
-- from source records with the graph; values are in the units of units.yaml.
create table if not exists point_limit (
    record          text not null,              -- source record: <source>:<record_type>:<key>[#n]
    limit_type      text not null,              -- hihi | hi | lo | lolo
    point_id        text not null,              -- the timeseries entity
    source          text not null,              -- pi_af | dcs | iow | ...
    basis           text not null,              -- alarm | iow
    value           double precision not null,
    unit            text,
    reported        text,                       -- as the source gave it, when converted
    level           text,                       -- IOW class (critical / standard / informational) or alarm priority
    allowed_minutes double precision,           -- IOW: time allowed outside before it must be acted on
    enabled         boolean not null default true,
    valid_from      timestamp,
    valid_to        timestamp,
    note            text,
    primary key (record, limit_type)
);
create index if not exists point_limit_of on point_limit (point_id);

-- Normal ranges learned from history (#101) per point and operating mode of its unit: used for points
-- with no limits from a source, and to check the ones that have them. Written by `fuzer limits learn`.
create table if not exists point_normal (
    point_id    text not null,
    mode        text not null,                  -- running | startup | stopped | transition | any
    p01         double precision not null,
    p50         double precision not null,
    p99         double precision not null,
    n           integer not null,
    share       double precision not null,      -- of the learning window spent in this mode
    since       timestamp not null,
    until       timestamp not null,
    learned_at  timestamptz not null default now(),
    primary key (point_id, mode)
);

-- Times a point was outside a limit while its unit was running (#101): what the SRE (#96) raises, and
-- for IOWs past their allowed time, the integrity work item opened for them (#97).
create table if not exists excursion (
    id               bigserial primary key,
    point_id         text not null,
    record           text not null,             -- the limit's record ('learned' for learned ranges)
    limit_type       text not null,
    basis            text not null,             -- alarm | iow | learned
    level            text,
    limit_value      double precision not null,
    start            timestamp not null,
    "end"            timestamp,                 -- null while still outside at the end of the scan
    peak             double precision not null,
    minutes_outside  double precision not null,
    allowed_minutes  double precision,
    work_item_id     bigint,
    found_at         timestamptz not null default now(),
    unique (point_id, record, limit_type, start)
);
create index if not exists excursion_open on excursion (start desc);

-- Debottleneck options (#107): ranked proposals against back-pressure, kept from the last scan so the
-- work board (#99) and the value score (#100) can read them without re-running the hydraulics.
create table if not exists debottleneck_option (
    id         bigserial primary key,
    scanned_at timestamptz not null default now(),
    as_of      text not null,
    rank       integer not null,
    kind       text not null,
    target     text,
    action     text not null,
    gain_sm3d  double precision not null,
    detail     jsonb not null
);
