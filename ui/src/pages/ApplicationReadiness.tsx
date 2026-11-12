import { api } from "../api";
import { href, Status } from "../lib";
import { Badge, Button, ButtonLink, Card } from "../ui";

type SettingsResult = Awaited<ReturnType<typeof api.settings>>;
type SettingsLoad = { data?: SettingsResult; loading: boolean; error?: string; reload: () => void };

export function ApplicationReadiness({ list }: { list: SettingsLoad }) {
  const config = (filter?: string) => href("/settings", {
    view: "configuration", project: list.data?.project ?? undefined, filter,
  });
  const rows = list.data?.settings ?? [];
  const deployment = list.data?.deployment;
  return <div>
    <h2>Application readiness</h2>
    <p>Application-wide defaults, sign-in references and optional AI. For connecting, testing, loading and
      recovering source data, open <a href={href("/sources")}>Sources</a>. Viewing this page makes no changes or AI calls.</p>
    <Button disabled={list.loading} onClick={list.reload}>Refresh configuration</Button>
    <Status loading={list.loading && !list.data} error={list.error} />
    <p className="small">Scope: {list.data?.project ?? "global"}. Configured values; refresh after a change.</p>
    <Card title="Deployment and sign-in">
      {deployment ? <>
        <Badge>{deployment.mode === "local" ? "Trusted local development" : deployment.configured ? "Microsoft configuration present" : "Sign-in configuration incomplete"}</Badge>
        <p>{deployment.mode === "local" ? "Attribution-only mode is limited to loopback development, not shared hosting."
          : deployment.configured ? "Required sign-in references are present. A real Microsoft sign-in is the test of them."
          : "Microsoft configuration is incomplete. Ask the deployment owner to configure sign-in in hosting."}</p>
        {deployment.bootstrap_present && <p role="status">Attention: the owner bootstrap secret is still present.
          After establishing an owner, remove FUZER_OWNER_BOOTSTRAP_TOKEN from hosting and apply the hosting change.</p>}
      </> : !list.loading && !list.error && <p>Deployment readiness is unavailable. Refresh or ask the deployment owner to check the server version.</p>}
      <p>Database location, public origin, Microsoft registration and secret values belong in deployment-managed
        configuration. Fuzer cannot manage Render credentials or restart hosting from this screen.</p>
      <ButtonLink href="https://areed145.github.io/fuzer/deployment/" target="_blank" rel="noreferrer">Deployment instructions ↗</ButtonLink>
    </Card>
    <Card title="Optional AI and application limits">
      <p>The Okan guided investigation works without AI. A present credential is checked only by a real paid call,
        which this page never makes.</p>
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
    <Card title="Demo onboarding">
      <p>The Demo tab is {rows.find((s) => s.key === "demo.enabled")?.value === true ? "enabled" : "disabled or unavailable"}.
        Administrators enable or hide it application-wide. AI, workflow and configuration access stay as they are.</p>
      <ButtonLink href={config("demo.enabled")}>Configure Demo</ButtonLink>
    </Card>
    <Card title="Apply changes and restart requirements">
      <p><strong>Next use:</strong> settings without a restart flag apply on subsequent reads after cache refresh;
        they do not rerun existing work. <strong>Restart:</strong> flagged settings require the affected application
        or scheduler process to restart. Saving is not confirmation that the running process has adopted a value.</p>
      <p>Changing an environment-backed secret requires applying the hosting change as well.</p>
      <ButtonLink href={config()}>Open all configuration</ButtonLink>{" "}
      <ButtonLink href={href("/")}>Return Home</ButtonLink>
    </Card>
  </div>;
}
