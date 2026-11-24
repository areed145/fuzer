/** Knowledge (Ops IQ): what the agents propose to know, decided by a person in one queue. Facts the assistant learned
 * from a conversation wait here before any answer recalls them; suggested lessons before anyone's practice takes them
 * up; skill drafts before the agents follow them (decided on the skill's page, behind its eval). */
import { useState } from "react";
import { api, type KnowledgeDecision, type ProposedFact, type SkillRow } from "../api";
import { when } from "../format";
import { Status, entityHref, href, useAction, useAsync } from "../lib";
import { usePermission } from "../Auth";
import { ActionStatus, Badge, Button, Card, Empty, Facts, Line, List, PageHeader, Table, Tabs, Text, Textarea, promptDialog, type Tone } from "../ui";
import { Actor } from "../work";
import { LessonCard } from "./Lessons";
import "../styles/agents.css";

const TABS = ["facts", "lessons", "skills", "decided"] as const;
type Tab = typeof TABS[number];
const LABEL: Record<Tab, string> = { facts: "Facts", lessons: "Lessons", skills: "Skill drafts", decided: "Decided" };

export function KnowledgePage({ query }: { query: URLSearchParams }) {
  const data = useAsync(() => api.knowledgeReview(), []);
  const d = data.data;
  const asked = TABS.find((t) => t === query.get("tab"));
  // With no tab named, open on the first kind with something waiting.
  const tab: Tab = asked ?? (d ? (TABS.find((t) => t !== "decided" && d.counts[t] > 0) ?? "facts") : "facts");
  return (
    <section>
      <PageHeader>Knowledge</PageHeader>
      <Tabs label="What waits for review" items={TABS.map((t) => ({ key: t, current: tab === t, href: href("/knowledge", { tab: t }),
        label: t !== "decided" && d && d.counts[t] ? `${LABEL[t]} (${d.counts[t]})` : LABEL[t] }))} />
      <Status of={data} />
      {d && (tab === "facts" ? (d.facts.length ? d.facts.map((f) => <FactCard key={f.id} f={f} onDecided={data.reload} />)
        : <Empty>No learned fact is waiting. A fact Ops IQ learns from a conversation comes here before any answer uses it.</Empty>)
        : tab === "lessons" ? (d.lessons.length ? d.lessons.map((l) => <LessonCard key={l.id} lesson={l} onDecided={data.reload} />)
        : <Empty>No lesson is waiting.</Empty>)
        : tab === "skills" ? <SkillDrafts d={d.skills} />
        : <Decided rows={d.recent} />)}
    </section>
  );
}

const period = (f: ProposedFact) =>
  f.valid_from && f.valid_to ? `${f.valid_from} to ${f.valid_to}`
    : f.valid_from ? `since ${f.valid_from}` : f.valid_to ? `until ${f.valid_to}` : "Open";

/** One proposed fact: what it says, what it is about, where it came from and what it would end, then the decision. */
function FactCard({ f, onDecided }: { f: ProposedFact; onDecided: () => void }) {
  const operate = usePermission("operate");
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(f.text);
  const { busy, error, run } = useAction();
  const approve = () => run("Approving…", async () => {
    if (text.trim() && text.trim() !== f.text) await api.editFact(f.id, "", { text: text.trim() });
    return api.approveFact(f.id, "");
  }, onDecided);
  const reject = async () => {
    const note = await promptDialog("Why is this fact wrong?", { confirm: "Reject" });
    if (note !== null) await run("Rejecting…", () => api.rejectFact(f.id, "", note), onDecided);
  };
  return <Card className="knowledge-fact" title={editing ? "Edit the fact" : f.text}
    actions={<Text small muted>learned {when(f.learned_at)}</Text>}>
    {editing && <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} aria-label="Fact" />}
    <Facts items={[
      ["About", f.entities.length ? <>{f.entities.map((e, i) => <span key={e.id}>{i ? ", " : ""}<a href={entityHref(e.id)}>{e.label ?? e.id}</a></span>)}</> : "No entity"],
      ["Holds", period(f)],
      ["From", f.episode_question ? <>an answer to “{f.episode_question}”, <Actor who={f.by_user} /></> : <Actor who={f.by_user} />],
      f.replaced.length > 0 && ["Ends", <List plain small>{f.replaced.map((r) => <li key={r.id}><s>{r.text}</s></li>)}</List>],
    ]} />
    <ActionStatus busy={busy} error={error} />
    {operate && <Line wrap>
      <Button variant="primary" disabled={!!busy || !text.trim()} onClick={approve}>{editing && text.trim() !== f.text ? "Save and approve" : "Approve"}</Button>
      {!editing && <Button disabled={!!busy} onClick={() => setEditing(true)}>Edit wording</Button>}
      {editing && <Button disabled={!!busy} onClick={() => { setEditing(false); setText(f.text); }}>Cancel edit</Button>}
      <Button disabled={!!busy} onClick={reject}>Reject</Button>
    </Line>}
  </Card>;
}

function SkillDrafts({ d }: { d: SkillRow[] }) {
  if (!d.length) return <Empty>No skill draft is waiting.</Empty>;
  return <Card title="Drafts waiting">
    <Table density="compact" variant="results" className="phone-rows">
      <thead><tr><th>Skill</th><th>Draft</th><th>By</th><th>Note</th><th>Fell short</th></tr></thead>
      <tbody>{d.map((s) => s.draft && <tr key={s.skill}>
        <td><a href={href("/skills", { skill: s.skill, tab: "versions", v: String(s.draft.version) })}><strong>{s.title}</strong></a>
          <Text as="div" small muted>{s.owner_label}</Text></td>
        <td>v{s.draft.version} <Text small muted>{when(s.draft.at)}</Text></td>
        <td><Actor who={s.draft.by} /></td>
        <td>{s.draft.note ?? "–"}</td>
        <td>{s.shortfalls || "–"}</td>
      </tr>)}</tbody>
    </Table>
  </Card>;
}

const DECISION: Record<string, { text: string; tone: Tone }> = {
  current: { text: "Approved", tone: "ok" }, ended: { text: "Approved, since ended", tone: "muted" },
  approved: { text: "Approved", tone: "ok" }, rejected: { text: "Rejected", tone: "muted" },
  published: { text: "Published", tone: "ok" }, retired: { text: "Published, since retired", tone: "muted" },
  rolled_back: { text: "Rolled back", tone: "muted" }, refused: { text: "Set aside", tone: "muted" },
};
const KIND = { fact: "Fact", lesson: "Lesson", skill: "Skill" } as const;

function Decided({ rows }: { rows: KnowledgeDecision[] }) {
  if (!rows.length) return <Empty>Nothing decided yet.</Empty>;
  return <Card title="Latest decisions">
    <Table density="compact" className="phone-rows">
      <thead><tr><th>What</th><th>Decision</th><th>By</th><th>When</th><th>Why</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={`${r.kind}:${r.ref}`}>
        <td><Text small muted>{KIND[r.kind]}</Text> {r.kind === "skill"
          ? <a href={href("/skills", { skill: r.title, tab: "versions", v: r.ref.split(" v").pop() })}>{r.ref.split("/").pop()}</a> : r.title}</td>
        <td><Badge tone={DECISION[r.status]?.tone ?? "muted"}>{DECISION[r.status]?.text ?? r.status}</Badge></td>
        <td>{r.by ? <Actor who={r.by} /> : "–"}</td>
        <td>{when(r.at)}</td>
        <td>{r.note ?? "–"}</td>
      </tr>)}</tbody>
    </Table>
  </Card>;
}
