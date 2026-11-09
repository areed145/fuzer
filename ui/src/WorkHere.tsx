/** Work here: on an asset's page, the open exceptions about it (and who has each) and what is planned on it, so
 * whoever looks up an asset sees the work around it before adding to it. */
import { api } from "./api";
import { Badge, Card } from "./ui";
import { href, useAsync } from "./lib";
import { holder, when } from "./work";

const day = (at: string) => new Date(at).toLocaleDateString(undefined, { day: "numeric", month: "short" });

export function WorkHere({ id, area }: { id: string; area: boolean }) {
  const open = useAsync(() => api.exceptions({ state: "open", subject: id }), [id]);
  const planned = useAsync(() => api.plan(area ? { area: id } : { subject: id }), [id, area]);
  const exceptions = open.data ?? [];
  const items = (planned.data ?? []).filter((i) => i.status !== "done" && i.status !== "cancelled");
  if (!exceptions.length && !items.length) return null;
  return (
    <Card as="section" title="Work here" className="work-here">
      {exceptions.length > 0 && <>
        <h3 className="small">Open exceptions</h3>
        <ul className="plain small">{exceptions.map((x) => <li key={x.id}>
          <a href={href("/exceptions")}>{x.title}</a> <span className="muted">{x.type.replace(/_/g, " ")} · last {when(x.last_seen)}</span>{" "}
          {x.item_id ? <a href={href(`/cases/${x.item_id}`)}>with {holder(x.assignee, x.role)}</a>
            : <Badge tone="warn">not dispatched</Badge>}
        </li>)}</ul>
      </>}
      {items.length > 0 && <>
        <h3 className="small">Planned</h3>
        <ul className="plain small">{items.map((i) => <li key={i.id}>
          <a href={href("/plan")}>{i.title}</a> <span className="muted">{day(i.start_at)}–{day(i.end_at)} · {i.kind.replace(/_/g, " ")}
            {i.crew ? ` · ${i.crew}` : ""}</span>{" "}
          {i.clashes.some((c) => c.kind !== "bundle") && <Badge tone="danger">clash</Badge>}
          {i.needs_shutdown && <Badge tone="warn">shutdown</Badge>}
        </li>)}</ul>
      </>}
    </Card>
  );
}
