import "../styles/ask.css";
import { Button, ButtonLabel, PageHeader, Card as UICard, Input , Badge as UIBadge} from "../ui";
import { useEffect, useRef, useState } from "react";
import { api, type AssistantJob, type Attachment, type SavedTurn } from "../api";
import { Markdown } from "../Markdown";
import { useAsync } from "../lib";

const EXAMPLES: [string, string][] = [
  ["Troubleshooting", "Why did 20-K-2200B keep tripping in March 2024, and what should we check if it happens again?"],
  ["Production", "How has production efficiency trended over the last 12 months, and what drove the worst month?"],
  ["Constraints", "What is limiting production right now, and how does it cascade through the field?"],
  ["Wells", "Which wells have lost the most oil rate this year, and why?"],
  ["Reliability", "Who are our bad actors, and what are their repeat failure modes?"],
  ["Work orders", "What in the work order backlog should we tackle first?"],
  ["Turnaround", "What is in scope for the next turnaround, and what is missing from it?"],
  ["Across domains", "Why does lift gas keep running short, and what should go in the next turnaround to fix it?"],
];

// Who is asking: the name the Review page remembers.
const asker = () => {
  try {
    return localStorage.getItem("fuzer.reviewer") || "anonymous";
  } catch {
    return "anonymous";
  }
};

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

export function AskPage() {
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
  const [text, setText] = useState("");
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
    if (!question.trim() || busy) return;
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

  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [turns.length]);

  return (
    <section className="ask">
      <PageHeader>Ask</PageHeader>
      <p className="muted">
        An orchestrator and three specialists (troubleshooting, production, maintenance) answer from Fuzer's linked
        data: SAP, PI, Energy Components, P&IDs, logs and documents. Every fact links to where it came from. Read
        only: it never acts on the plant.
      </p>
      {turns.length === 0 && (
        <div className="ask-examples">
          {EXAMPLES.map(([label, q]) => (
            <Button key={label} className="example" onClick={() => ask(q)}>
              <span className="muted small">{label}</span>
              <span>{q}</span>
            </Button>
          ))}
        </div>
      )}
      {openError && <div className="error small">{openError}</div>}
      {turns.map((t, i) => <TurnView key={i} turn={t} />)}
      <div ref={end} />
      <form className="ask-box card" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
        <textarea rows={3} value={text} placeholder={turns.length ? "Ask a follow-up…" : "Ask about the plant…"}
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
                      ask(text);
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
          <Button variant="primary" type="submit" disabled={busy || !text.trim()}>{busy ? "Working…" : "Ask"}</Button>
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
                     onDeleted={(id) => { if (id === conversation) { setTurns([]); setConversation(undefined); } }} />
    </section>
  );
}

const AGENT_LABEL: Record<string, string> = {
  orchestrator: "Orchestrator", troubleshooter: "Troubleshooter", production: "Production", maintenance: "Maintenance",
};

function TurnView({ turn }: { turn: Turn }) {
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
        {turn.error && <div className="error small">{turn.error}</div>}
        {!turn.error && (!j || j.status === "running") && (
          <div className="working">
            <div className="small"><UIBadge className="badge updating">Working…</UIBadge>{" "}
              {j ? `${j.model_calls} model calls so far` : "starting"}</div>
            {steps.length > 0 && (
              <ul className="steps small muted">
                {steps.slice(-6).map((s, i) => (
                  <li key={i}><b>{AGENT_LABEL[s.agent] ?? s.agent}</b> · {s.tool.replaceAll("_", " ")}
                    {Object.keys(s.args).length > 0 && <span className="mono"> {JSON.stringify(s.args)}</span>}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        {j?.status === "error" && <div className="error small">{j.error}</div>}
        {j?.status === "done" && j.answer && (
          <>
            <Markdown text={j.answer} />
            <div className="row small muted answer-meta">
              {j.specialists && j.specialists.length > 0 && <span>Specialists: {j.specialists.map((s) => AGENT_LABEL[s] ?? s).join(", ")}</span>}
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
              <Rate id={j.id} />
            </div>
            <Remember id={j.id} />
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
        }} title="Select text in the answer first, or type what to remember">Remember this…</Button>
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
      <Input placeholder="What should the assistant remember? (a statement from the answer)" value={text}
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
  if (sent !== null) return <span>Thanks: rated {sent ? "useful" : "not useful"}.</span>;
  const send = (useful: boolean) => api.feedback(`answer:${id}`, useful, asker(), comment || undefined).then(() => setSent(useful));
  return (
    <span className="rate-inline">
      Useful? <Button onClick={() => send(true)}>Yes</Button>{" "}
      <Button onClick={() => (open ? send(false) : setOpen(true))}>No</Button>
      {open && <Input placeholder="What was wrong or missing?" value={comment} onChange={(e) => setComment(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && send(false)} />}
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

function Conversations({ current, reload, busy, onOpen, onDeleted }: {
  current?: string; reload: number; busy: boolean; onOpen: (id: string) => void; onDeleted: (id: string) => void;
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
            <Button variant="link" className="muted" disabled={busy} title="Delete this conversation and its answers"
                    onClick={() => api.deleteConversation(c.id).then(() => { setGone((g) => [...g, c.id]); onDeleted(c.id); })}>
              delete
            </Button>
          </li>
        ))}
      </ul>
    </details>
  );
}
