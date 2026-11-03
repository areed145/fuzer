# Getting started

From a clean checkout to the Okan demo running locally, then the evals.

## 1. Install

You need [uv](https://docs.astral.sh/uv/) and Node.js (for the web UI). Docker is optional.

```sh
git clone https://github.com/areed145/fuzer && cd fuzer
uv sync
cp .env.example .env      # put your Anthropic key in ANTHROPIC_API_KEY
```

Without a key everything runs except Claude's reading and the operations assistant.

**Database.** Leave `FUZER_DATABASE_URL` unset and Fuzer runs a built-in Postgres in `.fuzer/pgdata`. To use
your own, `docker compose up -d` starts one on localhost:5432; then set
`FUZER_DATABASE_URL=postgresql://fuzer:fuzer@localhost:5432/fuzer`.

## 2. Run the Okan demo

```sh
uv run fuzer up           # then open http://127.0.0.1:8000
```

The first run takes a while. It builds the fake Okan field, loads every source, and has Claude read the operator
logs, P&ID drawings and document sections (this costs money). Then it builds the register and the web UI and starts
the server. Later starts reload or re-read only what changed. `uv run fuzer up --fresh` starts over with a clean
database and keeps Claude's readings.

Things to try:

- **Browse** and **Search**: find `20-K-2300A` or the well `OK-J12-02` and follow the links.
- **Ask**: the operations assistant. Try "Why does lift gas keep running short?"
- **Board**, **Work** and **Reports**: what the scheduled loop finds and drafts.
- **Architecture**: the components and what each holds now.

The planted stories to find are listed in the [README](https://github.com/areed145/fuzer#okan-field-demo).

## 3. Run the tests

```sh
uv run pytest -m unit                     # a few seconds, no database
uv run pytest -n auto --dist loadfile     # unit and integration, about 2 minutes on 4 cores
uv run pytest -m e2e                      # week-long replays and evals: slow
```

The tests never call Claude: agents and readers run on recorded answers.

## 4. Run the evals

```sh
uv run fuzer eval                                    # the register against the answer keys
uv run fuzer --project okan eval --ops --full        # the scheduled loop on a replayed week (scripted Claude)
uv run fuzer --project okan eval --agents            # the assistant on the answer-key questions (costs money)
```

[Evals and quality](evals.md) explains each one. The [assistant eval](assistant-eval.md) has the latest results.

## 5. Read on

- [Overview](overview.md) and [architecture](architecture.md) for the big picture.
- [From a well test to an answer](walkthrough.md) follows one record through the system.
- The [glossary](glossary.md) explains the terms used here.
- To change thresholds, models or schedules, see [settings](settings.md); every command is in the
  [command line](cli.md) page.
