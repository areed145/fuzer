import "../styles/ask.css";
import { ActionStatus, Button, ButtonLabel, ConfirmAction, PageHeader, Card as UICard, Input , Badge as UIBadge} from "../ui";
import { useEffect, useRef, useState } from "react";
import { api, type AssistantCatalog, type AssistantJob, type Attachment, type SavedTurn } from "../api";
import { Markdown } from "../Markdown";
import { href, useAsync } from "../lib";
import { loadPerson } from "../work";
import { DemoContext } from "../DemoContext";
import { usePermission } from "../Auth";

const EXAMPLES: [string, string][] = [
  ["Troubleshooting", "Which equipment has repeated trips, what evidence explains them, and what should be reviewed?"],
  ["Production", "How has production efficiency trended over the last 12 months, and what drove the worst month?"],
  ["Constraints", "What is limiting production right now, and how does it cascade through the field?"],
  ["Wells", "Which wells have lost the most oil rate this year, and why?"],
  ["Reliability", "Who are our bad actors, and what are their repeat failure modes?"],
  ["Work orders", "What in the work order backlog should we tackle first?"],
  ["Turnaround", "What is in scope for the next turnaround, and what is missing from it?"],
  ["Job readiness", "Which open jobs have spares out of stock, and what can't Fuzer tell us about their readiness?"],
  ["Incidents", "Give me the recorded timeline and corrective work for the latest incident on 20-K-2200B."],
  ["Reporting", "Summarize the latest period's production, losses and work backlog with their dates and gaps."],
  ["Across domains", "Why does lift gas keep running short, and what should go in the next turnaround to fix it?"],
];

// The signed-in actor, or the attribution name in explicit local mode.
const asker = () => loadPerson() || "anonymous";

const TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const MAX_IMAGES = 4;
const MAX_BYTES = 5 * 1024 * 1024;

/** An image file as an attachment (base64 for the API, an object URL for the preview). */
function attach(file: File): Promise<Attachment> {
  return new Promise((resolve, reject) => {
    if (!TYPES.includes(file.type)) return reject(new Error(`${file.name}: PNG, JPEG, GIF or WebP only`));
    if (file.size > MAX_BYTES) return reject(new Error(`${file.name}: larger than 5 MB`));
    const r = new FileReader();
    r.onload = () => resolve({ name: file.name, media_type: file.type as Attachment["media_type"],
                               data: String(r.result).split(",")[1], url: URL.createObjectURL(file) });
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

interface Turn {
  question: string;
  images?: Attachment[];
  imageUrls?: string[]; // saved images of a reopened conversation
  job?: AssistantJob;
  jobId?: string;
  error?: string;
}

export function AskPage({ query }: { query: URLSearchParams }) {
  const demo = query.get("demo") === "agents";
  const ai = usePermission("ai");
  const catalog = useAsync(() => api.assistantCatalog(), []);
  const labels = Object.fromEntries((catalog.data?.agents ?? []).map((a) => [a.id, a.label]));
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversation, setConversation] = useState<string>();
  const [listed, setListed] = useState(0); // bumped to reload the conversation list
  const [openError, setOpenError] = useState<string>();
  const reopen = async (id: string) => {
    setOpenError(undefined);
    try {
      const c = await api.conversation(id);
      setConversation(c.id);
      setTurns(c.turns.map((t) => saved(c.id, t)));
    } catch (e) {
      setOpenError(String(e));
    }
  };
  const [text, setText] = useState(query.get("q") ?? "");
  useEffect(() => { if (query.has("q")) setText(query.get("q") ?? ""); }, [query.get("q")]);
  const [images, setImages] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState<string>();
  const add = async (files: File[]) => {
    setAttachError(undefined);
    try {
      const got = await Promise.all(files.slice(0, MAX_IMAGES - images.length).map(attach));
      setImages((xs) => [...xs, ...got].slice(0, MAX_IMAGES));
    } catch (e) {
      setAttachError(String((e as Error).message ?? e));
    }
  };
  const busy = turns.some((t) => t.jobId && (!t.job || t.job.status === "running") && !t.error);
  const end = useRef<HTMLDivElement>(null);

  const ask = async (question: string) => {
    if (!question.trim() || busy || !ai) return;
    const sent = images;
    setText("");
    setImages([]);
    const n = turns.length;
    setTurns((ts) => [...ts, { question, images: sent }]);
    try {
      const started = await api.ask(question, conversation, asker(), sent);
      setConversation(started.conversation_id);
      setTurns((ts) => ts.map((t, i) => (i === n ? { ...t, jobId: started.job_id } : t)));
    } catch (e) {
      setTurns((ts) => ts.map((t, i) => (i === n ? { ...t, error: String(e) } : t)));
    }
  };

  // Poll the running job.
  const running = turns.findIndex((t) => t.jobId && (!t.job || t.job.status === "running") && !t.error);
  useEffect(() => {
    if (running < 0) return;
    const id = turns[running].jobId!;
    const timer = setTimeout(async () => {
      try {
        const job = await api.assistantJob(id);
        setTurns((ts) => ts.map((t, i) => (i === running ? { ...t, job } : t)));
      } catch (e) {
        setTurns((ts) => ts.map((t, i) => (i === running ? { ...t, error: String(e) } : t)));
      }
    }, 1200);
    return () => clearTimeout(timer);
  }, [running, turns]);

  const finished = turns.filter((t) => t.job && t.job.status !== "running").length;
  useEffect(() => setListed((n) => n + 1), [finished]);

  useEffect(() => {
    end.current?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "end",
    });
  }, [turns.length]);

  return (
    <section className="ask">
      <PageHeader>Ask</PageHeader>
      {demo && <DemoContext step="agents" />}
      {!ai && <p>Questions require AI access from an owner. No model call will be made with your current access.</p>}
      {ai && catalog.data?.provider_configured === false && (
        <UICard className="ask-no-provider" role="note">
          <strong>No AI provider is configured.</strong>{" "}
          The assistant needs an agents provider and API key{catalog.data.provider ? ` (selected: ${catalog.data.provider})` : ""}, so
          questions sent now will fail. An owner sets them in <a href={href("/settings")}>application settings</a>. Fuzer has
          not tested any provider to show this.
        </UICard>
      )}
      <p className="muted">
        A read-only operations assistant routes questions to domain specialists and answers from the evidence
        available in Fuzer's linked data. Every fact links to where it came from. It never acts on the plant.
      </p>
      {turns.length === 0 && (
        <div className="ask-examples">
          {EXAMPLES.map(([label, q]) => (
            <Button key={label} className="example" disabled={!ai} onClick={() => demo ? setText(q) : ask(q)}>
              <span className="muted small">{label}</span>
              <span>{q}</span>
            </Button>
          ))}
        </div>
      )}
      {openError && <div className="error small">{openError}</div>}
      <Specialists catalog={catalog} />
      {turns.map((t, i) => <TurnView key={i} turn={t} labels={labels} demo={demo} />)}
      <div ref={end} />
      <form className="ask-box card" onSubmit={(e) => { e.preventDefault(); if (!demo) ask(text); }}>
        <textarea aria-label="Question for the operations assistant" rows={3} value={text} placeholder={turns.length ? "Ask a follow-up…" : "Ask about the plant…"}
                  onChange={(e) => setText(e.target.value)}
                  onPaste={(e) => {
                    const files = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/"));
                    if (files.length) {
                      e.preventDefault();
                      add(files);
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      if (!demo) ask(text);
                    }
                  }} />
        {images.length > 0 && (
          <div className="attachments">
            {images.map((im, k) => (
              <span key={im.url} className="attachment">
                <img src={im.url} alt={im.name} />
                <Button type="button" aria-label={`Remove ${im.name}`}
                        onClick={() => setImages((xs) => xs.filter((_, j) => j !== k))}>×</Button>
              </span>
            ))}
          </div>
        )}
        {attachError && <div className="error small">{attachError}</div>}
        <div className="row">
          {demo ? <ConfirmAction label="paid question" disabled={!ai || !text.trim()} busy={busy}
            consequence="This sends the reviewed question and any attachments to the configured AI provider and may incur charges. The answer is stored in your account's conversation. Inspect any recorded job after a failure before a deliberate retry."
            onConfirm={() => ask(text)} />
            : <Button variant="primary" type="submit" disabled={!ai || busy || !text.trim()}>{busy ? "Working…" : "Ask"}</Button>}
          <ButtonLabel title="A screenshot, a photo of a spectrum, a datasheet page (or paste one)">
            Attach image
            <Input type="file" accept={TYPES.join(",")} multiple hidden disabled={images.length >= MAX_IMAGES}
                   onChange={(e) => { add(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
          </ButtonLabel>
          {turns.length > 0 && (
            <Button type="button" variant="link" disabled={busy}
                    onClick={() => { setTurns([]); setConversation(undefined); }}>New conversation</Button>
          )}
          <span className="spacer" />
          <span className="muted small">Enter to send · Shift+Enter for a new line · paste images</span>
        </div>
      </form>
      <Conversations current={conversation} reload={listed} busy={busy} onOpen={reopen}
                     readOnly={demo}
                     onDeleted={(id) => { if (id === conversation) { setTurns([]); setConversation(undefined); } }} />
    </section>
  );
}

type Catalog = ReturnType<typeof useAsync<AssistantCatalog>>;

/** A name from the catalog; ids it doesn't list (retired ones in saved conversations) read as words. */
const agentLabel = (labels: Record<string, string>, id: string) =>
  labels[id] ?? id.split(/[-_]/).map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");

/** Who can answer, and what none of them can. Reading it runs nothing; asking never depends on it. */
function Specialists({ catalog }: { catalog: Catalog }) {
  if (catalog.error) {
    return (
      <p className="muted small" role="status">
        The list of specialists could not be loaded. You can still ask a question.{" "}
        <Button variant="link" onClick={catalog.reload}>Try again</Button>
      </p>
    );
  }
  if (catalog.loading && !catalog.data) return <p className="muted small" role="status">Loading specialists…</p>;
  const specialists = catalog.data?.agents?.filter((a) => a.personas.length > 0) ?? [];
  if (specialists.length === 0) return null;
  const labels = Object.fromEntries(catalog.data!.agents.map((a) => [a.id, a.label]));
  return (
    <details className="card recent ask-specialists">
      <summary>Who answers: {specialists.length} {specialists.length === 1 ? "specialist" : "specialists"}, and what they can't do</summary>
      <ul className="specialist-list">
        {specialists.map((a) => (
          <li key={a.id}>
            <b>{a.label}</b>
            <div>{a.description}</div>
            <div className="muted small">For: {a.personas.join("; ")}</div>
            <div className="muted small">People decide: {a.human_decisions.join(" ")}</div>
          </li>
        ))}
      </ul>
      <h3>Not available yet</h3>
      <p className="muted small">These need a source Fuzer doesn't have. Asking gets an explanation, not an estimate.</p>
      <ul className="specialist-gaps">
        {catalog.data!.unavailable.map((u) => (
          <li key={u.capability}>{u.capability} <span className="muted small">({agentLabel(labels, u.agent)}: {u.needed})</span></li>
        ))}
      </ul>
    </details>
  );
}

function TurnView({ turn, labels, demo = false }: { turn: Turn; labels: Record<string, string>; demo?: boolean }) {
  const j = turn.job;
  const steps = j?.steps ?? [];
  return (
    <div className="turn">
      <div className="question">
        {turn.question}
        {turn.images && turn.images.length > 0 && (
          <div className="question-images">{turn.images.map((im) => <img key={im.url} src={im.url} alt={im.name} />)}</div>
        )}
        {turn.imageUrls && turn.imageUrls.length > 0 && (
          <div className="question-images">{turn.imageUrls.map((u) => <img key={u} src={u} alt="attached" />)}</div>
        )}
      </div>
      <UICard className="answer">
        {turn.error && (demo ? <ActionStatus error={turn.error} /> : <div className="error small">{turn.error}</div>)}
        {!turn.error && (!j || j.status === "running") && (
          <div className="working">
            <div className="small"><UIBadge className="badge updating">Working…</UIBadge>{" "}
              {j ? `${j.model_calls} model calls so far` : "starting"}</div>
            {steps.length > 0 && (
              <ul className="steps small muted">
                {steps.slice(-6).map((s, i) => (
                  <li key={i}><b>{agentLabel(labels, s.agent)}</b> · {s.tool.replaceAll("_", " ")}
                    {Object.keys(s.args).length > 0 && <span className="mono"> {JSON.stringify(s.args)}</span>}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        {j?.status === "error" && (demo ? <ActionStatus error={j.error} /> : <div className="error small">{j.error}</div>)}
        {j?.status === "done" && j.answer && (
          <>
            <Markdown text={j.answer} />
            <div className="row small muted answer-meta">
              {j.specialists && j.specialists.length > 0 && <span>Specialists: {j.specialists.map((id) => agentLabel(labels, id)).join(", ")}</span>}
              <span>{steps.length} tool calls · {j.seconds} s · ${j.usage?.cost_usd.toFixed(2)}</span>
              {j.usage && (
                <span title="Calls and cost per model, as reported by the API">
                  {Object.entries(j.usage.models).map(([m, x]) =>
                    `${m.replace("claude-", "")}: ${x.calls} calls $${x.cost_usd.toFixed(2)}${x.unpriced ? " (unpriced)" : ""}`).join(" · ")}
                </span>
              )}
              {j.recalled && j.recalled.length > 0 && (
                <span title="Facts learned in earlier conversations that came with this question">
                  {j.recalled.length} remembered fact{j.recalled.length === 1 ? "" : "s"}
                </span>
              )}
              <span className="spacer" />
              {!demo && <Rate id={j.id} />}
            </div>
            {!demo && <Remember id={j.id} />}
          </>
        )}
      </UICard>
    </div>
  );
}

/** "Remember this": an engineer gives a statement from the answer to keep as dated facts. */
function Remember({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [state, setState] = useState<string>();
  if (!open) {
    return (
      <div className="small">
        <Button variant="link" onClick={() => {
          setText(window.getSelection()?.toString().trim() ?? "");
          setOpen(true);
        }} title="Select text to extract into shared facility memory (uses the configured AI provider)">Share in facility memory…</Button>
        {state && <span className="muted"> {state}</span>}
      </div>
    );
  }
  const send = () => {
    setState("Remembering…");
    api.remember(id, text, asker()).then(
      (r) => { setState(r.items.length ? `Remembered: ${r.items.map((f) => f.text).join(" ")}` : "Nothing to remember in that."); setOpen(false); setText(""); },
      (e) => setState(String(e)));
  };
  return (
    <div className="row small remember">
      <Input placeholder="Statement to share in facility memory (uses AI)" value={text}
             onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && text.trim() && send()} />
      <Button variant="primary" disabled={!text.trim()} onClick={send}>Remember</Button>
      <Button variant="link" onClick={() => setOpen(false)}>Cancel</Button>
      {state && <span className="muted">{state}</span>}
    </div>
  );
}

function Rate({ id }: { id: string }) {
  const [sent, setSent] = useState<boolean | null>(null);
  const [comment, setComment] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (sent !== null) return <span>Thanks: rated {sent ? "useful" : "not useful"}.</span>;
  const send = async (useful: boolean) => {
    if (busy) return;
    setBusy(true); setError("");
    try { await api.feedback(`answer:${id}`, useful, asker(), comment || undefined); setSent(useful); }
    catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  };
  return (
    <span className="rate-inline">
      Useful? <Button disabled={busy} title="Learns facts from this answer into shared facility memory using AI"
        onClick={() => send(true)}>Yes &amp; share learning</Button>{" "}
      <Button disabled={busy} onClick={() => (open ? send(false) : setOpen(true))}>No</Button>
      {open && <Input placeholder="What was wrong or missing?" value={comment} onChange={(e) => setComment(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && send(false)} />}
      {error && <span role="alert">{error}</span>}
    </span>
  );
}

/** A saved question and answer as a finished turn. */
function saved(conversationId: string, t: SavedTurn): Turn {
  return {
    question: t.question.replace(/ \[\d+ image\(s\) attached\]$/, ""),
    imageUrls: t.images.map((sha) => `/assistant/conversations/${encodeURIComponent(conversationId)}/images/${sha}`),
    jobId: t.id,
    job: {
      id: t.id, conversation_id: conversationId, question: t.question, status: t.error ? "error" : "done",
      answer: t.answer, error: t.error, seconds: t.seconds ?? undefined, specialists: t.specialists,
      usage: t.usage ?? undefined, steps: t.tools.map((x) => ({ agent: x.agent, tool: x.tool, args: x.args ?? {} })),
      model_calls: t.usage?.model_calls ?? 0, recalled: t.recalled,
    },
  };
}

function Conversations({ current, reload, busy, onOpen, onDeleted, readOnly = false }: {
  current?: string; reload: number; busy: boolean; onOpen: (id: string) => void; onDeleted: (id: string) => void;
  readOnly?: boolean;
}) {
  const [gone, setGone] = useState<string[]>([]);
  const r = useAsync(() => api.conversations(asker()), [reload]);
  const items = (r.data ?? []).filter((c) => c.turns > 0 && !gone.includes(c.id));
  if (items.length === 0) return null;
  return (
    <details className="card recent" open>
      <summary>Your conversations <span className="muted small">{items.length}</span></summary>
      <ul className="plain small">
        {items.map((c) => (
          <li key={c.id}>
            <span className="muted mono">{c.updated_at.slice(0, 16).replace("T", " ")}</span>{" "}
            {c.id === current ? <b>{c.title}</b> : (
              <Button variant="link" disabled={busy} onClick={() => onOpen(c.id)}>{c.title}</Button>
            )}{" "}
            <span className="muted">· {c.turns} question{c.turns === 1 ? "" : "s"}</span>{" "}
            {!readOnly && <Button variant="link" className="muted" disabled={busy} title="Delete this conversation and its answers"
                    onClick={() => api.deleteConversation(c.id).then(() => { setGone((g) => [...g, c.id]); onDeleted(c.id); })}>
              delete
            </Button>}
          </li>
        ))}
      </ul>
    </details>
  );
}
