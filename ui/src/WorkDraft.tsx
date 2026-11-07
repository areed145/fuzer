import { useState } from "react";
import { api } from "./api";
import { usePermission } from "./Auth";
import { Status, entityHref, href, useAsync } from "./lib";
import { ActionStatus, ConfirmAction, Card } from "./ui";
import { usePerson } from "./work";

const queue = (role: string) => role.replace(/_/g, " ");

/** Work the assistant proposed. Nothing exists until the person who asked confirms it here. */
export function WorkDraftCard({ id }: { id: string }) {
  const draft = useAsync(() => api.workDraft(id), [id]);
  const operate = usePermission("operate");
  const [person] = usePerson();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const d = draft.data;
  const confirm = async () => {
    setBusy(true); setError(null);
    try { await api.confirmWorkDraft(id, person); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); draft.reload(); }  // the recorded state, whichever way that went
  };
  return <Card className="work-draft" as="section" aria-label="Draft work item">
    <Status loading={draft.loading && !d} error={draft.error} onRetry={draft.reload} />
    {d && <>
      <p className="small muted">{d.item_id ? "Work item" : "Draft from the assistant · not created yet"} · {queue(d.role)} queue</p>
      <h3>{d.title}</h3>
      <p>{d.summary}</p>
      <p className="small muted">Evidence: {d.evidence.map((ref, i) => <span key={ref}>{i > 0 && ", "}<a className="mono" href={entityHref(ref)}>{ref}</a></span>)}</p>
      {d.item_id
        ? <p role="status">Created by {d.confirmed_by}. <a href={href(`/cases/${d.item_id}`)}>Open the case</a>.</p>
        : operate
          ? <ConfirmAction label="Create work item" busy={busy}
              consequence={`This adds shared work to the ${queue(d.role)} queue, with you recorded as its creator.`} onConfirm={confirm} />
          : <p className="small muted">Creating work needs operate access. Ask an administrator or someone in this queue.</p>}
      <ActionStatus busy={busy ? "Creating the work item…" : null} error={error} />
    </>}
  </Card>;
}
