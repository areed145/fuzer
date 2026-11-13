import { useState } from "react";
import { api, type AccessAccount, type AccessAdministration, type AccessRole, type AuditCoverage, type DocumentGroups, type WorkRoleRow } from "../api";
import { refreshAccess, useAuth, usePermission } from "../Auth";
import { href, notifyChanged, Status, useAsync } from "../lib";
import { ActionStatus, Badge, Button, Card, ConfirmAction, EmptyState, Field, Input, PageHeader, Select, Disclosure } from "../ui";
import { TeamsPage, WorkflowAssignments } from "./workflows/Teams";

export function UsersPage({ query }: { query: URLSearchParams }) {
  const session = useAuth();
  const configure = usePermission("configure");
  const owner = !!session?.user?.owner;
  if (!owner && !configure) return <section><PageHeader>Users</PageHeader>
    <p>Users administration requires configuration access. Only an owner can approve accounts or grant application permissions.</p>
    <a href={href("/")}>Return home</a></section>;
  const workflow = query.get("view") === "workflow" || !owner;
  return <section>
    <PageHeader subtitle="Accounts, application access and workflow assignments." help="identity">Users</PageHeader>
    <nav className="tabs users-tabs" aria-label="Users views">
      {owner && <a href={href("/users")} className={!workflow ? "on" : ""} aria-current={!workflow ? "page" : undefined}>Accounts &amp; access</a>}
      <a href={href("/users", { view: "workflow" })} className={workflow ? "on" : ""} aria-current={workflow ? "page" : undefined}>Workflow assignments &amp; routing</a>
    </nav>
    {!owner && <p>Configuration administrators can manage workflow assignments and routing, not application access.
      An owner approves accounts and grants application roles.</p>}
    {workflow ? <TeamsPage query={query} administration /> : <Administration query={query} />}
  </section>;
}

function Administration({ query }: { query: URLSearchParams }) {
  const [message, setMessage] = useState("");
  const data = useAsync(() => api.accessAdministration(), []);
  const workflow = useAsync(async () => {
    const [roles, people] = await Promise.all([api.rolesCatalog(), api.workPeople()]);
    return { roles, people };
  }, []);
  const changed = (message?: string) => {
    setMessage(message ?? "");
    data.reload(); workflow.reload();
  };
  const search = query.get("q") ?? "";
  const status = query.get("status") ?? "";
  const filter = (values: Record<string, string | undefined>, replace = false) => {
    const url = href("/users", { ...Object.fromEntries(query), ...values });
    if (replace) {
      history.replaceState(history.state, "", url);
      window.dispatchEvent(new Event("hashchange"));
    } else location.hash = url;
  };
  const accounts = data.data?.accounts.filter(account => {
    const matchesStatus = status === "not-enabled" ? !account.enabled
      : status === "enabled" ? account.enabled : status === "owners" ? account.enabled && account.owner : true;
    return matchesStatus && [account.display_name, account.actor, ...account.roles].join(" ").toLowerCase().includes(search.trim().toLowerCase());
  }) ?? [];
  const enabledOwners = data.data?.accounts.filter(account => account.enabled && account.owner).length ?? 0;
  const project = useAuth()?.project ?? "";
  const documents = useAsync(() => project ? api.documentGroups(project) : Promise.resolve(null), [project]);
  const groups = [...new Set(Object.values(documents.data?.restricted ?? {}))].sort();
  const restricted = (message?: string) => { changed(message); documents.reload(); };
  return <div>
    <ActionStatus>{message && <p>{message}</p>}</ActionStatus>
    <Status loading={data.loading} error={data.error} />
    {data.error && <Button onClick={data.reload}>Try again</Button>}
    {data.data && <>
      <Card title="Owner recovery">
        <p>{enabledOwners} enabled {enabledOwners === 1 ? "owner" : "owners"}. Keep at least one enabled owner.
          {enabledOwners < 2 && " Ask a second trusted account holder to sign in, then review owner access for recovery."}</p>
        <p className="small">Owner status grants every application capability and all human workflow roles.</p>
      </Card>
      {project && <AuditRecord project={project} />}
      {project && <RestrictedDocuments key={JSON.stringify(documents.data?.restricted ?? {})} project={project}
        data={documents.data} loading={documents.loading} error={documents.error} reload={restricted} />}
      <h2>Accounts</h2>
      <div className="row account-filters">
        <Field label="Find accounts" width="full"><Input type="search" width="full" value={search}
          onChange={event => filter({ q: event.target.value || undefined }, true)} placeholder="Name, stable identity or application role" /></Field>
        <Field label="Account status filter"><Select aria-label="Account status filter" value={status} onChange={event => filter({ status: event.target.value || undefined })}>
          <option value="">All accounts</option><option value="not-enabled">Not enabled</option>
          <option value="enabled">Enabled</option><option value="owners">Enabled owners</option>
        </Select></Field>
        {(search || status) && <Button onClick={() => filter({ q: undefined, status: undefined })}>Clear account filters</Button>}
      </div>
      <p role="status">{accounts.length} of {data.data.accounts.length} accounts</p>
      {!data.data.accounts.length && <p>No accounts have signed in yet.</p>}
      {data.data.accounts.length > 0 && !accounts.length && <EmptyState title="No accounts match">Change the name or status filter to find an account.</EmptyState>}
      {accounts.map((account) => <AccountCard key={`${account.id}:${account.enabled}:${account.owner}:${account.roles.join(",")}`}
        account={account} data={data.data!} workflow={workflow.data?.roles}
        assigned={workflow.data?.people.find(p => p.name === account.actor)?.assigned_roles ?? []}
        groups={groups} reload={changed} />)}
      <Disclosure label={<>Application role definitions</>}>
      <h2>Application roles</h2>
      {data.data.roles.map((role) => <RoleCard key={role.name + role.permissions.join(",") + role.description}
        role={role} data={data.data!} reload={changed} />)}
      <RoleCard data={data.data} reload={changed} />
      </Disclosure>
    </>}
    <Status loading={workflow.loading} error={workflow.error} />
    {workflow.error && <Button onClick={workflow.reload}>Retry workflow assignments</Button>}
  </div>;
}

function AccountCard({ account, data, workflow, assigned, groups, reload }: {
  account: AccessAccount; data: AccessAdministration; workflow?: WorkRoleRow[]; assigned: string[]; groups: string[];
  reload: (message?: string) => void;
}) {
  const [enabled, setEnabled] = useState(account.enabled);
  const [owner, setOwner] = useState(account.owner);
  const [roles, setRoles] = useState(account.roles);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const permissions = account.owner ? Object.keys(data.permissions) : [...new Set(
    data.roles.filter(r => account.roles.includes(r.name)).flatMap(r => r.permissions))];
  const dirty = enabled !== account.enabled || owner !== account.owner || [...roles].sort().join() !== [...account.roles].sort().join();
  const save = async () => {
    setBusy(true); setError(""); setSaved(false);
    try { await api.updateAccount(account.id, { enabled, owner, roles }); setSaved(true); reload(`Account access saved for ${account.display_name}.`); refreshAccess(); notifyChanged(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  };
  return <Card className="access-account" title={<>{account.display_name} <Badge tone={account.enabled ? "ok" : "warn"}>
    {account.owner ? "Owner" : account.enabled ? "Enabled" : "Not enabled"}</Badge></>}>
    <p>Effective access: <strong>{!account.enabled ? "None: account not enabled" : permissions.map(p => data.permissions[p] ?? p).join(", ") || "No permissions assigned"}</strong>.
      {" "}{account.owner ? "From owner status." : `From saved application roles: ${account.roles.join(", ") || "none"}.`}
      {account.enabled && !permissions.includes("read") && " Read permission is required to enter the application."}</p>
    <Disclosure label={<>Microsoft sign-in identity</>}>
      <p>Stable account identity: <code>{account.actor}</code>. Display names are not unique and do not link or merge accounts.</p>
      <p>Tenant: <code>{account.tenant_id ?? "Unavailable"}</code>; object: <code>{account.object_id ?? "Unavailable"}</code>.</p>
    </Disclosure>
    <Disclosure open={!account.enabled} label={<>{account.enabled ? "Change account access" : "Approve or keep disabled"}</>}>
      <fieldset disabled={busy}><legend>Account status</legend>
        <label><Input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />Enabled</label>
        <label><Input type="checkbox" checked={owner} onChange={(event) => setOwner(event.target.checked)} />Owner (all capabilities)</label>
      </fieldset>
      <fieldset disabled={busy || owner}><legend>Application roles</legend>
        {data.roles.map((role) => <label key={role.name} title={role.description}>
          <Input type="checkbox" checked={roles.includes(role.name)}
            onChange={(event) => setRoles(event.target.checked ? [...roles, role.name] : roles.filter((value) => value !== role.name))} />
          {role.name}
        </label>)}
      </fieldset>
      {dirty && <p role="status">Unsaved account access changes. Effective access above still reflects the saved account.
        Review the confirmation before applying changes.</p>}
      <ConfirmAction label="Save account access" busy={busy} disabled={!dirty}
        consequence={`Apply access to ${account.display_name} (${account.actor})? ${!enabled ? "Disabling revokes active sessions; workflow assignments remain for handoff." : owner ? "Owner grants every application capability and all human workflow roles." : `Application roles: ${roles.join(", ") || "none"}. Changes affect existing sessions.`}`}
        onConfirm={save} />
    </Disclosure>
    <ActionStatus busy={busy ? "Saving account access..." : null} error={error}>{saved && <p>Account access saved.</p>}</ActionStatus>
    {!account.owner && account.enabled && <AccountAreas account={account} reload={reload} />}
    {!account.owner && account.enabled && groups.length > 0 && <AccountGroups account={account} groups={groups} reload={reload} />}
    {workflow && <AccountAssignments key={assigned.join(",")}
      account={account} roles={workflow.filter(r => r.kind === "person")} assigned={assigned} reload={reload} />}
  </Card>;
}

function AccountAssignments({ account, roles, assigned, reload }: {
  account: AccessAccount; roles: WorkRoleRow[]; assigned: string[]; reload: (message?: string) => void;
}) {
  if (account.owner) return <p>{account.enabled
    ? "Workflow access: all human roles from owner status, including roles added later."
    : "Workflow access: none while this account is disabled. Owner status is retained, but human workflow roles become effective only when the account is enabled."}
    {" "}Explicit roster assignments are preserved; changing them cannot remove owner status.</p>;
  return <Disclosure label={<>Human workflow assignments ({assigned.length})</>}>
    {!account.enabled && <p>Assignments are retained, but this disabled account has no effective workflow-role access.</p>}
    <p>Saved assignments use this account's stable identity, never its display name. Changing these does not enable the account,
      grant application permissions or move already claimed work. Use <a href={href("/users", { view: "workflow", person: account.actor })}>routing and workload</a> for coverage/handoff.</p>
    <WorkflowAssignments name={account.actor} roles={roles} assigned={assigned}
      onInspect={() => reload()}
      reload={() => reload(`Workflow assignments saved for ${account.display_name}. Application access unchanged.`)} />
  </Disclosure>;
}

function AccountAreas({ account, reload }: { account: AccessAccount; reload: (message?: string) => void }) {
  const project = useAuth()?.project ?? "";
  if (!project) return null;
  const saved = account.areas?.[project] ?? [];
  return <Disclosure label={<>Areas in {project} ({saved.length || "all"})</>}>
    <AreasEditor key={saved.join(",")} account={account} project={project} saved={saved} reload={reload} />
  </Disclosure>;
}

function AreasEditor({ account, project, saved, reload }: {
  account: AccessAccount; project: string; saved: string[]; reload: (message?: string) => void;
}) {
  const [areas, setAreas] = useState(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const options = useAsync(() => api.search("", "area"), []);
  const dirty = [...areas].sort().join() !== [...saved].sort().join();
  const save = async () => {
    setBusy(true); setError("");
    try {
      await api.setAreas(account.id, project, areas);
      reload(areas.length ? `${account.display_name} now acts only on work in ${areas.length} ${areas.length === 1 ? "area" : "areas"} of ${project}.`
        : `${account.display_name} acts on work across all of ${project} again.`);
      notifyChanged();
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  };
  return <>
    <p>{saved.length ? `Acts only on work in ${saved.length} ${saved.length === 1 ? "area" : "areas"}.` : "Acts on work anywhere in this project."}
      {" "}Reading is never narrowed; facility-wide work stays shared.</p>
    <Status loading={options.loading} error={options.error} />
    {options.data && !options.data.length && <p>This project's register has no areas yet.</p>}
    {options.data && options.data.length > 0 && <fieldset disabled={busy}><legend>Areas it can act on (none ticked: all)</legend>
      {options.data.map((hit) => <label key={hit.fuzer_id}>
        <Input type="checkbox" checked={areas.includes(hit.fuzer_id)}
          onChange={(event) => setAreas(event.target.checked ? [...areas, hit.fuzer_id] : areas.filter((value) => value !== hit.fuzer_id))} />
        {hit.label ?? hit.fuzer_id}
      </label>)}
    </fieldset>}
    <ConfirmAction label="Save areas" busy={busy} disabled={!dirty}
      consequence={areas.length ? `Narrow ${account.display_name} to ${areas.length} ${areas.length === 1 ? "area" : "areas"} of ${project}? Their actions on work elsewhere are refused; they can still read it.`
        : `Let ${account.display_name} act on work anywhere in ${project}?`}
      onConfirm={save} />
    <ActionStatus busy={busy ? "Saving areas..." : null} error={error} />
  </>;
}

const day = (d: Date) => d.toISOString().slice(0, 10);

/** R.5b: an owner downloads one project's audit record for a window, and sees what it covers and how long it keeps. */
function AuditRecord({ project }: { project: string }) {
  const today = new Date();
  const [since, setSince] = useState(day(new Date(today.getTime() - 30 * 86400000)));
  const [until, setUntil] = useState(day(today));
  const [format, setFormat] = useState<"csv" | "json">("csv");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  const [failure, setFailure] = useState("");
  const coverage = useAsync<AuditCoverage | null>(() => api.auditCoverage(project), [project]);
  const download = async () => {
    setBusy(true); setFailure(""); setDone("");
    try {
      // the window runs to the end of its last day
      const end = new Date(`${until}T00:00:00Z`); end.setUTCDate(end.getUTCDate() + 1);
      const text = await api.auditExport(project, `${since}T00:00:00Z`, end.toISOString(), format);
      const rows = format === "csv" ? Math.max(text.trim().split("\n").length - 1, 0) : (JSON.parse(text).rows ?? []).length;
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([text], { type: format === "csv" ? "text/csv" : "application/json" }));
      link.download = `fuzer-audit-${project}-${since.replaceAll("-", "")}-${until.replaceAll("-", "")}.${format}`;
      link.click();
      URL.revokeObjectURL(link.href);
      setDone(`Downloaded ${rows} ${rows === 1 ? "event" : "events"} from ${project}, ${since} to ${until}. The download is itself in the record.`);
    } catch (e) { setFailure(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const c = Array.isArray(coverage.data?.included) ? coverage.data : null;  // an API without the coverage answers otherwise
  const every = c?.included.filter(i => i.history).map(i => i.source) ?? [];
  const latest = c?.included.filter(i => !i.history).map(i => i.source) ?? [];
  return <Card title={`Audit record for ${project}`}>
    <p>Who approved a write-back, changed a routing or signed in, for this project only. Each row says whether its
      record keeps every change or only the latest.</p>
    <div className="row wrap">
      <Field label="From"><Input type="date" value={since} max={until} onChange={e => setSince(e.target.value)} /></Field>
      <Field label="To"><Input type="date" value={until} min={since} onChange={e => setUntil(e.target.value)} /></Field>
      <Field label="Format"><Select aria-label="Format" value={format} onChange={e => setFormat(e.target.value as "csv" | "json")}>
        <option value="csv">CSV</option><option value="json">JSON</option>
      </Select></Field>
    </div>
    <Button disabled={busy || !since || !until || since > until} onClick={download}>Download audit record</Button>
    <ActionStatus busy={busy ? "Exporting the audit record..." : null} error={failure}>{done && <p>{done}</p>}</ActionStatus>
    <Status loading={coverage.loading} error={coverage.error} />
    {c?.retention && <ul className="small">{Object.entries(c.retention).map(([k, v]) => <li key={k}>{v}</li>)}</ul>}
    {c && <Disclosure label={<>What the record covers ({every.length + latest.length} records, {(c.excluded ?? []).length} left out)</>}>
      <p className="small"><strong>Every change:</strong> {every.join(", ") || "none"}</p>
      <p className="small"><strong>Latest only</strong> (overwritten when decided again): {latest.join(", ") || "none"}</p>
      <ul className="small">{(c.excluded ?? []).map(x => <li key={x.source}>{x.source}: {x.reason}</li>)}</ul>
    </Disclosure>}
  </Card>;
}

function RestrictedDocuments({ project, data, loading, error, reload }: {
  project: string; data?: DocumentGroups | null; loading: boolean; error?: string; reload: (message?: string) => void;
}) {
  const saved = data?.restricted ?? {};
  const [restricted, setRestricted] = useState<Record<string, string>>(saved);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const wanted = Object.fromEntries(Object.entries(restricted).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v));
  const dirty = JSON.stringify(Object.entries(wanted).sort()) !== JSON.stringify(Object.entries(saved).sort());
  const count = Object.keys(wanted).length;
  const save = async () => {
    setBusy(true); setFailure("");
    try {
      await api.setDocumentGroups(project, wanted);
      reload(count ? `${count} document ${count === 1 ? "class is" : "classes are"} now restricted in ${project}.`
        : `Every document class is open in ${project} again.`);
      notifyChanged();
    } catch (e) { setFailure(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <Card title={`Restricted documents in ${project}`}>
    <p>Restrict a document class, such as licensed OEM manuals, to a group. People outside the group can't see those
      documents anywhere: pages, search, files or the assistant. Owners see everything. Leave the group empty to keep a class open.</p>
    <Status loading={loading} error={error} />
    {data && !data.classes.length && <p>This project's register has no documents yet.</p>}
    {data && data.classes.length > 0 && <fieldset disabled={busy}><legend>Group that may see each class</legend>
      {data.classes.map((c) => <Field key={c.doc_type} label={`${c.doc_type.replaceAll("_", " ")} (${c.documents})`}>
        <Input value={restricted[c.doc_type] ?? ""} placeholder="Open to everyone"
          onChange={(event) => setRestricted({ ...restricted, [c.doc_type]: event.target.value })} />
      </Field>)}
    </fieldset>}
    <ConfirmAction label="Save restrictions" busy={busy} disabled={!dirty}
      consequence={count ? `Restrict ${count} document ${count === 1 ? "class" : "classes"} in ${project}? People outside each group stop seeing those documents at once.`
        : `Open every document class in ${project} to everyone who can read it?`}
      onConfirm={save} />
    <ActionStatus busy={busy ? "Saving restrictions..." : null} error={failure} />
  </Card>;
}

function AccountGroups({ account, groups, reload }: { account: AccessAccount; groups: string[]; reload: (message?: string) => void }) {
  const project = useAuth()?.project ?? "";
  if (!project) return null;
  const saved = account.groups?.[project] ?? [];
  return <Disclosure label={<>Document groups in {project} ({saved.length})</>}>
    <GroupsEditor key={saved.join(",")} account={account} project={project} groups={groups} saved={saved} reload={reload} />
  </Disclosure>;
}

function GroupsEditor({ account, project, groups, saved, reload }: {
  account: AccessAccount; project: string; groups: string[]; saved: string[]; reload: (message?: string) => void;
}) {
  const [chosen, setChosen] = useState(saved);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dirty = [...chosen].sort().join() !== [...saved].sort().join();
  const save = async () => {
    setBusy(true); setError("");
    try {
      await api.setGroups(account.id, project, chosen);
      reload(chosen.length ? `${account.display_name} is now in ${chosen.join(", ")} in ${project}.`
        : `${account.display_name} is in no document groups in ${project}.`);
      notifyChanged();
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  };
  return <>
    <p>{saved.length ? `Sees documents restricted to ${saved.join(", ")}.` : "Sees no restricted documents."}</p>
    <fieldset disabled={busy}><legend>Groups</legend>
      {groups.map((group) => <label key={group}>
        <Input type="checkbox" checked={chosen.includes(group)}
          onChange={(event) => setChosen(event.target.checked ? [...chosen, group] : chosen.filter((value) => value !== group))} />
        {group}
      </label>)}
    </fieldset>
    <ConfirmAction label="Save groups" busy={busy} disabled={!dirty}
      consequence={`Put ${account.display_name} in ${chosen.join(", ") || "no groups"} in ${project}? They see exactly the restricted documents of those groups.`}
      onConfirm={save} />
    <ActionStatus busy={busy ? "Saving groups..." : null} error={error} />
  </>;
}

function RoleCard({ role, data, reload }: { role?: AccessRole; data: AccessAdministration; reload: (message?: string) => void }) {
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [permissions, setPermissions] = useState(role?.permissions ?? ["read"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const save = async () => {
    setBusy(true); setError(""); setSaved(false);
    try {
      await api.setAccessRole(name, { description, permissions });
      if (!role) { setName(""); setDescription(""); setPermissions(["read"]); }
      setSaved(true); reload(`Application role ${name} saved. Existing sessions use the updated permissions.`); refreshAccess(); notifyChanged();
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  };
  return <Card className="access-role" title={role?.name ?? "Add an application role"}>
    <fieldset disabled={busy}><legend>Application role definition</legend>
      {!role && <Field label="Role name"><Input value={name} onChange={(event) => setName(event.target.value)}
        pattern="[a-z][a-z0-9_]{0,63}" maxLength={64} required placeholder="e.g. reliability_reviewer" /></Field>}
      <Field label="Description" width="full"><Input width="full" value={description}
        onChange={(event) => setDescription(event.target.value)} required maxLength={500} /></Field>
      <fieldset disabled={busy}><legend>Permissions (changes apply to existing sessions)</legend>
        {Object.entries(data.permissions).map(([key, label]) => <label key={key}>
          <Input type="checkbox" checked={permissions.includes(key)}
            onChange={(event) => setPermissions(event.target.checked ? [...permissions, key] : permissions.filter((value) => value !== key))} />
          {label}
        </label>)}
      </fieldset>
      <p className="small">AI access permits provider charges and is not part of the administrator role.</p>
      <ConfirmAction label={role ? "Save application role" : "Add application role"} busy={busy}
        disabled={!/^[a-z][a-z0-9_]{0,63}$/.test(name) || !description.trim()}
        consequence={`Apply application role ${name} with permissions ${permissions.join(", ") || "none"}? This affects all assigned accounts and their existing sessions. AI access can incur provider charges.`}
        onConfirm={save} />
    </fieldset>
    <ActionStatus busy={busy ? "Saving application role..." : null} error={error}>{saved && <p>Application role saved.</p>}</ActionStatus>
  </Card>;
}
