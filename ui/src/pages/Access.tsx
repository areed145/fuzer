import { useState } from "react";
import { api, type AccessAccount, type AccessAdministration, type AccessRole } from "../api";
import { useAuth } from "../Auth";
import { href, Status, useAsync } from "../lib";
import { Badge, Button, Card, Field, Input, PageHeader } from "../ui";

export function AccessPage() {
  const session = useAuth();
  if (!session?.user?.owner) return <section><PageHeader>Sign-in &amp; access</PageHeader>
    <p>Only an owner can grant application access. Ask an owner to update your account.</p>
    <a href={href("/")}>Return home</a></section>;
  return <Administration />;
}

function Administration() {
  const data = useAsync(() => api.accessAdministration(), []);
  return <section>
    <PageHeader subtitle="Approve signed-in accounts and define reusable application roles.">Sign-in &amp; access</PageHeader>
    <p>This is a shared operational demonstration, not a private sandbox. Approved users see the same synthetic facility;
      operational changes affect that shared facility. Assistant conversations and uploads stay with the account that created them.
      Facts deliberately learned into shared facility memory are shared knowledge.</p>
    <Card title="Two kinds of role">
      <p><strong>Application roles</strong> below control what a signed-in account may access, change or spend.
        <strong> Workflow roles</strong> route human work, and are managed in <a href={href("/teams")}>Teams &amp; routing</a>.
        Giving someone a workflow role does not grant application access.</p>
      <p>An owner can use all application capabilities and every current or future human workflow role. Automated agents keep
        their separate identities. Keep at least one enabled owner; a second owner provides recovery if a Microsoft account becomes unavailable.</p>
    </Card>
    <Status loading={data.loading} error={data.error} />
    {data.error && <Button onClick={data.reload}>Try again</Button>}
    {data.data && <>
      <h2>Accounts</h2>
      {!data.data.accounts.length && <p>No accounts have signed in yet.</p>}
      {data.data.accounts.map((account) => <AccountCard key={`${account.id}:${account.enabled}:${account.owner}:${account.roles.join(",")}`}
        account={account} data={data.data!} reload={data.reload} />)}
      <h2>Application roles</h2>
      {data.data.roles.map((role) => <RoleCard key={role.name + role.permissions.join(",") + role.description}
        role={role} data={data.data!} reload={data.reload} />)}
      <RoleCard data={data.data} reload={data.reload} />
    </>}
  </section>;
}

function AccountCard({ account, data, reload }: { account: AccessAccount; data: AccessAdministration; reload: () => void }) {
  const [enabled, setEnabled] = useState(account.enabled);
  const [owner, setOwner] = useState(account.owner);
  const [roles, setRoles] = useState(account.roles);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return <Card className="access-account" title={<>{account.display_name} <Badge tone={account.enabled ? "ok" : "warn"}>
    {account.owner ? "Owner" : account.enabled ? "Enabled" : "Awaiting approval"}</Badge></>}>
    <p className="muted small">Workflow roster identity: <code>{account.actor}</code>. Use this identity when assigning
      human workflow roles in Teams &amp; routing; display names are not sign-in identifiers.</p>
    <form onSubmit={async (event) => {
      event.preventDefault(); setBusy(true); setError("");
      try { await api.updateAccount(account.id, { enabled, owner, roles }); reload(); }
      catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
      finally { setBusy(false); }
    }}>
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
      <Button type="submit" disabled={busy}>{busy ? "Saving..." : "Save account access"}</Button>
      {error && <p role="alert">{error}</p>}
    </form>
  </Card>;
}

function RoleCard({ role, data, reload }: { role?: AccessRole; data: AccessAdministration; reload: () => void }) {
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [permissions, setPermissions] = useState(role?.permissions ?? ["read"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <Card className="access-role" title={role?.name ?? "Add an application role"}>
    <form onSubmit={async (event) => {
      event.preventDefault(); setBusy(true); setError("");
      try {
        await api.setAccessRole(name, { description, permissions });
        if (!role) { setName(""); setDescription(""); setPermissions(["read"]); }
        reload();
      } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
      finally { setBusy(false); }
    }}>
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
      <p className="muted small">AI access permits provider charges; it is not included in the administrator role.
        Configuration access is trusted administration, including source connections and scheduled jobs.</p>
      <Button type="submit" disabled={busy}>{busy ? "Saving..." : role ? "Save application role" : "Add application role"}</Button>
      {error && <p role="alert">{error}</p>}
    </form>
  </Card>;
}
