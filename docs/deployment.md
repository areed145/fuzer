# Deployment and settings

Running Fuzer, its configuration and how it performs at size.

## Configuration

`.env` holds only the database, the project and secrets. Everything else is a setting: change it on the Settings page or with `fuzer settings set <key> <value>`. A project's value beats the global one, which beats the default. [Settings](settings.md) lists them all.

Reference: [command line](cli.md), [database schema](schema.md).

## Render

`render.yaml` configures the **existing Okan test deployment**, keeping the
`fuzer` web service and `fuzer-db` database names. Sync that Blueprint rather
than creating a second service/database. The default `Dockerfile` builds the
React UI and generates all synthetic Okan exports, drawings, documents and the
process model **inside the image**. The data therefore survives container
replacement; generating files only in a Render shell does not.

The service uses a paid 2 GB plan for the API, Dagster webserver/daemon and agent
worker, and a 1 GB persistent disk at `/var/lib/fuzer`. Review billing before
syncing. It is intentionally **single-instance**: all processes share
`DAGSTER_HOME=/var/lib/fuzer/dagster` (Dagster's SQLite run/event/schedule storage)
on this disk. The Jobs page reads that same instance, including daemon heartbeats.
Dagster's port 3000 listens on loopback only; Render exposes only the API on
`$PORT`. The supervisor logs child output to Render, propagates shutdown, and
exits if any required child dies so Render restarts the whole runtime.
Persistent disks imply downtime during deploys; do not scale this configuration
to multiple replicas. Splitting services later requires shared Dagster database
storage and code-location configuration, not merely `FUZER_DAGSTER_URL`.

### Initial load and upgrades

Startup applies schema migrations, then loads Okan if the register is empty,
even when the API has already created its tables. A database marker and advisory
lock make interrupted loads retryable and completed loads one-time. A built Okan
register is preserved, including its settings and reviewer decisions. Other
assets or unidentified pre-existing source data cause a clear startup failure:
there is **no automatic reset**, on first boot or on subsequent redeploys.
Zero-record ingestion is an error, not a successful bootstrap.

For an existing dashboard-managed service, remove any custom Docker Command
override so the image runs `python -m fuzer.cli.deploy`, attach the persistent
disk, and set these environment values (or sync the Blueprint):

| Variable | Value |
|---|---|
| `FUZER_DATABASE_URL` | Existing `fuzer-db` internal connection string (injected by Blueprint) |
| `FUZER_PACK` | `okan` |
| `FUZER_DATA_DIR` | `/app/fakeplant/data/okan` |
| `FUZER_TIMESERIES` | `synthetic` |
| `DAGSTER_HOME` | `/var/lib/fuzer/dagster` |

Remove stale `FUZER_PROJECT` or other asset-specific overrides. Deploy the merged
revision from the branch Render actually tracks. Once it starts, `/health`
should report `{"ok":true,"pack":"okan"}` and `/stats` should have nonzero records
and entities. The logs should show `Okan sample loaded` on the first empty load
and `already initialized` on restart. Jobs should show a live daemon. Source
timestamps are historic demo data, so freshness warnings are not live plant
outages. Do not use `fakeplant demo`, `fuzer reset` or `up --fresh` as a startup
command. If a test database truly needs wiping, verify its Render identity and
reset it explicitly once, outside normal startup.

### Credentials and scheduled costs

Set `ANTHROPIC_API_KEY` as a **secret environment variable** on the Render service
and redeploy. The Blueprint prompts for it (`sync: false`) but does not store its
value in Git. Alternatively, create a Render environment group such as
`fuzer-secrets`, add the key there, and link the group to this service; avoid a
duplicate service-level key overriding the group's value. All children inherit
the same environment. Without a key, the API and Dagster start, but Ask is
unavailable and the agent worker is explicitly not started.

Credentials are not encrypted DB values: source connections and secret settings
store **references**, such as `env:FUZER_PI_PASSWORD`, `env:FUZER_ADX_TOKEN` and
`env:FUZER_ALERT_WEBHOOK`. Configure actual values in Render's secret environment
or linked group; do not paste them into SQL, source YAML or committed `.env`
files. Sources -> connections shows `resolves` and supports connection tests.
Settings edits models, limits, schedules and secret references, not the Anthropic
key itself. Local reference/configuration rows can be recreated on Render, but
local `.env` values do not transfer with the database. Synthetic Okan needs no
PI or ADX credentials.

Dagster starts the repository's default schedules and sensors, and the worker
runs workflow agent steps against the same Postgres. With a key, scheduled
Claude reading/diagnosis/reporting can incur charges. Review caps/models in
Settings and schedules in Jobs before leaving it running. Initial ingestion
does not block on Claude reads; the scheduled readers enrich cached readings
later. This is fictional test data, not a live facility.

**Access control:** the application does not provide authentication for this
deployment. The UI and API include write actions, and an API key would enable
chargeable assistant requests. Restrict access outside the application before
sharing it; do not use this sample for sensitive data. Repository changes and a
merge do not themselves verify a live Render deployment.

## Performance

Okan is the only supported synthetic project and test fixture. Use
`uv run fuzer bench` to measure the current build on your environment.
