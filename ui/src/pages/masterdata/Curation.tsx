/** Correcting entity resolution from the entity page (#476): "Same as…" merges two entities, "Not this record"
 * splits a record off. Both show what will happen first, apply on a rebuild queued at once, and can be undone from
 * the decision log on the Master data page. */
import { useState } from "react";
import { api, type MergePreview, type SearchHit, type SplitPreview } from "../../api";
import { Button, Input, Empty } from "../../ui";
import { entityHref, fmt, href } from "../../lib";
import { usePerson } from "../../work";
import { usePermission } from "../../Auth";
import { useAct } from "../workflows/shared";

const AFTER = "Applied on a rebuild that starts now; undo it from Master data › Decisions.";

function Done({ batch }: { batch: string }) {
  return <p className="small" role="status">Done ({batch}). {AFTER} <a href={href("/masterdata", { tab: "decisions" })}>Decision log</a></p>;
}

export function NotThisRecord({ fuzerId, refId, onDone }: { fuzerId: string; refId: string; onDone?: () => void }) {
  const [person] = usePerson();
  const operate = usePermission("operate");
  const [p, setP] = useState<SplitPreview | null>(null);
  const [reason, setReason] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const a = useAct();
  if (!operate || !person) return null;
  if (done) return <Done batch={done} />;
  if (!p) return <Button size="sm" variant="link" disabled={!!a.busy} title="This record is a different thing"
    onClick={() => void a.run("preview", () => api.splitPreview(fuzerId, refId), setP)}>Not this record…</Button>;
  return (
    <div className="curation small">
      <p>Split <span className="mono">{p.ref}</span> off. {p.stays.length} record{p.stays.length === 1 ? "" : "s"} stay.
        {p.brings.length > 0 && <> It takes its values with it: {p.brings.map((b) => `${b.field} ${fmt(b.value)}`).join("; ")}.</>}</p>
      <p className="small">{p.note}</p>
      <div className="row">
        <Input placeholder="why (kept on the decision)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <Button size="sm" variant="primary" disabled={!!a.busy}
          onClick={() => void a.run("split", () => api.splitRecord(fuzerId, refId, person, reason || undefined),
            (r) => { setDone(r.batch); onDone?.(); })}>Split it off</Button>
        <Button size="sm" variant="link" onClick={() => setP(null)}>Cancel</Button>
      </div>
      {a.error && <p role="alert" className="error">{a.error}</p>}
    </div>
  );
}

export function SameAs({ fuzerId, kind, onDone }: { fuzerId: string; kind: string; onDone?: () => void }) {
  const [person] = usePerson();
  const operate = usePermission("operate");
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [p, setP] = useState<MergePreview | null>(null);
  const [reason, setReason] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const a = useAct();
  if (!operate || !person) return null;
  if (done) return <Done batch={done} />;
  if (!open) return <Button size="sm" variant="link" onClick={() => setOpen(true)}
    title="Another entity in the register is this same thing">Same as…</Button>;
  const search = () => void a.run("search", () => api.search(q),
    (r) => setHits(r.filter((h) => h.fuzer_id !== fuzerId && h.kind === kind).slice(0, 10)));
  return (
    <div className="curation small">
      {!p && <>
        <div className="row">
          <Input aria-label="Find the other entity" placeholder={`find the other ${kind}`} value={q}
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") search(); }} />
          <Button size="sm" onClick={search}>Search</Button>
          <Button size="sm" variant="link" onClick={() => { setOpen(false); setHits(null); }}>Cancel</Button>
        </div>
        {hits && hits.length === 0 && <Empty>No other {kind} matches “{q}”.</Empty>}
        {hits && hits.length > 0 && <ul className="plain">{hits.map((h) => (
          <li key={h.fuzer_id}><Button size="sm" variant="link" disabled={!!a.busy}
            onClick={() => void a.run("preview", () => api.mergePreview(fuzerId, h.fuzer_id), setP)}>
            {h.label ?? h.fuzer_id}</Button> <span className="muted">{h.description ?? ""}</span></li>
        ))}</ul>}
      </>}
      {p && <>
        <p>Make <a href={entityHref(p.b.fuzer_id)}>{p.b.label ?? p.b.fuzer_id}</a> and this one a single {kind}:
          {" "}{p.records} records, keys {p.keys.map((k) => k.key).join(", ")}.</p>
        {p.differ.length > 0 && <p>Where they disagree: {p.differ.map((d) => `${d.field} ${fmt(d.a)} vs ${fmt(d.b)}`).join("; ")}.</p>}
        {p.replaces.length > 0 && <p>This reverses an earlier decision that they differ: {p.replaces.join("; ")}.</p>}
        <p className="small">{p.note}</p>
        <div className="row">
          <Input placeholder="why (kept on the decision)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <Button size="sm" variant="primary" disabled={!!a.busy}
            onClick={() => void a.run("merge", () => api.mergeEntities(fuzerId, p.b.fuzer_id, person, reason || undefined),
              (r) => { setDone(r.batch); onDone?.(); })}>Merge them</Button>
          <Button size="sm" variant="link" onClick={() => setP(null)}>Back</Button>
        </div>
      </>}
      {a.error && <p role="alert" className="error">{a.error}</p>}
    </div>
  );
}
