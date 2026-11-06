import { api } from "../api";
import { href, Status, useAsync } from "../lib";
import { Badge, Button, ButtonLink, Card } from "../ui";

type SettingsResult = Awaited<ReturnType<typeof api.settings>>;
type SettingsLoad = { data?: SettingsResult; loading: boolean; error?: string; reload: () => void };

export function SetupReadiness({ list }: { list: SettingsLoad }) {
  const catalog = useAsync(() => api.catalog(), []);
  const jobs = useAsync(() => api.jobsHealth(), []);
  const config = (filter?: string) => href("/settings", {
    view: "configuration", project: list.data?.project ?? undefined, filter,
  });
  const rows = list.data?.settings ?? [];
  const deployment = list.data?.deployment;
  const connections = (catalog.data?.systems ?? []).flatMap((s) =>
    s.connections.map((c) => ({ ...c, system: s.name, systemEnabled: s.enabled })));
  return <div>
    <h2>Setup &amp; readiness</h2>
    <p>Follow these steps to prepare the shared facility. Viewing this page reads configuration and recorded
      diagnostics only: it does not test connections, load data, restart services or call AI.</p>
    <Button disabled={list.loading || catalog.loading || jobs.loading} onClick={() => {
      list.reload(); catalog.reload(); jobs.reload();
    }}>Refresh readiness</Button>
    <Status loading={list.loading && !list.data} error={list.error} />
    <p className="muted small">Scope: {list.data?.project ?? "global"}. These are configured values, not a snapshot of
      every running worker. Refresh after making changes; no restart-completion tracking is available.</p>
    <Card title="1. Check deployment and sign-in">
      {deployment ? <>
        <Badge>{deployment.mode === "local" ? "Trusted local development" : deployment.configured ? "Microsoft configuration present" : "Sign-in configuration incomplete"}</Badge>
        <p>{deployment.mode === "local" ? "Attribution-only mode is limited to loopback development, not shared hosting."
          : deployment.configured ? "Required sign-in references are present. This does not verify a real Microsoft login or role restrictions."
          : "Microsoft configuration is incomplete. Ask the deployment owner to configure sign-in in hosting."}</p>
        {deployment.bootstrap_present && <p role="status">Attention: the owner bootstrap secret is still present.
          After establishing an owner, remove FUZER_OWNER_BOOTSTRAP_TOKEN from hosting and apply the hosting change.</p>}
      </> : !list.loading && !list.error && <p>Deployment readiness is unavailable. Refresh or ask the deployment owner to check the server version.</p>}
      <p>Database location, public origin, Microsoft registration and secret values belong in deployment-managed
        configuration. Fuzer cannot manage Render credentials or restart hosting from this screen.</p>
      <ButtonLink href="https://areed145.github.io/fuzer/deployment/" target="_blank" rel="noreferrer">Deployment instructions ↗</ButtonLink>
    </Card>
    <Card title="2. Connect and load source data">
      <Status loading={catalog.loading && !catalog.data} error={catalog.error} />
      {catalog.data && <>
        <p>{connections.length} connections · {catalog.data.systems.reduce((n, s) =>
          n + s.datasets.filter((d) => d.loaded_at).length, 0)} datasets with a recorded load.
          A successful connection test does not mean data has been loaded or is current.</p>
        {connections.length === 0 && <p>No connections yet. Add a source using the guided connection, mapping and loading steps.</p>}
        <ul>{connections.map((c) => <li key={c.id}>
          <strong>{c.system} / {c.name}</strong>: {!c.enabled || !c.systemEnabled ? "disabled"
            : Object.values(c.secrets).some((s) => !s.resolves) ? "missing secret reference"
            : !c.last_test ? "not tested" : c.last_test.ok ? "last test passed" : "last test failed"}
          {c.last_tested_at && <> · recorded {c.last_tested_at}</>}
        </li>)}</ul>
      </>}
      <p>Run a deliberate test in Systems &amp; connections. Tests contact the configured service, may read sample
        rows and save the result; they do not ingest or call AI. Loads and rebuilds are separate actions in Sources.</p>
      <ButtonLink href={href("/sources", { view: "systems" })}>Review connections &amp; test</ButtonLink>{" "}
      <ButtonLink href={href("/sources", { view: "add" })}>Add a source</ButtonLink>{" "}
      <ButtonLink href={href("/sources")}>Review loads &amp; freshness</ButtonLink>
    </Card>
    <Card title="3. Check scheduled work">
      <Status loading={jobs.loading && !jobs.data} error={jobs.error} />
      {jobs.data && <p>{jobs.data.summary || "No scheduler diagnosis is available."}</p>}
      <p>Jobs is the source of truth for scheduler heartbeats, checks and run failures. Configuration does not prove a
        daemon is running. A deployment operator must start or repair missing infrastructure.</p>
      <ButtonLink href={href("/jobs")}>Inspect jobs &amp; recovery</ButtonLink>
    </Card>
    <Card title="4. Choose optional AI and review limits">
      <p>The Okan guided investigation works without AI. Credential presence below does not verify reachability,
        model access, account balance or a successful paid call. No paid probe is run here.</p>
      <ul>{["llm", "agents"].map((prefix) => {
        const provider = rows.find((s) => s.key === `${prefix}.provider`);
        const key = rows.find((s) => s.key === `${prefix}.api_key`);
        return <li key={prefix}>{prefix === "llm" ? "Readers" : "Assistant"}: {String(provider?.value ?? "unknown")} · {
          !key ? "credential status unavailable" : key.resolves ? "secret reference resolves" : "secret reference missing"
        } · <a href={config(`${prefix}.`)}>Configure {prefix === "llm" ? "readers" : "assistant"}</a></li>;
      })}</ul>
      <p>Keep secret values in hosting; enter only env:VARIABLE_NAME references in configuration. Local or
        managed-identity providers may not need an API key. Grant AI permission only to intended users.
        Application concurrency/token/run limits are not an account-wide spending ceiling: set provider-side budgets too.</p>
      <ButtonLink href={config("assistant.")}>Assistant limits</ButtonLink>{" "}
      <ButtonLink href={config("max_usd")}>Scheduled AI budgets</ButtonLink>
    </Card>
    <Card title="5. Apply changes and confirm the outcome">
      <p><strong>Next use:</strong> settings without a restart flag apply on subsequent reads after cache refresh;
        they do not rerun existing work. <strong>Restart:</strong> flagged settings require the affected application
        or scheduler process to restart. Saving is not confirmation that the running process has adopted a value.</p>
      <p>Source connection changes apply to the next test/load. Mapping changes require the load/rebuild actions
        shown in Sources. Changing an environment-backed secret requires applying the hosting change as well.</p>
      <ButtonLink href={config()}>Open all configuration</ButtonLink>{" "}
      <ButtonLink href={href("/")}>Return Home</ButtonLink>
    </Card>
  </div>;
}
