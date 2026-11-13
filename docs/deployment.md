# Deployment and settings

## Guided setup and readiness

Open **Home -> Source readiness -> Sources** with configure permission to connect, test,
ingest, monitor freshness and recover failed loads. The existing dataset list owns readiness;
Systems & connections owns deliberate tests, and Jobs owns scheduler/run diagnostics.
**Settings -> Readiness** owns deployment/sign-in references, application defaults,
optional AI/limits and restart requirements. Home links to these destinations without duplicating
configuration. Refresh reads existing configuration and
diagnostics only; it does not call AI, test credentials, load data or restart processes.
Readers retain browser Appearance settings but cannot retrieve facility configuration.

**Configuration** is the existing audited settings editor, with effective project/global/legacy
environment/default provenance. Legacy source-seeding settings remain compatible for old integrations,
but their duplicate UI editors are retired: use **Sources -> Systems & connections** for endpoints
and secret references. Deliberate connection tests contact the service, may read sample rows and
store their outcome; a test is not a load or freshness check. Editing a connection invalidates its
previous test. Changing a deployment secret also requires retesting, since Fuzer cannot observe
external credential rotation.

Settings marked **next use** apply to subsequent reads after cache refresh, not already-running
work. **Restart** settings require the affected API/agent or scheduler process to restart.
This screen does not track applied runtime generations or offer hosting restart/secret management.
Public origin, Microsoft app registration, database connectivity and secret values remain deployment
operator responsibilities. Credential presence does not verify login, provider reachability, model
access or a spending ceiling. Keep provider-side budgets as well as application limits.

The browser admits at most two API requests at a time and shares identical in-flight read-only
requests within the same session. Waiting is bounded (32 requests, 30 seconds); overflow or timeout
is shown as an actionable error. Server admission remains four concurrent requests by default,
shared across tabs/visitors, and rejects overload with 429 and Retry-After. Requests are never
automatically replayed: especially do not retry mutations or paid calls after an uncertain result.

Running Fuzer, its configuration and how it performs at size.

## Configuration

`.env` holds the database, project, secrets and deployment trust/bootstrap configuration
(Microsoft application registration and the canonical public URL). Operational tunables are
settings: change them on the Settings page or with `fuzer settings set <key> <value>`.
A project's value beats the global one, which beats the default. [Settings](settings.md) lists them all.

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
syncing. It is intentionally **single-instance**. Every start writes
`DAGSTER_HOME=/var/lib/fuzer/dagster/dagster.yaml` so Dagster's run, event and
schedule storage lives in `fuzer-db` (schema `dagster`), not in SQLite on the disk:
the API, webserver, daemon and every run process share it, and SQLite on a small
network disk failed under them with `disk I/O error`. Only run output (compute
logs, pruned after 14 days) stays on the disk, and the first start on this layout
deletes the old SQLite files and the run history they held. Sensor and schedule
ticks are purged after 14 days (skipped sensor ticks after 3). The Jobs page reads
that same instance, including daemon heartbeats.
Dagster's port 3000 listens on loopback only; Render exposes only the API on
`$PORT`. The supervisor logs child output to Render, propagates shutdown, and
exits if any required child dies so Render restarts the whole runtime.
The Jobs page only offers an external Dagster link when `FUZER_DAGSTER_URL` is
configured with a browser-accessible URL; it never exposes the server's loopback
address to remote users.
Persistent disks imply downtime during deploys; do not scale this configuration
to multiple replicas. Splitting services later requires shared Dagster database
storage and code-location configuration, not merely `FUZER_DAGSTER_URL`.

### Initial load and upgrades

Startup applies schema migrations, then loads Okan if the register is empty,
even when the API has already created its tables. A database marker and advisory
lock make interrupted loads retryable and completed loads one-time. A built Okan
register keeps its settings and reviewer decisions, and every deploy brings it up
to date: startup registers new built-in mappings, never over a person's edit,
loads exports that changed in the image, and rebuilds if anything did. This runs
before the API starts, so a deploy that changes data takes longer to come up. A
failure here is logged as `Data refresh failed` and the service starts on the
register it has; it isn't retried, so inspect Sources, then redeploy or run
`fuzer ingest` deliberately. A new image can change document text, and with a key
the startup readers then pay to read those sections. Other
assets or unidentified pre-existing source data cause a clear startup failure:
there is **no automatic reset**, on first boot or on subsequent redeploys.
Zero-record ingestion is an error, not a successful bootstrap.

For an existing dashboard-managed service, remove any custom Docker Command
override so the image runs `python -m fuzer.cli.deploy --bundle /app/fakeplant/bundles/okan`, attach the persistent
disk, and set these environment values (or sync the Blueprint):

| Variable | Value |
|---|---|
| `FUZER_DATABASE_URL` | Existing `fuzer-db` internal connection string (injected by Blueprint) |
| `DAGSTER_HOME` | `/var/lib/fuzer/dagster` |
| `FUZER_GITHUB_ISSUES_TOKEN` | Optional: a fine-grained GitHub token for this repository with Issues: read and write. With it, a crash in any process (supervisor, API, workers, Dagster), a 500 and a failed Dagster run open an `auto-report` issue, deduplicated by fingerprint (`fuzer.core.bugreports`) |
| `FUZER_GITHUB_ISSUES_REPO` | The repository those issues go to, as `owner/name` |

### Jobs on their own service (optional)

By default one service runs everything (`FUZER_ROLE` unset, or `all`), so every scheduled run shares the web
service's CPU with page requests. To keep them apart, run the same image twice against the same database:

| Service | `FUZER_ROLE` | Runs |
|---|---|---|
| Web service | `web` | The API and the Dagster webserver behind "Open in Dagster" |
| Background worker | `worker` | The Dagster daemon, every run it starts, the agent workers and the startup readings |

Both need `FUZER_DATABASE_URL` and the same provider keys. Dagster's run, event and schedule storage is in
Postgres, so the Jobs page and run history work from either service. Run output (compute logs) is written where the
run happened, on the worker; give the worker the persistent disk if those logs should survive a deploy. Both
services seed at start, one after the other (an advisory lock).

Remove stale `FUZER_PROJECT`, `FUZER_PACK`, `FUZER_DATA_DIR`, `FUZER_TIMESERIES` or other asset-specific
overrides: each deploy registers the `--bundle` facility as the database's first project from its seed bundle,
and its pack, exports folder and time-series provider come from the project (P1.6). A leftover
`FUZER_DATA_DIR` still overrides the first project's folder, with a deprecation warning in the log. Deploy the merged
revision from the branch Render actually tracks. Once it starts, `/health`
should report `{"ok":true,"pack":"okan"}` and, after approved sign-in, `/stats` should have nonzero records
and entities. The logs should show `OKAN sample loaded` on the first empty load,
and on later starts `already initialized` followed by `Data refreshed` or `Data unchanged`. Jobs should show a live daemon. Source
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
Settings and schedules in Jobs before leaving it running. After loading, startup
reads uncached logs, drawings, document sections and datasheets for each project
in the background, then rebuilds with the results. This can incur charges on the
first start with a configured provider; unchanged cached inputs are not read
again on redeploy. Without a configured provider, startup reports what remains
unread and makes no model calls. Cached readings left unapplied by an interrupted
start are rebuilt without reading them again. Reader failures are logged, not
retried automatically; inspect recorded readings before a deliberate retry.
The API does not wait for these reads. This is fictional test data, not a live facility.

### Microsoft sign-in and first owner

The default mode is **fail-closed Microsoft sign-in**. A missing application registration
leaves the login page available with an explanatory setup message, but blocks data and
execution APIs. `/health` is public and is not an authentication-readiness check.

The deployment owner performs this setup once; visitors need no API key or CLI:

1. Register a **Web** application in Microsoft Entra, choosing **Accounts in any organizational
   directory and personal Microsoft accounts** (`AzureADandPersonalMicrosoftAccount`).
   Fuzer uses the `common` authority, authorization code flow, PKCE and nonce/state validation
   through MSAL. It requests sign-in scopes only, not Microsoft Graph access.
   Registering the app requires access to an Entra tenant; this does not mean users need a
   corporate Microsoft account. Check Microsoft's current tenant-creation eligibility if
   you do not already have a tenant.
2. Set `FUZER_PUBLIC_URL` to the exact HTTPS origin of the existing Render service, with no
   trailing path. Register that origin plus `/auth/callback` as the Web redirect URI.
   Set `FUZER_MICROSOFT_CLIENT_ID` to the application ID and
   `FUZER_MICROSOFT_CLIENT_SECRET` to the secret **value**, in Render's secret settings.
   Do not enable implicit grants or put credentials in the repository/browser.
3. Keep `FUZER_AUTH_MODE=microsoft`. The Blueprint generates a random
   `FUZER_OWNER_BOOTSTRAP_TOKEN`; for an existing dashboard-managed service, configure
   a cryptographically random secret at least 32 characters long. Never share it in chat.
4. Sign in with the Microsoft account that should own Fuzer. On the approval page, enter
   the owner setup code from Render. Only someone who both signs in and possesses that
   secret can become the initial owner. The transaction refuses bootstrap once any owner
   exists. Remove the bootstrap secret after setup.
5. In **Settings -> Users -> Accounts & access**, approve additional accounts after their first sign-in and
   assign application roles. Add a second trusted owner for recovery. No account is
   automatically approved based on email, name, domain, or being the first visitor.
   Microsoft controls password/passkey/Authenticator behavior; Fuzer does not collect
   passwords or require a particular Microsoft MFA policy.

### Demo account (optional)

For demonstrations and automated checks of a live deployment (for example a Claude
session testing the site), Fuzer has one non-Microsoft account, **Demo account**
(audit actor `user:demo`). It is off unless the deployment holds `FUZER_DEMO_TOKEN`,
a cryptographically random secret of at least 32 characters, set in Render's secret
settings and never in the repository or chat. Microsoft stays the sign-in for people.

- `POST /auth/demo` with `{"token": "<FUZER_DEMO_TOKEN>"}` returns a normal session
  cookie and CSRF value; a wrong token is refused and leaves no account or session.
  `scripts/demo_login.py` does this and saves a Playwright storage state for screenshots.
- The first sign-in creates the account enabled with the `operator` role in the
  projects named by `FUZER_DEMO_PROJECTS` (comma-separated; default every project the
  database holds, which on the demo deployment are Okan, Kestrel and Harrier).
  It cannot open other projects unless an owner grants them.
- It can never be an owner or hold `configure`, whatever roles an owner gives it, so
  it cannot manage users, settings, sources or routing. It gets no `ai` permission
  unless an owner grants it `ai_user` on Users.
- Every sign-in and action is recorded as `user:demo`. Its sessions last at most
  12 hours. An owner can disable it on Users; removing `FUZER_DEMO_TOKEN` ends its
  live sessions as well as further sign-ins.

Accounts are bound to Microsoft's validated tenant ID and object ID, not mutable email
addresses or display names. An owner has every application permission and all current
and future **human** workflow roles, without impersonating an automated agent.
Application roles (`reader`, `operator`, `administrator`, `ai_user`, plus owner-defined
roles) are separate from extensible routing roles in **Users -> Workflow assignments & routing**. New application
roles combine registered permissions; new permission types require an explicit server
policy and tests. Only owners can grant application access or create owners; the last
enabled owner cannot be removed. Disabling an account revokes its sessions.
Configuration administrators may manage workflow routing without granting application access.
Assign human workflow roles directly from the signed-in account using its stable roster identity;
legacy names remain separate and must not be matched by display name. Disabling an account preserves
saved assignments but removes effective workflow access; use workload/coverage for deliberate handoff.
Work context means presentation focus, never a permission or routing change. Context selection and
Home personalization are deferred; there is no separate editable persona registry.

Signed-in approved users operate the **shared** synthetic facility, not isolated per-user
plant copies. Assistant conversations, uploaded images, questions and answers are scoped
to their creator, including continuation, retrieval and deletion. Deliberately learned
facility memory (including **Yes & share learning** ratings), workflow history and charts remain shared product evidence; do not
use this demonstrator for sensitive information.

Server checks cover all data/execution routes, including administrative GET previews,
provider-backed previews, mutations and workflow-role checks. Audit actors come from
the server session, not a browser-supplied `by`. Sessions use hashed opaque tokens in
Postgres with absolute expiry, Secure/HttpOnly/SameSite cookies, revocation and CSRF
confirmation for mutations and paid GET previews. No provider tokens are persisted.

**Bounds:** the single-instance API limits simultaneous authenticated requests and
32 MB request bodies (the existing four 5 MB images plus base64/JSON overhead).
HTTP route database connections have a 30-second statement timeout. Settings control API concurrency,
assistant question concurrency/time limits and session lifetime. Assistant input/output
and reader token limits remain in force; scheduled diagnosis retains its existing spend
caps. AI permission permits charges, not unlimited anonymous access. These controls are
resource bounds, **not a hard cumulative dollar budget**; use provider-side spend limits
for an account-wide ceiling, including scheduled/background work.

Remote HTTP MCP is intentionally refused in Microsoft mode: no second unauthenticated
execution surface is published. Trusted in-process agents and local stdio MCP keep working.
HTTP MCP is restricted to explicit local mode and loopback; supporting remote authenticated
MCP later requires its own validated authorization design, not an open bind.

For trusted development only, explicitly set `FUZER_AUTH_MODE=local` and bind to loopback.
That mode retains attribution-only names, rejects non-loopback HTTP requests and is
refused on Render. It is not a production fallback. Public API documentation endpoints
are disabled; reference documentation is shipped separately.

Repository changes, opening a PR and merging do not themselves verify a live Render
deployment. Before sharing a deployed revision, verify fresh-session login and approval,
owner administration, desktop/mobile Okan navigation, and rejected unauthorized writes.

## Backup and restore

Everything a shift would miss lives in `fuzer-db`:

- every project's schema (`public` for the first project, `proj_<name>` for the others), with its register, cases, work, decisions and readings;
- the shared `fuzer` schema, with accounts, grants, settings and access events;
- the `dagster` schema, with run history and schedules.

The persistent disk holds only Dagster run output, which is pruned after 14 days. The synthetic exports come with the image. So a recovery is a database recovery.

### What Render keeps

| What | Kept | How it is restored |
|---|---|---|
| Point-in-time recovery of `fuzer-db` | Last 3 days on a Hobby workspace, 7 on Pro or higher. None on the Free database plan. | Render creates a **new** database as it was at a time you pick, at least 10 minutes ago. The original is left as it is. |
| Logical exports of `fuzer-db` | 7 days. Made on demand from the database's Recovery page, never automatically. | `pg_restore` into an **empty** database. |
| Persistent disk snapshots | Daily, kept 7 days. | Whole-disk restore from the service's Disk page. It is not needed for Fuzer data. |

Nothing is copied off Render. A copy older than 7 days, or one that survives losing the Render account, would need a scheduled `pg_dump` to outside storage. That is a paid service and isn't set up.

### What a restore loses

A restore loses everything written after the restore point:

- decisions, case notes and closed work;
- settings changes, sign-ins and grant changes;
- Dagster run history.

Before you retire the original database, export its audit record since the restore point. That list is what the shift has to redo:

```bash
FUZER_DATABASE_URL='<original database external URL>' \
  uv run fuzer audit export --since <restore point, UTC> --format csv --out lost-since-restore.csv
```

Rows marked `latest only` show a record's latest state, not every change to it ([the audit record](ot-security.md)).

### Restoring at a shift-safe time

1. **Agree a window with the IOC lead.** Use a gap between shifts. Tell the outgoing shift that the board is about to go stale, and tell them when to stop recording decisions.
2. **Pick the restore point.** Choose the last time before the bad deploy, bad import or lost data. Render's Events tab and the audit record of the current database show when that happened.
3. **Create the recovery database.** In Render, open `fuzer-db`, then Recovery, then Restore, and choose the point. Keep the existing settings so the plan and allow list match.
4. **Check the recovery database before switching.** Run the commands below against its external URL. `fuzer projects list` should name every project. The audit record should end just before the restore point:

   ```bash
   FUZER_DATABASE_URL='<recovery database external URL>' uv run fuzer projects list
   FUZER_DATABASE_URL='<recovery database external URL>' \
     uv run fuzer audit export --since <restore point minus a day> --format csv | tail -5
   ```

5. **Export what will be lost** from the original database (the command above).
6. **Switch the service.** Set `FUZER_DATABASE_URL` on the `fuzer` web service to the recovery database's *internal* URL, then redeploy. `render.yaml` reads the URL from the database named `fuzer-db`. Until `fromDatabase.name` is changed to the recovery database in a merged PR, a Blueprint sync points the service back at the original. Don't sync in between.
7. **Confirm the service is back.** The startup log should show `already initialized`, not a fresh load. Then check `/health`, sign-in, and the Board for the last decision before the restore point.
8. **Hand over.** Give the shift `lost-since-restore.csv` to redo. Suspend the original database, and delete it once the shift has finished with it.

To restore from a logical export instead, create an empty Postgres 16 database and run `pg_restore --no-owner -d '<its external URL>' <export folder>`. Then carry on from step 4. Never restore an export into a database that already holds Fuzer data.

### Rehearsal

A local rehearsal on 13 Nov 2026 used Postgres 16 and the Okan register (117 tables in `public`, 286,126 rows):

1. Recorded a settings change as `user:ioc-lead`.
2. Took a `pg_dump -Fd` backup (5.2 MB, under a second).
3. Recorded a second change.
4. Restored into an empty database with `pg_restore --no-owner` (about a second).

Row counts in `public` matched. The restored `fuzer` schema had every row from before the backup and none from after it. On the restored database, `fuzer audit export` showed the first change and not the second. On the original, the same export over the window since the backup listed exactly the second change, which is the one a restore would lose. `fuzer projects list` worked on the restored database.

A rehearsal of point-in-time recovery on Render itself hasn't been run. The recovery database is billed while it exists.

## Performance

Okan is the only supported synthetic project and test fixture. Use
`uv run fuzer bench` to measure the current build on your environment.
