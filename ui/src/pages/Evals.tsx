/** Evals (Ops IQ): how well each skill answers its questions and how sound the summaries, report notes and daily
 * reports Fuzer kept are, with every run's history (agents/eval_runs). The checks need no model; the grade is the
 * model's reading of each result and waits, saying why, while no model may be called. Engineers keep each skill's
 * questions here: add one, change what a good answer must name, retire one. */
import { useEffect, useState } from "react";
import { api, type AiAvailability, type EvalResult, type EvalRun, type EvalSubject, type KeptQuestion } from "../api";
import { money, percent, when } from "../format";
import { Status, href, useAction, useAsync } from "../lib";
import { usePermission } from "../Auth";
import { Sparkline } from "../analytics/Metric";
import {
  ActionStatus, Badge, Button, Card, Disclosure, Empty, Facts, Field, Input, Line, List, Notice, PageHeader, Table, Tabs,
  Text, Textarea, type Tone,
} from "../ui";
import { Actor } from "../work";
import "../styles/agents.css";

const evalHref = (subject?: string, query: Record<string, string | undefined> = {}) => href("/evals", { subject, ...query });
const isSkill = (subject: string) => subject.startsWith("skill:");

export function EvalsPage({ query }: { query: URLSearchParams }) {
  const subject = query.get("subject");
  return subject ? <SubjectPage key={subject} subject={subject} query={query} /> : <Overview />;
}

/** The model's grade of a run: its score, or what it waits on. */
function GradeBadge({ run }: { run: Pick<EvalRun, "grade" | "grade_status"> }) {
  if (run.grade_status === "graded" && run.grade !== null) return <Badge tone={tone(run.grade)}>{percent(run.grade)}</Badge>;
  if (run.grade_status === "grading") return <Badge tone="info">Grading</Badge>;
  if (run.grade_status === "waiting") return <Badge tone="warn">Waiting</Badge>;
  if (run.grade_status === "failed") return <Badge tone="danger">Grade failed</Badge>;
  return <Text small muted>Not graded</Text>;
}

const tone = (score: number): Tone => (score >= 0.8 ? "ok" : score >= 0.6 ? "warn" : "danger");
const scoreText = (run: EvalRun) => run.status === "running" ? "Running" : run.status === "failed" ? "Failed"
  : run.score === null ? "–" : percent(run.score);

function AiNotice({ ai }: { ai: AiAvailability }) {
  return ai.available ? null : <Notice tone="warn">{ai.why} Checks still run; grades wait until a model may be called.</Notice>;
}

function Overview() {
  const data = useAsync(() => api.evals(), []);
  const subjects = data.data?.subjects ?? [];
  const groups = [...new Set(subjects.map((s) => s.group))];
  return (
    <section>
      <PageHeader>Evals</PageHeader>
      <Status of={data} />
      {data.data && <AiNotice ai={data.data.ai} />}
      {groups.map((g) => <Card key={g} title={g}>
        <Table density="compact" variant="results" className="phone-rows">
          <thead><tr><th>What</th><th>Latest</th><th>Checks</th><th>Grade</th><th>Trend</th></tr></thead>
          <tbody>{subjects.filter((s) => s.group === g).map((s) => <SubjectRow key={s.subject} s={s} />)}</tbody>
        </Table>
      </Card>)}
    </section>
  );
}

function SubjectRow({ s }: { s: EvalSubject }) {
  const r = s.latest;
  return <tr>
    <td><a href={evalHref(s.subject)}><strong>{s.label}</strong></a>
      {s.kind === "skill" && <Text as="div" small muted>{s.questions ? `${s.questions} questions` : "No questions"}</Text>}</td>
    <td>{r ? when(r.at) : "Never run"}</td>
    <td>{r ? scoreText(r) : "–"}</td>
    <td>{r ? <GradeBadge run={r} /> : "–"}</td>
    <td data-label="Trend"><Sparkline label={s.label} points={s.trend.map((v, i) => ({ at: String(i + 1), value: v }))} /></td>
  </tr>;
}

function SubjectPage({ subject, query }: { subject: string; query: URLSearchParams }) {
  const skill = isSkill(subject);
  const tab = skill && query.get("tab") === "questions" ? "questions" : "runs";
  const runId = query.get("run");
  const data = useAsync(() => api.evalSubject(subject), [subject]);
  const d = data.data;
  const ai = usePermission("ai");
  const operate = usePermission("operate");
  const { busy, error, run } = useAction();
  const running = !!d?.runs.some((r) => r.status === "running" || r.grade_status === "grading");
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(data.reload, 4000);
    return () => window.clearInterval(t);
  }, [running, data.reload]);
  const start = () => run("Starting…", () => skill ? api.runSkillEval(subject.slice(6)) : api.runOutputsEval(subject),
    (r) => { location.hash = evalHref(subject, { run: String(r.id) }); data.reload(); });
  // A skill's run asks a model; the checks on what the agents wrote ask none.
  const blocked = !d ? "Loading" : skill && !ai ? "Running a skill's eval asks a model: it needs AI access."
    : skill && !d.ai.available ? d.ai.why : skill && !d.questions?.some((q) => q.status === "active") ? "Add a question first." : null;
  return (
    <section>
      <PageHeader record detail={d && (skill ? "Answers to its eval questions" : "Checks on what the agents kept")}
        actions={<a href={evalHref()}>All evals</a>}>{d?.label ?? subject}</PageHeader>
      <Status of={data} />
      {d && <>
        <AiNotice ai={d.ai} />
        {(operate || ai) && <Line wrap>
          <Button variant="primary" disabled={!!busy || !!blocked} title={blocked ?? undefined} onClick={start}>Run now</Button>
          {blocked && blocked !== "Loading" && <Text small muted>{blocked}</Text>}
          {skill && <a href={href("/skills", { skill: subject.slice(6) })}>Open the skill</a>}
        </Line>}
        <ActionStatus busy={busy} error={error} />
        {skill && <Tabs label="Runs or questions" items={[
          { key: "runs", label: "Runs", current: tab === "runs", href: evalHref(subject) },
          { key: "questions", label: `Questions (${d.questions?.filter((q) => q.status === "active").length ?? 0})`,
            current: tab === "questions", href: evalHref(subject, { tab: "questions" }) },
        ]} />}
        {tab === "questions" ? <Questions skill={subject.slice(6)} questions={d.questions ?? []} onSaved={data.reload} />
          : <>
            {runId && <RunDetail key={runId} id={runId} subject={subject} onGraded={data.reload} />}
            <Runs runs={d.runs} subject={subject} current={runId} />
          </>}
      </>}
    </section>
  );
}

function Runs({ runs, subject, current }: { runs: EvalRun[]; subject: string; current: string | null }) {
  if (!runs.length) return <Empty>No run yet.</Empty>;
  return <Card title="Runs">
    <Table density="compact" className="phone-rows">
      <thead><tr><th>When</th><th>By</th><th>Checks</th><th>Grade</th><th>Cost</th></tr></thead>
      <tbody>{runs.map((r) => <tr key={r.id} aria-current={String(r.id) === current || undefined}>
        <td><a href={evalHref(subject, { run: String(r.id) })}>{when(r.at)}</a></td>
        <td>{r.origin === "recorded" ? <Text small muted>{r.by}</Text> : <Actor who={r.by ?? "anonymous"} />}</td>
        <td>{scoreText(r)}{isSkill(subject) && r.scores.version ? <Text small muted> · v{r.scores.version}</Text> : null}</td>
        <td><GradeBadge run={r} /></td>
        <td>{r.cost_usd ? money(r.cost_usd) : "–"}</td>
      </tr>)}</tbody>
    </Table>
  </Card>;
}

function RunDetail({ id, subject, onGraded }: { id: string; subject: string; onGraded: () => void }) {
  const data = useAsync(() => api.evalRun(id), [id]);
  const r = data.data;
  const ai = usePermission("ai");
  const { busy, error, run } = useAction();
  const live = r?.status === "running" || r?.grade_status === "grading";
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(data.reload, 4000);
    return () => window.clearInterval(t);
  }, [live, data.reload]);
  const grade = () => run("Grading…", () => api.gradeEvalRun(Number(id)), () => { data.reload(); onGraded(); });
  const s = r?.scores ?? {};
  return <Card title={r ? `Run of ${when(r.at)}` : "Run"} actions={<a href={evalHref(subject)}>Close</a>}>
    <Status of={data} />
    {r && <>
      <Facts grid items={[
        ["Checks", isSkill(subject) ? <>{scoreText(r)}{r.status === "running" && ` (${s.asked ?? 0} of ${s.questions ?? "?"} asked)`}</>
          : <>{s.passed ?? 0} of {s.items ?? 0} passed</>],
        ["Grade", <GradeBadge run={r} />],
        isSkill(subject) && s.routed != null && ["Right specialist", percent(s.routed as number)],
        !isSkill(subject) && !!s.unknown_numbers && ["Numbers not in the facts", s.unknown_numbers],
        !isSkill(subject) && !!s.operating_instructions && ["Operating instructions", s.operating_instructions],
        ["By", r.origin === "recorded" ? r.by : <Actor who={r.by ?? "anonymous"} />],
        r.grade_model && ["Graded by", <Text mono small>{r.grade_model}</Text>],
        r.cost_usd > 0 && ["Cost", money(r.cost_usd)],
      ]} />
      {r.error && <Notice tone="danger">{r.error}</Notice>}
      {r.grade_why && <Text as="div" small muted>{r.grade_why}</Text>}
      {r.origin === "run" && r.status === "done" && (r.grade_status === "waiting" || r.grade_status === "failed") && !!r.results?.length && <Line wrap>
        <Button disabled={!!busy || !ai} title={ai ? undefined : "Grading asks a model: it needs AI access."} onClick={grade}>Grade now</Button>
      </Line>}
      <ActionStatus busy={busy} error={error} />
      {r.results?.length ? r.results.map((x, i) => <Result key={i} x={x} />) : r.status === "done" && <Empty>Nothing to check.</Empty>}
    </>}
  </Card>;
}

const fact = (f: string | string[]) => (Array.isArray(f) ? f.join(" or ") : f);

function Result({ x }: { x: EvalResult }) {
  const issues = x.checks ? [
    ...(x.checks.uncited_points ?? []).map((t) => `Cites nothing: ${t}`),
    ...(x.checks.uncited_lines ?? []).map((t) => `Cites nothing: ${t}`),
    ...(x.checks.unknown_citations ?? []).map((t) => `Cites what the facts don't hold: ${t}`),
    ...(x.checks.unknown_numbers ?? []).map((t) => `A number not in the facts: ${t}`),
    ...(x.checks.operating_instructions ?? []).map((t) => `Tells someone to operate the plant: ${t}`),
    ...(x.checks.long_headline ? ["The headline is long"] : []),
  ] : [];
  const passed = x.checks ? x.checks.ok : x.mentions === 1 && x.routed;
  return <div className="eval-result">
    <Line wrap>
      <Badge tone={passed ? "ok" : "warn"}>{x.checks ? (passed ? "Checks pass" : "Fails a check")
        : x.mentions == null ? "Not scored" : `${percent(x.mentions)} named`}</Badge>
      {x.grade?.score != null && <Badge tone={x.grade.score >= 4 ? "ok" : x.grade.score >= 3 ? "warn" : "danger"}>Grade {x.grade.score} of 5</Badge>}
      <strong>{x.href ? <a href={x.href}>{x.label}</a> : x.question ?? x.label}</strong>
    </Line>
    {x.grade?.why && <Text as="div" small>{x.grade.why}</Text>}
    {(x.missing?.length ?? 0) > 0 && <Text as="div" small muted>Missed: {x.missing!.map(fact).join("; ")}</Text>}
    {x.routed === false && <Text as="div" small muted>Not answered by the expected specialist{x.specialists?.length ? ` (asked ${x.specialists.join(", ")})` : ""}.</Text>}
    {issues.length > 0 && <List small>{issues.map((t, i) => <li key={i}>{t}</li>)}</List>}
    {(x.answer || x.text) && <Disclosure label={x.answer ? "Answer" : "What was kept"}><div className="eval-text">{x.answer ?? x.text}</div></Disclosure>}
  </div>;
}

const ORIGIN: Record<KeptQuestion["origin"], { text: string; tone: Tone }> = {
  shipped: { text: "Shipped", tone: "muted" }, added: { text: "Added", tone: "info" }, case: { text: "From a case", tone: "warn" },
};

function Questions({ skill, questions, onSaved }: { skill: string; questions: KeptQuestion[]; onSaved: () => void }) {
  const operate = usePermission("operate");
  const [adding, setAdding] = useState(false);
  const active = questions.filter((q) => q.status === "active");
  const retired = questions.filter((q) => q.status === "retired");
  return <>
    {operate && (adding ? <QuestionForm skill={skill} q={null} onDone={() => { setAdding(false); onSaved(); }} onCancel={() => setAdding(false)} />
      : <Line><Button onClick={() => setAdding(true)}>Add a question</Button></Line>)}
    {active.length ? active.map((q, i) => <QuestionCard key={q.key ?? `case:${i}`} skill={skill} q={q} onSaved={onSaved} />)
      : <Empty>No question is active. A skill with no questions can't show how it answers.</Empty>}
    {retired.length > 0 && <Disclosure label={`Retired (${retired.length})`}>
      {retired.map((q) => <QuestionCard key={q.key} skill={skill} q={q} onSaved={onSaved} />)}
    </Disclosure>}
  </>;
}

function QuestionCard({ skill, q, onSaved }: { skill: string; q: KeptQuestion; onSaved: () => void }) {
  const operate = usePermission("operate");
  const [editing, setEditing] = useState(false);
  const { busy, error, run } = useAction();
  if (editing) return <QuestionForm skill={skill} q={q} onDone={() => { setEditing(false); onSaved(); }} onCancel={() => setEditing(false)} />;
  const toggle = () => run(q.status === "active" ? "Retiring…" : "Restoring…",
    () => q.status === "active" ? api.retireEvalQuestion(skill, q.key!) : api.restoreEvalQuestion(skill, q.key!), onSaved);
  return <Card title={q.question} actions={<Line>
    <Badge tone={ORIGIN[q.origin].tone}>{ORIGIN[q.origin].text}{q.version ? ` v${q.version}` : ""}</Badge>
    {q.edited && <Badge tone="info">Edited</Badge>}
  </Line>}>
    <Facts items={[
      ["Must name", q.must_mention.length ? q.must_mention.map(fact).join("; ") : "Nothing named"],
      q.specialists.length > 0 && ["Answered by", q.specialists.join(", ")],
      q.by && ["Changed", <><Actor who={q.by} /> {q.at && when(q.at)}</>],
      q.note && ["Note", q.note],
    ]} />
    <ActionStatus busy={busy} error={error} />
    {operate && q.key && <Line wrap>
      {q.status === "active" && <Button disabled={!!busy} onClick={() => setEditing(true)}>Edit</Button>}
      <Button disabled={!!busy} onClick={toggle}>{q.status === "active" ? "Retire" : "Restore"}</Button>
    </Line>}
    {!q.key && <Text as="div" small muted>Drawn from the cases behind a published version, so it is kept with that version and not edited here.</Text>}
  </Card>;
}

/** One fact a good answer must name per line; alternatives on one line split by " | ". */
const toLines = (m: (string | string[])[]) => m.map((f) => (Array.isArray(f) ? f.join(" | ") : f)).join("\n");
const fromLines = (t: string) => t.split("\n").map((l) => l.trim()).filter(Boolean)
  .map((l) => (l.includes("|") ? l.split("|").map((x) => x.trim()).filter(Boolean) : l));

function QuestionForm({ skill, q, onDone, onCancel }: { skill: string; q: KeptQuestion | null; onDone: () => void; onCancel: () => void }) {
  const [question, setQuestion] = useState(q?.question ?? "");
  const [must, setMust] = useState(toLines(q?.must_mention ?? []));
  const [specialists, setSpecialists] = useState((q?.specialists ?? []).join(", "));
  const { busy, error, run } = useAction();
  const save = () => run("Saving…", () => api.saveEvalQuestion({
    skill, key: q?.key ?? null, question: question.trim(), must_mention: fromLines(must),
    specialists: specialists.split(",").map((s) => s.trim()).filter(Boolean),
  }), onDone);
  return <Card title={q ? "Edit the question" : "Add a question"}>
    <Field label="Question"><Textarea rows={2} value={question} onChange={(e) => setQuestion(e.target.value)} /></Field>
    <Field label="Must name" hint="One fact per line. Put alternatives on one line split by |, such as: 20-XV-3100 | export valve">
      <Textarea rows={4} value={must} onChange={(e) => setMust(e.target.value)} /></Field>
    <Field label="Answered by" hint="The specialists that should answer it, split by commas. Leave empty for any.">
      <Input value={specialists} onChange={(e) => setSpecialists(e.target.value)} /></Field>
    <ActionStatus busy={busy} error={error} />
    <Line wrap>
      <Button variant="primary" disabled={!!busy || !question.trim()} onClick={save}>Save</Button>
      <Button disabled={!!busy} onClick={onCancel}>Cancel</Button>
    </Line>
  </Card>;
}
