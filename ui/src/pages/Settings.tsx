import { useState } from "react";
import { api, type SettingRow, type SettingScope } from "../api";
import { Status, href, notifyChanged, useAsync } from "../lib";
import { loadPerson } from "../work";
import { PeopleCard } from "./People";

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
  const project = query.get("project") ?? undefined;
  const list = useAsync(() => api.settings(project), [project]);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const person = loadPerson() || "anonymous";
  const f = filter.trim().toLowerCase();
  const rows = (list.data?.settings ?? []).filter(
    (s) => !f || s.key.toLowerCase().includes(f) || s.description.toLowerCase().includes(f),
  );
  const groups = [...new Set(rows.map((s) => s.group))];
  return (
    <section>
      <h1>Settings</h1>
      <p className="muted">
        Thresholds, caps, models and schedules. A project value beats the global one, which beats the default. Each
        change needs a reason and is kept in the setting's history. Changes are recorded as <b>{person}</b>.
      </p>
      <div className="card row small">
        <label>
          Project{" "}
          <select
            value={list.data?.project ?? ""}
            onChange={(e) => (location.hash = href("/settings", { project: e.target.value || undefined }))}
          >
            {!list.data?.project && <option value="">(none: global only)</option>}
            {list.data?.projects.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <input placeholder="filter settings" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      {!f && <PeopleCard by={person} />}
      <Status loading={list.loading && !list.data} error={list.error} empty={list.data && rows.length === 0}>
        No settings match.
      </Status>
      {groups.map((g) => (
        <div className="card" key={g}>
          <h2>{g}</h2>
          <table className="settings">
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
          </table>
        </div>
      ))}
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
          {s.restart && <span className="badge" title="takes effect when the process restarts">restart</span>}
          <div className="muted small">{s.description}</div>
        </td>
        <td>
          <b>{show(s.value)}</b>{" "}
          <span className={`badge ${s.from === "default" ? "" : "key"}`}>{s.from}</span>
          {s.type === "secret" && (
            <span className={`badge ${s.resolves ? "ok" : ""}`}>{s.resolves ? "resolves" : "not set"}</span>
          )}
          {s.from === "project" && s.global !== null && <div className="muted small">global: {show(s.global)}</div>}
        </td>
        <td className="muted">{show(s.default)}</td>
        <td className="muted small">{range(s)}</td>
        <td className="muted small">{s.updated_by ? `${s.updated_by}, ${when(s.updated_at)}` : ""}</td>
        <td><button onClick={toggle}>{open ? "Close" : "Change"}</button></td>
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
  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    if (reason.trim().length < 3) return setError("Give a short reason: it goes in the history.");
    setBusy(true);
    try {
      await fn();
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
      <select value={scope} onChange={(e) => setScope(e.target.value as SettingScope)}>
        {canProject && <option value="project">for {project}</option>}
        <option value="global">for all projects</option>
      </select>
      {s.type === "bool" ? (
        <select value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="true">yes</option><option value="false">no</option>
        </select>
      ) : s.choices ? (
        <select value={value} onChange={(e) => setValue(e.target.value)}>
          {s.choices.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      ) : (
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          type={s.type === "int" || s.type === "float" ? "number" : "text"}
          step={s.type === "float" ? "any" : undefined}
          min={s.min}
          max={s.max}
          placeholder={s.type === "secret" ? "env:NAME" : undefined}
        />
      )}
      <input className="grow" placeholder="reason for the change" value={reason} onChange={(e) => setReason(e.target.value)} />
      <button className="primary" disabled={busy} onClick={save}>Save</button>
      <button disabled={busy || current === null} onClick={reset}
        title={scope === "project" ? "back to the global value" : "back to the default"}>
        Reset
      </button>
      {error && <span className="error">{error}</span>}
    </div>
  );
}

function History({ settingKey }: { settingKey: string }) {
  const h = useAsync(() => api.settingHistory(settingKey), [settingKey]);
  if (h.error) return <div className="error small">{h.error}</div>;
  if (!h.data) return null;
  if (h.data.length === 0) return <p className="muted small">Never changed: it has always been the default.</p>;
  return (
    <table className="list small">
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
    </table>
  );
}
