import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import type { AuthSession } from "./authState";
import { setCsrf } from "./authState";
import { setTrustedPerson } from "./identityStore";
import { Logo } from "./brand";
import { useRoute } from "./lib";
import { Badge, Button, ButtonLink, Card, Field, Input } from "./ui";
import "./styles/auth.css";

const AuthContext = createContext<AuthSession | null>(null);
export const useAuth = () => useContext(AuthContext);
export function usePermission(permission: string) {
  const session = useAuth();
  return !session || session.mode === "local" || !!session.user?.enabled &&
    (session.user.owner || session.user.permissions.includes(permission));
}

export function AuthGate({ children }: { children: ReactNode }) {
  const { query } = useRoute();
  const [session, setSession] = useState<AuthSession | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [setupCode, setSetupCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const reload = () => { setError(""); setRevision((value) => value + 1); };
  useEffect(() => {
    let active = true;
    api.authSession().then((value) => {
      if (!active) return;
      setCsrf(value.user?.csrf ?? "");
      if (value.mode === "microsoft") setTrustedPerson(value.user?.actor ?? "");
      setSession(value);
    }).catch((failure: Error) => { if (active) setError(failure.message); });
    return () => { active = false; };
  }, [revision]);
  useEffect(() => {
    const expired = () => {
      setSession(null);
      setCsrf("");
      setError("Your session has ended. Reload sign-in to continue. Your saved work is kept.");
    };
    window.addEventListener("fuzer:unauthorized", expired);
    const refresh = () => { if (document.visibilityState === "visible") reload(); };
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("fuzer:unauthorized", expired);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  if (session?.mode === "local" || session?.user?.enabled && (session.user.owner || session.user.permissions.includes("read"))) {
    return <AuthContext.Provider value={session}>{children}</AuthContext.Provider>;
  }
  const pending = session?.user;
  const signInResult = query.get("signin");
  return (
    <main className="auth-page" id="main-content">
      <Logo />
      <Card>
        <Badge tone="warn">Synthetic Okan demonstration</Badge>
        <h1>{pending ? "Your account is ready for approval" : "Investigate with evidence, not guesswork"}</h1>
        <p>Explore the Okan facility, trace a recorded issue to its asset and supporting evidence, and read a cited finding.
          The demonstration uses historical synthetic records, not a live plant.</p>
        {error ? <div role="alert"><p>{error}</p><Button onClick={reload}>Reload sign-in</Button></div>
          : !session ? <p role="status">Checking your session...</p>
          : !session.configured ? <div role="alert">
            <p>Microsoft sign-in has not been configured by the deployment owner. No API key or command-line setup is needed by visitors.</p>
            <Button onClick={reload}>Check again</Button>
          </div>
          : pending ? <>
            <p>Signed in as <strong>{pending.display_name}</strong>. Ask an owner to enable your account and assign application access.
              Signing in does not grant privileges automatically.</p>
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
              <Button onClick={reload}>Check approval</Button>
              <SignOut />
            </div>
          </> : <>
            {signInResult && <p role="alert">{signInResult === "unavailable"
              ? "Microsoft sign-in is temporarily unreachable. Try again shortly."
              : `Sign-in ${signInResult === "expired" ? "expired" : "could not be completed"}. Try again; you can choose a different Microsoft account.`}
              {" "}If this continues, contact the deployment owner to check the application registration.</p>}
            <ButtonLink variant="primary" href={location.hash.startsWith("#/") && !signInResult
              ? `/auth/login?return_to=${encodeURIComponent(location.hash)}` : "/auth/login"}>Continue with Microsoft</ButtonLink>
            <p className="muted small">Personal Microsoft and work/school accounts are supported. Microsoft handles your password,
              passkeys and Authenticator. An owner approves access to this shared demonstration.</p>
          </>}
        {actionError && <p role="alert">{actionError}</p>}
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
