/** Where an entity sits and what sits in it: the chain above it as a breadcrumb, and the first two levels beneath it
 * with the open work and down count rolled up through each, so a facility opens on its areas and an area on its
 * wells and machines. A row opens that entity, one level further down; its own children show in place. */
import { useState } from "react";
import { api, type Hierarchy, type TreeNode } from "./api";
import { count } from "./format";
import { Badge, Button, Card, Text } from "./ui";
import { KindBadge, entityHref, useAsync } from "./lib";
import { kindWord, plural } from "./wording";

/** The tree an entity page reads once and shares between its breadcrumb, Beneath and its tabs. */
export const useHierarchy = (id: string) => useAsync(() => api.hierarchy(id)
  .then((t) => (Array.isArray(t?.up) && Array.isArray(t?.children) ? t : null), () => null), [id]);

/** "31 areas", "229 equipment": a kind's name for a count of them. */
export function kindCount(n: number, kind: string): string {
  const word = kindWord(kind).toLowerCase();
  return /equipment$|s$/.test(word) ? `${count(n)} ${word}` : plural(n, word);
}

export function Breadcrumb({ tree }: { tree: Hierarchy | null | undefined }) {
  if (!tree?.up.length) return null;
  return <nav className="entity-trail" aria-label="Where this sits">
    <ol>{[...tree.up].reverse().map((u) => <li key={u.id}>
      <a href={entityHref(u.id)} title={kindWord(u.kind)}>{u.label}</a>
    </li>)}</ol>
  </nav>;
}

const TONE = { high: "danger", medium: "warn", low: "info" } as const;

function Row({ node, nested }: { node: TreeNode; nested?: boolean }) {
  const [open, setOpen] = useState(false);
  const kids = node.children.length > 0;
  return <li className={nested ? "beneath-row nested" : "beneath-row"}>
    <div className="beneath-line">
      <a href={entityHref(node.id)} className="beneath-name">
        <Text mono>{node.label}</Text>
        {node.description && node.description !== node.label && <Text small muted>{node.description}</Text>}
      </a>
      <span className="beneath-facts">
        <KindBadge kind={node.kind} />
        {node.open_work > 0 && <Badge tone={node.work_band ? TONE[node.work_band] : "muted"}>{count(node.open_work)} open</Badge>}
        {node.down > 0 && <Badge tone="danger">{count(node.down)} down</Badge>}
        {!nested && kids && <Button variant="link" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Hide" : `${count(node.beneath)} beneath`}
        </Button>}
        {nested && node.beneath > 0 && <Text small muted>{count(node.beneath)} beneath</Text>}
      </span>
    </div>
    {open && <ul className="beneath-list">
      {node.children.map((c) => <Row key={c.id} node={c} nested />)}
      {node.more > 0 && <li className="beneath-row nested"><a href={entityHref(node.id)}>{count(node.more)} more</a></li>}
    </ul>}
  </li>;
}

export function Beneath({ tree }: { tree: Hierarchy | null | undefined }) {
  if (!tree?.children.length) return null;
  const kinds = Object.entries(tree.kinds).map(([k, n]) => kindCount(n, k)).join(" · ");
  return <Card as="section" className="beneath" title="Beneath" detail={kinds}>
    <ul className="beneath-list">
      {tree.children.map((c) => <Row key={c.id} node={c} />)}
    </ul>
    {tree.more > 0 && <Text small muted>{count(tree.more)} more not listed</Text>}
  </Card>;
}
