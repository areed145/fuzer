import { api } from "./api";
import { usePermission } from "./Auth";
import { Status, entityHref, href, useAction, useAsync } from "./lib";
import { ActionStatus, ConfirmAction, Card, Text, Heading } from "./ui";
import { usePerson } from "./work";
import { Markdown } from "./Markdown";
import { display } from "./entityRefs";
import { useEntityNames } from "./useEntityNames";
import { person as personName, say } from "./wording";

const queue = (role: string) => role.replace(/_/g, " ");

/** Work the assistant proposed. Nothing exists until the person who asked confirms it here. */
export function WorkDraftCard({ id }: { id: string }) {
  const draft = useAsync(() => api.workDraft(id), [id]);
  const operate = usePermission("operate");
  const [person] = usePerson();
  const { busy, error, run } = useAction();
  const d = draft.data;
  const names = useEntityNames(d?.evidence ?? []);
  // reload the recorded state, whichever way that went
  const confirm = () => run("create", () => api.confirmWorkDraft(id, person)).finally(draft.reload);
  return <Card className="work-draft" as="section" aria-label="Draft case">
    <Status of={draft} />
    {d && <>
      <Text as="p" small muted>{d.item_id ? "Case" : "Draft from the assistant · not created yet"} · {queue(d.role)} queue</Text>
      <Heading level={3}>{say(d.title)}</Heading>
      {/* the assistant writes the summary in Markdown, diagrams in a mermaid fence */}
      <div className="md work-draft-summary"><Markdown text={d.summary} /></div>
      {/* what the case asks for: its checklist once opened */}
      {(d.steps ?? []).length > 0 && <ol className="work-draft-steps">{d.steps.map((x, i) => <li key={i}>{x.text}</li>)}</ol>}
      <Text as="p" small muted>Evidence: {d.evidence.map((ref, i) => <span key={ref}>{i > 0 && ", "}<a href={entityHref(ref)} title={ref}>{display(ref, names)}</a></span>)}</Text>
      {d.item_id
        ? <p role="status">Created by {personName(d.confirmed_by)}. <a href={href(`/cases/${d.item_id}`)}>Open the case</a>.</p>
        : operate
          ? <ConfirmAction label="Open the case" busy={!!busy}
              consequence={`This adds shared work to the ${queue(d.role)} queue, with you recorded as its creator.`} onConfirm={confirm} />
          : <Text as="p" small muted>Creating work needs operate access. Ask an administrator or someone in this queue.</Text>}
      <ActionStatus busy={busy ? "Opening the case…" : null} error={error} />
    </>}
  </Card>;
}
