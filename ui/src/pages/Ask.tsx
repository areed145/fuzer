import { useEffect, useRef, useState } from "react";
import { api, type AssistantJob } from "../api";
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

interface Turn {
  question: string;
  job?: AssistantJob;
  jobId?: string;
  error?: string;
}

export function AskPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversation, setConversation] = useState<string>();
  const [text, setText] = useState("");
  const busy = turns.some((t) => t.jobId && (!t.job || t.job.status === "running") && !t.error);
  const end = useRef<HTMLDivElement>(null);

  const ask = async (question: string) => {
    if (!question.trim() || busy) return;
    setText("");
    const n = turns.length;
    setTurns((ts) => [...ts, { question }]);
    try {
      const started = await api.ask(question, conversation, asker());
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

  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [turns.length]);

  return (
    <section className="ask">
      <h1>Ask</h1>
      <p className="muted">
        An orchestrator and three specialists (troubleshooting, production, maintenance) answer from Fuzer's linked
        data: SAP, PI, Energy Components, P&IDs, logs and documents. Every fact links to where it came from. Read
        only: it never acts on the plant.
      </p>
      {turns.length === 0 && (
        <div className="examples">
          {EXAMPLES.map(([label, q]) => (
            <button key={label} className="example" onClick={() => ask(q)}>
              <span className="muted small">{label}</span>
              <span>{q}</span>
            </button>
          ))}
        </div>
      )}
      {turns.map((t, i) => <TurnView key={i} turn={t} />)}
      <div ref={end} />
      <form className="ask-box card" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
        <textarea rows={3} value={text} placeholder={turns.length ? "Ask a follow-up…" : "Ask about the plant…"}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      ask(text);
                    }
                  }} />
        <div className="row">
          <button className="primary" type="submit" disabled={busy || !text.trim()}>{busy ? "Working…" : "Ask"}</button>
          {turns.length > 0 && (
            <button type="button" className="link" disabled={busy}
                    onClick={() => { setTurns([]); setConversation(undefined); }}>New conversation</button>
          )}
          <span className="spacer" />
          <span className="muted small">Enter to send · Shift+Enter for a new line</span>
        </div>
      </form>
      <RecentAnswers />
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
      <div className="question">{turn.question}</div>
      <div className="card answer">
        {turn.error && <div className="error small">{turn.error}</div>}
        {!turn.error && (!j || j.status === "running") && (
          <div className="working">
            <div className="small"><span className="badge updating">Working…</span>{" "}
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
              <span>{steps.length} tool calls · {j.usage?.model_calls} model calls · {j.seconds} s · ${j.usage?.cost_usd.toFixed(2)}</span>
              <span className="spacer" />
              <Rate id={j.id} />
            </div>
          </>
        )}
      </div>
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
      Useful? <button onClick={() => send(true)}>Yes</button>{" "}
      <button onClick={() => (open ? send(false) : setOpen(true))}>No</button>
      {open && <input placeholder="What was wrong or missing?" value={comment} onChange={(e) => setComment(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && send(false)} />}
    </span>
  );
}

function RecentAnswers() {
  const r = useAsync(() => api.assistantAnswers(), []);
  const items = (r.data ?? []).filter((a) => a.answer).slice(0, 8);
  if (items.length === 0) return null;
  return (
    <details className="card recent">
      <summary>Recent questions <span className="muted small">{items.length}</span></summary>
      <ul className="plain small">
        {items.map((a) => (
          <li key={a.id}>
            <span className="muted mono">{a.created_at.slice(0, 16).replace("T", " ")}</span> {a.question}{" "}
            <span className="muted">· {a.by_user} · ${Number(a.usage?.cost_usd ?? 0).toFixed(2)}
              {a.ratings > 0 && ` · ${a.useful}/${a.ratings} useful`}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
