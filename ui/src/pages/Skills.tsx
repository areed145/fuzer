/** Skills (Ops IQ): the playbooks the agents follow, read and changed by the engineers who own them. A change is a
 * draft; it goes live when a person publishes it behind the skill's eval (agents/skill_gate), and one action rolls it
 * back. Every version stays in the skill's history. */
import { useState } from "react";
import { api, type RecommendationsTaken, type SkillDetail, type SkillRow, type SkillVersion } from "../api";
import { percent, when } from "../format";
import { Status, href, useAction, useAsync } from "../lib";
import { Markdown } from "../Markdown";
import { usePermission } from "../Auth";
import {
  ActionStatus, Badge, Button, Card, ConfirmAction, Empty, Facts, Field, Input, Line, List, PageHeader, Table, Tabs,
  Text, Textarea, promptDialog, type Tone,
} from "../ui";
import { Actor } from "../work";
import "../styles/agents.css";

const skillHref = (skill: string, query: Record<string, string | undefined> = {}) => href("/skills", { skill, ...query });

export function SkillsPage({ query }: { query: URLSearchParams }) {
  const skill = query.get("skill");
  return skill ? <SkillPage key={skill} skill={skill} query={query} /> : <SkillList show={query.get("show") ?? "all"} />;
}

const SHOWS = [
  { key: "all", label: "All" },
  { key: "draft", label: "Draft waiting" },
  { key: "short", label: "Fell short" },
] as const;

function SkillList({ show }: { show: string }) {
  const data = useAsync(() => api.skills(), []);
  const rows = (data.data?.skills ?? []).filter((s) => show === "draft" ? !!s.draft : show === "short" ? s.shortfalls > 0 : true);
  const areas = [...new Set(rows.map((s) => s.area_label))];
  return (
    <section>
      <PageHeader>Skills</PageHeader>
      <Tabs label="Which skills" items={SHOWS.map((s) => ({ key: s.key, label: s.label, current: show === s.key,
        href: href("/skills", s.key === "all" ? {} : { show: s.key }) }))} />
      <Status of={data} empty={!!data.data && rows.length === 0}>
        {show === "draft" ? "No draft is waiting." : show === "short" ? "No skill fell short since its live version." : "No skills."}
      </Status>
      {areas.map((area) => <Card key={area} title={area}>
        <Table density="compact" variant="results" className="phone-rows skills-table">
          <thead><tr><th>Skill</th><th>Live</th><th>Waiting</th><th>Fell short</th><th>Recommendation taken</th><th>Eval</th></tr></thead>
          <tbody>{rows.filter((s) => s.area_label === area).map((s) => <SkillListRow key={s.skill} s={s} />)}</tbody>
        </Table>
      </Card>)}
    </section>
  );
}

function SkillListRow({ s }: { s: SkillRow }) {
  return <tr>
    <td><a href={skillHref(s.skill)}><strong>{s.title}</strong></a>
      <Text as="div" small muted>{s.purpose}</Text></td>
    <td>{s.live.version ? <>v{s.live.version} <Text small muted>{when(s.live.at)}</Text></> : "As shipped"}</td>
    <td>{s.draft ? <a href={skillHref(s.skill, { tab: "versions", v: String(s.draft.version) })}>
      <Badge tone="warn">Draft v{s.draft.version}</Badge></a> : "–"}</td>
    <td>{s.shortfalls ? <a href={skillHref(s.skill, { tab: "short" })}>{s.shortfalls}</a> : "–"}</td>
    <td>{takenText(s.taken)}</td>
    <td>{s.questions ? <>{s.questions} questions{s.score !== null && <Text small muted> · {percent(s.score)} at publish</Text>}</> : "No questions"}</td>
  </tr>;
}

const TABS = [
  { key: "playbook", label: "Playbook" },
  { key: "versions", label: "Versions" },
  { key: "short", label: "Fell short" },
] as const;

function SkillPage({ skill, query }: { skill: string; query: URLSearchParams }) {
  const tab = TABS.find((t) => t.key === query.get("tab"))?.key ?? "playbook";
  const data = useAsync(() => api.skill(skill), [skill]);
  const versions = useAsync(() => api.skillVersions(skill), [skill]);
  const [editing, setEditing] = useState<{ text: string; from: SkillVersion | null } | null>(null);
  const d = data.data;
  const reload = () => { data.reload(); versions.reload(); };
  return (
    <section>
      <PageHeader record detail={d && <>{d.owner_label} · {d.live.version ? `v${d.live.version} live since ${when(d.live.at)}` : "as shipped"}</>}
        actions={<a href={href("/skills")}>All skills</a>}>
        {d?.title ?? "Skill"}
      </PageHeader>
      <Status of={data} />
      {d && <>
        <Tabs label="Skill" items={TABS.map((t) => ({ key: t.key, label: t.key === "short" && d.shortfalls.length ? `${t.label} (${d.shortfalls.length})` : t.label,
          current: tab === t.key, href: skillHref(skill, t.key === "playbook" ? {} : { tab: t.key }) }))} />
        {editing ? <Editor d={d} start={editing.text} from={editing.from} onDone={(v) => {
          setEditing(null);
          reload();
          if (v !== null) location.hash = skillHref(skill, { tab: "versions", v: String(v) });
        }} />
          : tab === "playbook" ? <Playbook d={d} onEdit={() => setEditing({ text: d.playbook, from: null })} />
          : tab === "versions" ? <Versions d={d} versions={versions} chosen={query.get("v")} reload={reload}
              onEdit={(v) => setEditing({ text: v.playbook, from: v })} />
          : <Shortfalls d={d} />}
      </>}
    </section>
  );
}

function Playbook({ d, onEdit }: { d: SkillDetail; onEdit: () => void }) {
  const operate = usePermission("operate");
  return <>
    <Card title="About" actions={operate && <Button variant="primary" onClick={onEdit}>Edit playbook</Button>}>
      <Facts grid items={[
        ["What it covers", d.purpose],
        ["Loaded by", d.agents.join(", ")],
        d.personas.length > 0 && ["Written for", d.personas.join(", ")],
        d.pack && d.pack.classes.length > 0 && ["Equipment classes", d.pack.classes.join(", ")],
        ["Tools it names", d.named_tools.length ? d.named_tools.join(", ") : "None"],
        ["Eval questions", d.questions.length ? String(d.questions.length) : "None"],
      ]} />
    </Card>
    <Card title={d.live.version ? `Playbook v${d.live.version}` : "Playbook as shipped"} className="skill-playbook">
      <Markdown text={d.body} />
    </Card>
  </>;
}

/** The playbook in a text box; saving it makes a draft, never a live version. */
function Editor({ d, start, from, onDone }: { d: SkillDetail; start: string; from: SkillVersion | null; onDone: (version: number | null) => void }) {
  const [text, setText] = useState(start);
  const [note, setNote] = useState("");
  const { busy, error, run } = useAction();
  const save = () => run("Saving the draft…", () => api.draftSkill(d.skill, text, "", note.trim(), from?.version), (r) => onDone(r.version));
  return <Card title={from ? `Edit from draft v${from.version}` : `Edit the playbook${d.live.version ? ` (v${d.live.version})` : ""}`}>
    <Field label="Playbook">
      <Textarea className="skill-editor" value={text} onChange={(e) => setText(e.target.value)} rows={24} spellCheck />
    </Field>
    <Field label="What changed and why">
      <Input value={note} onChange={(e) => setNote(e.target.value)} width="full" maxLength={300} />
    </Field>
    <Text as="p" small>Saving makes a new draft. It goes live only when someone publishes it behind the skill's eval{from ? `, and it replaces draft v${from.version}` : ""}.</Text>
    <ActionStatus busy={busy} error={error} />
    <Line wrap>
      <Button variant="primary" disabled={!!busy || text === start || !text.trim()} onClick={save}>Save draft</Button>
      <Button disabled={!!busy} onClick={() => onDone(null)}>Cancel</Button>
    </Line>
  </Card>;
}

const STATUS: Record<SkillVersion["status"], { text: string; tone: Tone }> = {
  draft: { text: "Draft", tone: "warn" },
  published: { text: "Live", tone: "ok" },
  retired: { text: "Retired", tone: "muted" },
  rolled_back: { text: "Rolled back", tone: "muted" },
  refused: { text: "Set aside", tone: "muted" },
};

function Versions({ d, versions, chosen, reload, onEdit }: {
  d: SkillDetail; versions: ReturnType<typeof useAsync<SkillVersion[]>>; chosen: string | null; reload: () => void;
  onEdit: (v: SkillVersion) => void;
}) {
  const list = versions.data ?? [];
  const pick = list.find((v) => String(v.version) === chosen) ?? list.find((v) => v.status === "draft") ?? list.find((v) => v.status === "published");
  return <>
    <Status of={versions} />
    {list.length > 0 && <Card title="History">
      <Table density="compact" variant="results" className="phone-rows">
        <thead><tr><th>Version</th><th>State</th><th>By</th><th>Note</th><th>Eval at publish</th></tr></thead>
        <tbody>{list.map((v) => <tr key={v.version} aria-current={v === pick ? "true" : undefined}>
          <td><a href={skillHref(d.skill, { tab: "versions", v: String(v.version) })}><strong>v{v.version}</strong></a>
            {v.version === 0 ? null : <Text as="div" small muted>{when(v.published_at ?? v.created_at)}</Text>}</td>
          <td><Badge tone={STATUS[v.status].tone}>{STATUS[v.status].text}</Badge></td>
          <td>{v.published_by ? <Actor who={v.published_by} /> : v.created_by ? <Actor who={v.created_by} /> : "Shipped"}</td>
          <td>{v.note ?? "–"}</td>
          <td>{v.eval && "questions" in v.eval ? <GateLine g={v.eval} /> : "–"}</td>
        </tr>)}</tbody>
      </Table>
    </Card>}
    {pick && <VersionCard key={pick.version} d={d} v={pick} live={list.find((x) => x.status === "published")} reload={reload} onEdit={onEdit} />}
  </>;
}

function GateLine({ g }: { g: NonNullable<SkillVersion["eval"]> }) {
  if (!("questions" in g)) return null;
  if (!g.questions) return <Text small muted>No questions</Text>;
  return <>{percent(g.candidate?.score ?? 0)} <Text small muted>against {percent(g.live?.score ?? 0)} on {g.questions}</Text></>;
}

/** One version against the live playbook, with what can be done to it. */
function VersionCard({ d, v, live, reload, onEdit }: { d: SkillDetail; v: SkillVersion; live?: SkillVersion; reload: () => void; onEdit: (v: SkillVersion) => void }) {
  const diff = useAsync(() => api.skillDiff(d.skill, v.version), [d.skill, v.version]);
  const operate = usePermission("operate"), ai = usePermission("ai");
  const { busy, error, run } = useAction();
  const [refusal, setRefusal] = useState<string | null>(null);
  const isLive = v.status === "published";
  const n = diff.data?.questions ?? 0;
  const publish = () => {
    setRefusal(null);
    return run("Asking the eval questions of both versions…", async () => {
      try { return await api.publishSkill(d.skill, v.version, ""); }
      catch (e) {
        const body = (e as { body?: { detail?: { refused?: string } } }).body;
        if (body?.detail?.refused) { setRefusal(body.detail.refused); return null; }
        throw e;
      }
    }, reload);
  };
  const setAside = async () => {
    const note = await promptDialog(`Why set draft v${v.version} aside?`, { confirm: "Set aside" });
    if (note !== null) await run("Setting the draft aside…", () => api.setSkillAside(d.skill, v.version, "", note), reload);
  };
  const cantPublish = !ai ? "Publishing asks the eval questions of the agents, which needs AI access."
    : n > 0 && !d.ai.available ? d.ai.why : null;
  return <Card title={isLive ? `v${v.version}, live` : v.version === 0 ? "As shipped" : `v${v.version} against ${diff.data?.against ? `v${diff.data.against}` : "the shipped playbook"}`}
    actions={diff.data && !isLive && <Text small muted>{diff.data.added} lines added, {diff.data.removed} removed</Text>}>
    {v.cases.length > 0 && <Text as="p" small>Drawn from {v.cases.length === 1 ? "one case" : `${v.cases.length} cases`} where the skill fell short.</Text>}
    {"questions" in (v.eval ?? {}) && <GateDetail g={v.eval as NonNullable<SkillVersion["eval"]>} />}
    <Status of={diff} />
    {diff.data && (isLive || v.version === 0 && !live
      ? <Text as="p" small>The agents load this version.</Text>
      : <Diff lines={diff.data.lines} />)}
    {refusal && <Text as="p" small role="alert">{refusal}</Text>}
    <ActionStatus busy={busy} error={error} />
    {operate && <Line wrap>
      {v.status === "draft" && <>
        <ConfirmAction label="Publish" busy={!!busy} disabled={!!cantPublish}
          consequence={n ? `This asks ${n} eval questions of the live version and of this draft (${2 * n} answers from the agents). It goes live only if it scores no lower, and the live version is retired.`
            : "This skill has no eval questions, so the draft goes live without an eval. The live version is retired."}
          onConfirm={publish} />
        <Button disabled={!!busy} onClick={() => onEdit(v)}>Edit from this draft</Button>
        <Button disabled={!!busy} onClick={setAside}>Set aside</Button>
      </>}
      {isLive && v.version > 0 && <ConfirmAction label="Roll back" busy={!!busy}
        consequence={`v${v.version} is rolled back and the version live before it is live again. Nothing is asked of the agents.`}
        onConfirm={() => run("Rolling back…", () => api.rollBackSkill(d.skill, ""), reload)} />}
    </Line>}
    {operate && v.status === "draft" && cantPublish && <Text as="p" small>{cantPublish}</Text>}
  </Card>;
}

function GateDetail({ g }: { g: NonNullable<SkillVersion["eval"]> }) {
  if (!("questions" in g) || !g.questions || !g.candidate || !g.live) return null;
  return <>
    <Facts items={[
      ["Eval at publish", `${percent(g.candidate.score)} against ${percent(g.live.score)} for v${g.live_version}, on ${g.questions} questions`],
      ["Facts named", `${percent(g.candidate.mentions)} (was ${percent(g.live.mentions)})`],
      ["Right specialist", `${percent(g.candidate.routed)} (was ${percent(g.live.routed)})`],
    ]} />
    {g.candidate.missing.length > 0 && <List small>
      {g.candidate.missing.map((m) => <li key={m.question}>{m.question} <Text muted>missed {m.missing.map((x) => Array.isArray(x) ? x.join(" or ") : x).join(", ")}</Text></li>)}
    </List>}
  </>;
}

type DiffLine = { op: "same" | "add" | "remove"; text: string };

/** Unchanged stretches fold to the two lines either side of a change, so a short change in a long playbook reads at a
 * glance. */
function folded(lines: DiffLine[]): (DiffLine | { op: "gap"; n: number })[] {
  const out: (DiffLine | { op: "gap"; n: number })[] = [];
  for (let i = 0; i < lines.length;) {
    if (lines[i].op !== "same") { out.push(lines[i++]); continue; }
    let j = i;
    while (j < lines.length && lines[j].op === "same") j++;
    const head = i === 0 ? 0 : 2, tail = j === lines.length ? 0 : 2;
    if (j - i <= head + tail + 1) out.push(...lines.slice(i, j));
    else out.push(...lines.slice(i, i + head), { op: "gap", n: j - i - head - tail }, ...lines.slice(j - tail, j));
    i = j;
  }
  return out;
}

function Diff({ lines }: { lines: DiffLine[] }) {
  if (!lines.some((l) => l.op !== "same")) return <Empty>No change from the live playbook.</Empty>;
  return <div className="skill-diff" role="group" aria-label="Changes against the live playbook">
    {folded(lines).map((l, i) => l.op === "gap"
      ? <div key={i} className="skill-diff-gap">{l.n} unchanged lines</div>
      : <div key={i} className={`skill-diff-${l.op}`}><span aria-hidden="true">{l.op === "add" ? "+" : l.op === "remove" ? "−" : " "}</span>
        <span className="sr-only">{l.op === "add" ? "Added: " : l.op === "remove" ? "Removed: " : ""}</span>{l.text || " "}</div>)}
  </div>;
}

/** A skill's recommendations taken, as the API counted them: the share and how many were decided on. */
const takenText = (t: RecommendationsTaken | null | undefined) => (t ? `${percent(t.rate)} of ${t.decided}` : "–");

function Shortfalls({ d }: { d: SkillDetail }) {
  if (!d.shortfalls.length) return <Empty>Nothing fell short since {d.live.version ? `v${d.live.version} went live` : "it shipped"}.</Empty>;
  return <Card title={`Since ${d.live.version ? `v${d.live.version} went live` : "it shipped"}`}
    detail={d.taken ? `Recommendation taken ${takenText(d.taken)}` : undefined}>
    <List plain>
      {d.shortfalls.map((s, i) => <li key={i} className="skill-shortfall">
        {s.kind === "answer"
          ? <><strong>{s.question}</strong> <Badge tone="warn">answer rated not useful</Badge></>
          : <><a href={href(`/cases/${s.case}`)}>{s.title ?? `Case ${s.case}`}</a>{" "}
            <Badge tone="warn">{s.kind === "option" ? "recommended option not taken"
              : `brief ${s.followed === "no" ? "not followed" : "partly followed"}`}</Badge></>}
        {s.note && <Text as="p" small>“{s.note}”</Text>}
        <Text as="div" small muted><Actor who={s.by} />, {when(s.at)}</Text>
      </li>)}
    </List>
  </Card>;
}

