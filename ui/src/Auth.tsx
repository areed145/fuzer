import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import type { AuthSession } from "./authState";
import { setCsrf } from "./authState";
import { setTrustedPerson } from "./identityStore";
import { Logo } from "./brand";
import { useRoute } from "./lib";
import { ActionStatus, Badge, Button, ButtonLink, Card, Field, Input, PageHeader } from "./ui";
import "./styles/auth.css";

const AuthContext = createContext<AuthSession | null>(null);
export const useAuth = () => useContext(AuthContext);
export const refreshAccess = () => window.dispatchEvent(new Event("fuzer:access-changed"));
export function usePermission(permission: string) {
  const session = useAuth();
  return !session || session.mode === "local" || !!session.user?.enabled &&
    (session.user.owner || session.user.permissions.includes(permission));
}

export function AuthGate({ children }: { children: ReactNode }) {
  const { query } = useRoute();
  const [session, setSession] = useState<AuthSession | null>(null);
  const [error, setError] = useState("");
  const [ended, setEnded] = useState(false);
  const [checking, setChecking] = useState(false);
  const [revision, setRevision] = useState(0);
  const [setupCode, setSetupCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const reload = () => { setChecking(true); setRevision((value) => value + 1); };
  useEffect(() => {
    let active = true;
    api.authSession().then((value) => {
      if (!active) return;
      setCsrf(value.user?.csrf ?? "");
      if (value.mode === "microsoft") setTrustedPerson(value.user?.actor ?? "");
      setSession(value); setError(""); setEnded(false);
    }).catch((failure: Error) => { if (active) setError(failure.message); })
      .finally(() => { if (active) setChecking(false); });
    return () => { active = false; };
  }, [revision]);
  useEffect(() => {
    const expired = () => {
      setSession(null);
      setCsrf("");
      setEnded(true);
      setError("Your session has ended. Reload sign-in to continue. Your saved work is kept.");
    };
    window.addEventListener("fuzer:unauthorized", expired);
    window.addEventListener("fuzer:access-changed", reload);
    const refresh = () => { if (document.visibilityState === "visible") reload(); };
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("fuzer:unauthorized", expired);
      window.removeEventListener("fuzer:access-changed", reload);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  const pending = session?.user;
  const signInResult = query.get("signin");
  const admitted = session?.mode === "local" || !!session?.user?.enabled && (session.user.owner || session.user.permissions.includes("read"));
  // One heading per state, so the page says what happened before it says what to do.
  const state = admitted ? "admitted" : ended ? "ended" : error ? "check-failed" : !session ? "checking"
    : !session.configured ? "unconfigured" : pending ? session.needs_owner ? "first-owner" : pending.enabled ? "no-read" : "pending"
    : signInResult ? "sign-in-failed" : "sign-in";
  const shown = useRef<string | null>(null);
  useEffect(() => {  // a changed state moves focus to its heading; finishing the first check keeps the browser's focus
    if (shown.current !== null && shown.current !== "checking" && shown.current !== state && state !== "admitted") {
      document.querySelector<HTMLElement>(".auth-page h1")?.focus();
    }
    shown.current = state;
  }, [state]);

  if (admitted) {
    return <AuthContext.Provider value={session}>
      {error && <div role="alert" className="notice">Access could not be refreshed: {error}{" "}
        <Button onClick={reload}>Reload access</Button></div>}
      {children}
    </AuthContext.Provider>;
  }
  const heading = {
    ended: "Your session has ended", "check-failed": "Sign-in could not be checked", checking: "Checking your session",
    unconfigured: "Sign-in is not available yet", "first-owner": "Set up the first owner",
    "no-read": "Your account needs application access", pending: "Your account is ready for approval",
    "sign-in-failed": signInResult === "unavailable" ? "Microsoft sign-in is unreachable" : "Sign-in did not complete",
    "sign-in": "Investigate with evidence, not guesswork", admitted: "",
  }[state];
  const check = (label: string) => <Button onClick={reload} disabled={checking}>{checking ? "Checking..." : label}</Button>;
  return (
    <main className="auth-page" id="main-content">
      <Logo />
      <Card>
        <Badge tone="warn">Synthetic Okan demonstration</Badge>
        <PageHeader>{heading}</PageHeader>
        {["sign-in", "sign-in-failed", "checking"].includes(state) &&
          <p>Explore the Okan facility, trace a recorded issue to its asset and supporting evidence, and read a cited finding.
            The demonstration uses historical synthetic records.</p>}
        {ended ? <div role="alert"><p>{error}</p>{check("Reload sign-in")}</div>
          : error ? <div role="alert">
            <p>The sign-in service did not answer: {error}. Nothing was changed. Try again; if this continues,
              the deployment owner should check that the service is running.</p>
            {check("Reload sign-in")}
          </div>
          : !session ? <p role="status">Checking your session...</p>
          : !session.configured ? <div role="alert">
            <p>Microsoft sign-in has not been configured by the deployment owner. No API key or command-line setup is needed by visitors.</p>
            {check("Check again")}
          </div>
          : pending ? <>
            <Badge tone="warn">{pending.enabled ? "Signed in · no read access" : "Signed in · account not enabled"}</Badge>
            <p>Signed in as <strong>{pending.display_name}</strong>. {state === "first-owner"
              ? "No owner has been set up for this deployment yet, so nobody can approve accounts."
              : pending.enabled
              ? "Your account is enabled, but its application roles do not include read access. Ask an owner to review your application roles."
              : "Ask an owner to enable your account and assign application access. A not-enabled account may be awaiting approval or deliberately disabled."}
              {" "}An owner grants access after you sign in.</p>
            <ol aria-label="Access next steps">
              <li>Microsoft sign-in is complete for this account.</li>
              <li>{state === "first-owner" ? "The deployment owner enters the setup code below. Anyone else waits for them to finish."
                : pending.enabled ? "An owner must assign an application role with read permission."
                : "An owner must review account approval and application roles in Settings → Users."}</li>
              <li>{state === "first-owner" ? "Once an owner exists, they approve other accounts in Settings → Users."
                : "Check again after the owner saves access. Your original destination is kept."}</li>
            </ol>
            {session.needs_owner && <form onSubmit={async (event) => {
              event.preventDefault(); setBusy(true); setActionError("");
              try { await api.bootstrapOwner(setupCode); setSetupCode(""); reload(); }
              catch (failure) { setActionError(failure instanceof Error ? failure.message : String(failure)); }
              finally { setBusy(false); }
            }}>
              <h2>Deployment owner setup</h2>
              <p>Only the deployment owner should use the setup code configured in the hosting provider's secret settings.
                This binds owner access to this Microsoft account; it is not first-visitor registration.</p>
              <Field label="Owner setup code"><Input type="password" autoComplete="off" value={setupCode}
                onChange={(event) => setSetupCode(event.target.value)} required minLength={32} maxLength={512} /></Field>
              <Button type="submit" disabled={busy || setupCode.length < 32}>{busy ? "Confirming..." : "Set up owner access"}</Button>
            </form>}
            <div className="row">
              {check(pending.enabled ? "Check access" : "Check approval")}
              <SignOut />
            </div>
          </> : <>
            {signInResult && <p role="alert">{signInResult === "unavailable"
              ? "Microsoft sign-in is temporarily unreachable. Try again shortly."
              : `Sign-in ${signInResult === "expired" ? "expired" : "could not be completed"}. Try again; you can choose a different Microsoft account.`}
              {" "}If this continues, contact the deployment owner to check the application registration.</p>}
            <ButtonLink variant="primary" href={location.hash.startsWith("#/") && !signInResult
              ? `/auth/login?return_to=${encodeURIComponent(location.hash)}` : "/auth/login"}>Continue with Microsoft</ButtonLink>
            <p className="small">Personal and work or school Microsoft accounts work. An owner approves access.</p>
          </>}
        <ActionStatus busy={busy ? "Setting up owner access..." : null} error={actionError} />
      </Card>
    </main>
  );
}

export function SignOut() {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return <>
    <Button size="sm" disabled={busy} onClick={async () => {
      setBusy(true); setError("");
      try { await api.signOut(); location.reload(); }
      catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); setBusy(false); }
    }}>{busy ? "Signing out..." : "Sign out"}</Button>
    {error && <span role="alert">{error}</span>}
  </>;
}
