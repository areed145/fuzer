/** What agents did on a case (P4.4): each agent step with the agents that worked on it, the skill and model
 * versions, the tools called and the cost, read from the step's ledger in the case history, and the case's agent
 * spend against its cap. One card; the detail of a step sits behind a Disclosure. */
import type { WorkItem } from "./api";
import { Actor, when } from "./work";
import { Badge, Card, Disclosure, Table } from "./ui";

interface Ledger {
  agent?: string;
  agents?: string[];
  skills?: Record<string, string>;
  models?: string[];
  tools?: { agent?: string; tool: string; args?: Record<string, unknown> }[];
  seconds?: number | null;
  cost_usd?: number;
}

export interface AgentStep { id: number; at: string; actor: string; step: string; answer?: string; ledger: Ledger }

const usd = (x: number | undefined) => `$${(x ?? 0).toFixed(2)}`;

/** Every agent step on the case that kept a ledger, from its history. */
export function agentSteps(it: WorkItem): AgentStep[] {
  return it.events.filter((e) => e.kind === "agent_result").flatMap((e) =>
    Object.values((e.data?.data ?? {}) as Record<string, any>)
      .filter((v) => v && typeof v === "object" && v.ledger)
      .map((v) => ({ id: e.id, at: e.at, actor: e.actor, step: String(e.data?.step ?? e.element ?? "step"),
                     answer: typeof v.answer === "string" ? v.answer : undefined, ledger: v.ledger as Ledger })));
}

export function AgentWork({ it }: { it: WorkItem }) {
  const steps = agentSteps(it);
  const spend = it.agent_spend;
  if (!steps.length && !spend?.usd) return null;
  const over = !!spend?.cap_usd && spend.usd >= spend.cap_usd;
  return (
    <Card title="Agent work" actions={spend && <span className="small">
      {usd(spend.usd)} {spend.cap_usd ? <>of {usd(spend.cap_usd)} cap</> : "spent"}{" "}
      {over && <Badge tone="warn">cap reached</Badge>}</span>}>
      <Table density="compact">
        <thead><tr><th>Step</th><th>Agents</th><th>Model</th><th>Tools</th><th>Cost (USD)</th><th>When</th></tr></thead>
        <tbody>
          {steps.map((s, i) => (
            <tr key={`${s.id}-${i}`}>
              <td>{s.step}</td>
              <td><Actor who={s.actor} />{(s.ledger.agents ?? []).filter((a) => `agent:${a}` !== s.actor).map((a) =>
                <span key={a} className="small"> {a}</span>)}</td>
              <td className="small">{(s.ledger.models ?? []).join(", ") || "none recorded"}</td>
              <td>{s.ledger.tools?.length ?? 0}</td>
              <td>{usd(s.ledger.cost_usd)}</td>
              <td className="small">{when(s.at)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {steps.map((s, i) => (
        <Disclosure key={`d-${s.id}-${i}`} label={<>What {s.step} did</>}>
          {s.answer && <p className="small">{s.answer}</p>}
          <Table density="compact" variant="list">
            <thead><tr><th>Tool</th><th>Agent</th><th>Arguments</th></tr></thead>
            <tbody>{(s.ledger.tools ?? []).map((t, j) => (
              <tr key={j}><td className="mono">{t.tool}</td><td className="small">{t.agent}</td>
                <td className="small mono">{JSON.stringify(t.args ?? {})}</td></tr>))}</tbody>
          </Table>
          <p className="small">Skill versions:{" "}
            {Object.entries(s.ledger.skills ?? {}).map(([k, v]) => <span key={k} className="mono">{k} {v} </span>)}
          </p>
        </Disclosure>
      ))}
    </Card>
  );
}
