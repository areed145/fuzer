-- Charts the operations assistant made (plots/): the Plotly figure as built, what the agent was told
-- about it, and the records it came from. A figure is a snapshot of the data when it was asked.
create table if not exists assistant_figure (
    id          text primary key,
    kind        text not null,
    title       text not null,
    params      jsonb not null default '{}',
    spec        jsonb not null,
    summary     jsonb not null default '{}',
    sources     text[] not null default '{}',
    by_source   text not null default 'fuzer',  -- 'assistant' when the agent supplied the numbers
    created_at  timestamptz not null default now()
);
