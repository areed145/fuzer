/** Work here: on an asset's page, the open exceptions about it (and who has each) and what is planned on it, so
 * whoever looks up an asset sees the work around it before adding to it. */
import { api } from "./api";
import { when, whenDay } from "./format";
import { Badge, Card } from "./ui";
import { href, planItemHref, useAsync } from "./lib";
import { holder } from "./work";

const day = (at: string) => whenDay(at);

/** What the card reads, shared with the asset page so it can draw its overview at once (#1069). */
export const readWork = (id: string, area: boolean) =>
  Promise.all([api.exceptions({ state: "open", subject: id }).catch(() => []), api.plan(area ? { area: id } : { subject: id }).catch(() => [])]);

export function WorkHere({ id, area, data }: { id: string; area: boolean; data?: Awaited<ReturnType<typeof readWork>> }) {
  const own = useAsync(() => (data === undefined ? readWork(id, area) : Promise.resolve(undefined)), [id, area, data === undefined]).data;
  const [open, planned] = data ?? own ?? [[], []];
  const exceptions = open ?? [];
  const items = (planned ?? []).filter((i) => i.status !== "done" && i.status !== "cancelled");
  if (!exceptions.length && !items.length) return null;
  return (
    <Card as="section" title="Work here" className="work-here">
      {exceptions.length > 0 && <>
        <h3 className="small">Open exceptions</h3>
        <ul className="plain small">{exceptions.map((x) => <li key={x.id}>
          <a href={href("/exceptions")}>{x.title}</a> <span className="muted">{x.type.replace(/_/g, " ")} · last {when(x.last_seen)}</span>{" "}
          {x.item_id ? <a href={href(`/cases/${x.item_id}`)}>with {holder(x.assignee, x.role)}</a>
            : x.planned ? <a className="muted" href={planItemHref(x.planned)}>planned {whenDay(x.planned.start_at)}</a> : <Badge tone="warn">not dispatched</Badge>}
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
