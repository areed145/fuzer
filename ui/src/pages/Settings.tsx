import "../styles/settings.css";
import { Button, PageHeader, Table, Card as UICard, Input, Select, Badge as UIBadge, Empty } from "../ui";
import { useState } from "react";
import { api, type SettingRow, type SettingScope } from "../api";
import { Status, href, notifyChanged, useAsync } from "../lib";
import { loadPerson } from "../work";
import { Appearance } from "../theme";
import { usePermission } from "../Auth";
import { ApplicationReadiness } from "./ApplicationReadiness";
import { Detection } from "./Detection";

const when = (at: string | null) => (at ? at.slice(0, 16).replace("T", " ") : "");

const show = (v: unknown) =>
  v === null || v === undefined || v === "" ? "—" : typeof v === "boolean" ? (v ? "yes" : "no") : String(v);

function range(s: SettingRow): string {
  if (s.choices) return s.choices.join(" / ");
  if (s.min !== undefined && s.max !== undefined) return `${s.min} to ${s.max}`;
  if (s.min !== undefined) return `≥ ${s.min}`;
  if (s.max !== undefined) return `≤ ${s.max}`;
  return "";
}

export function SettingsPage({ query }: { query: URLSearchParams }) {
  const configure = usePermission("configure");
  if (!configure) return <section><PageHeader>Settings</PageHeader><Appearance />
    <p>Facility configuration requires administrator access. Ask an owner to update your application roles.</p></section>;
  return <Configuration key={query.toString()} query={query} />;
}

function Configuration({ query }: { query: URLSearchParams }) {
  const project = query.get("project") ?? undefined;
  const list = useAsync(() => api.settings(project), [project]);
  const [filter, setFilter] = useState(query.get("filter") ?? "");
  const [open, setOpen] = useState<string | null>(null);
  const person = loadPerson() || "anonymous";
  const f = filter.trim().toLowerCase();
  const rows = (list.data?.settings ?? []).filter(
    (s) => s.group !== "Sources" && s.group !== "Detection" && (!f || s.key.toLowerCase().includes(f) || s.description.toLowerCase().includes(f)),
  );
  const groups = [...new Set(rows.map((s) => s.group))];
  const detection = query.get("view") === "detection";
  const configuration = !detection && (query.get("view") === "configuration" || query.has("filter"));
  return (
    <section>
      <PageHeader subtitle="Preferences and configuration.">Settings</PageHeader>
      <Appearance />
      <nav className="tabs" aria-label="Settings sections">
        <a className={!configuration && !detection ? "on" : ""} href={href("/settings", { project })} aria-current={!configuration && !detection ? "page" : undefined}>Readiness</a>
        <a className={configuration ? "on" : ""} href={href("/settings", { project, view: "configuration" })} aria-current={configuration ? "page" : undefined}>All settings</a>
        <a className={detection ? "on" : ""} href={href("/settings", { view: "detection" })} aria-current={detection ? "page" : undefined}>Detection</a>
      </nav>
      {detection ? <Detection /> : !configuration ? <ApplicationReadiness list={list} /> : <>
      <UICard className="row small">
        <label>
          Project{" "}
          <Select
            value={list.data?.project ?? ""}
            onChange={(e) => (location.hash = href("/settings", { project: e.target.value || undefined, view: "configuration" }))}
          >
            {!list.data?.project && <option value="">(none: global only)</option>}
            {list.data?.projects.map((p) => <option key={p} value={p}>{p}</option>)}
          </Select>
        </label>
        <Input aria-label="Filter settings" placeholder="filter settings" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </UICard>
      <p className="small">Manage source endpoints and secret references in{" "}
        <a href={href("/sources", { view: "systems" })}>Systems &amp; connections</a>, not legacy source-seeding settings.</p>
      {!f && <p>Accounts, permissions and workflow assignments are managed in <a href={href("/users")}>Users</a>.</p>}
      <Status loading={list.loading && !list.data} error={list.error} empty={list.data && rows.length === 0}>
        No settings match.
      </Status>
      {groups.map((g) => (
        <UICard className="" key={g}>
          <h2>{g}</h2>
          <Table variant="settings">
            <thead>
              <tr>
                <th>Setting</th><th>Value</th><th>Default</th><th>Range</th><th>Last changed</th><th />
              </tr>
            </thead>
            <tbody>
              {rows.filter((s) => s.group === g).map((s) => (
                <SettingLine
                  key={s.key}
                  s={s}
                  project={list.data?.project ?? null}
                  person={person}
                  open={open === s.key}
                  toggle={() => setOpen(open === s.key ? null : s.key)}
                  saved={() => { list.reload(); notifyChanged(); }}
                />
              ))}
            </tbody>
          </Table>
        </UICard>
      ))}
      </>}
    </section>
  );
}

function SettingLine({ s, project, person, open, toggle, saved }: {
  s: SettingRow; project: string | null; person: string; open: boolean; toggle: () => void; saved: () => void;
}) {
  return (
    <>
      <tr>
        <td>
          <code>{s.key}</code>
          {s.restart && <UIBadge className="badge" title="takes effect when the process restarts">restart</UIBadge>}
          {!s.restart && <UIBadge>next use</UIBadge>}
          <div className="muted small">{s.description}</div>
        </td>
        <td>
          <b>{show(s.value)}</b>{" "}
          <UIBadge className={`badge ${s.from === "default" ? "" : "key"}`}>{s.from}</UIBadge>
          {s.type === "secret" && (
            <UIBadge className={`badge ${s.resolves ? "ok" : ""}`}>{s.resolves ? "resolves" : "not set"}</UIBadge>
          )}
          {s.from === "project" && s.global !== null && <div className="muted small">global: {show(s.global)}</div>}
          {s.from === "environment" && <div className="muted small">Legacy variable: <code>{s.environment}</code>. Save here to override it.</div>}
        </td>
        <td className="muted">{show(s.default)}</td>
        <td className="muted small">{range(s)}</td>
        <td className="muted small">{s.updated_by ? `${s.updated_by}, ${when(s.updated_at)}` : ""}</td>
        <td><Button onClick={toggle}>{open ? "Close" : "Change"}</Button></td>
      </tr>
      {open && (
        <tr className="setting-edit">
          <td colSpan={6}>
            <Editor s={s} project={project} person={person} saved={saved} />
            <History settingKey={s.key} />
          </td>
        </tr>
      )}
    </>
  );
}

function Editor({ s, project, person, saved }: {
  s: SettingRow; project: string | null; person: string; saved: () => void;
}) {
  const canProject = s.scopes.includes("project") && project !== null;
  const [scope, setScope] = useState<SettingScope>(canProject ? "project" : "global");
  const current = scope === "project" ? s.project : s.global;
  const [value, setValue] = useState<string>(String(current ?? s.value ?? ""));
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    setMessage("");
    if (reason.trim().length < 3) return setError("Give a short reason: it goes in the history.");
    setBusy(true);
    try {
      await fn();
      setMessage(s.restart
        ? "Saved. The affected application or scheduler process must be restarted by the deployment operator. Running values have not been verified."
        : "Saved. Applies on next use after the settings cache refreshes; existing work is not rerun.");
      setReason("");
      saved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const save = () => run(() => api.setSetting(s.key, value, scope, project, person, reason.trim()));
  const reset = () => run(() => api.resetSetting(s.key, scope, project, person, reason.trim()));
  return (
    <div className="row small">
      <Select aria-label={`Scope for ${s.key}`} value={scope} onChange={(e) => {
        const next = e.target.value as SettingScope;
        setScope(next);
        setValue(String((next === "project" ? s.project : s.global) ?? s.value ?? ""));
        setMessage("");
      }}>
        {canProject && <option value="project">for {project}</option>}
        <option value="global">for all projects</option>
      </Select>
      {s.type === "bool" ? (
        <Select aria-label={`Value for ${s.key}`} value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="true">yes</option><option value="false">no</option>
        </Select>
      ) : s.choices ? (
        <Select aria-label={`Value for ${s.key}`} value={value} onChange={(e) => setValue(e.target.value)}>
          {s.choices.map((c) => <option key={c} value={c}>{c}</option>)}
        </Select>
      ) : (
        <Input
          aria-label={`Value for ${s.key}`}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          type={s.type === "int" || s.type === "float" ? "number" : "text"}
          step={s.type === "float" ? "any" : undefined}
          min={s.min}
          max={s.max}
          placeholder={s.type === "secret" ? "env:NAME" : undefined}
        />
      )}
      <Input aria-label={`Reason for ${s.key}`} className="grow" placeholder="reason for the change" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button variant="primary" disabled={busy} onClick={save}>Save</Button>
      <Button disabled={busy || current === null} onClick={reset}
        title={scope === "project" ? "back to the global value" : "back to the default"}>
        Reset
      </Button>
      {error && <span role="alert" className="error">{error}</span>}
      {message && <span role="status">{message}</span>}
    </div>
  );
}

function History({ settingKey }: { settingKey: string }) {
  const h = useAsync(() => api.settingHistory(settingKey), [settingKey]);
  if (h.error) return <div className="error small">{h.error}</div>;
  if (!h.data) return null;
  if (h.data.length === 0) return <Empty className="small">Never changed: it has always been the default.</Empty>;
  return (
    <Table variant="list" density="compact">
      <tbody>
        {h.data.map((c, i) => (
          <tr key={i}>
            <td className="muted">{when(c.at)}</td>
            <td>{c.by}</td>
            <td>{c.scope === "global" ? "all projects" : c.scope.replace("project:", "")}</td>
            <td>{show(c.old)} → {c.new === null ? "reset" : show(c.new)}</td>
            <td className="muted">{c.reason ?? ""}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
