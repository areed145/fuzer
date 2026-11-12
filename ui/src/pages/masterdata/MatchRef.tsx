/** Pick the asset a value meant (#476): search the register, pick one, and choose whether to remember it (an alias,
 * so the next record or exception with that value matches on its own). */
import { useState } from "react";
import { api, type SearchHit } from "../../api";
import { Button, Input, Empty } from "../../ui";

export function MatchRef({ value, onPick, busy, remember: rememberDefault = true, label = "Match" }: {
  value: string; busy?: boolean; remember?: boolean; label?: string;
  onPick: (fuzerId: string, remember: boolean) => void;
}) {
  const bare = value.includes(":") ? value.slice(value.indexOf(":") + 1) : value;
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState(bare);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [remember, setRemember] = useState(rememberDefault);
  const [error, setError] = useState<string | null>(null);
  const search = async () => {
    setError(null);
    try { setHits((await api.search(q)).slice(0, 12)); } catch (e) { setError((e as Error).message); }
  };
  if (!open) return <Button size="sm" variant="link" onClick={() => { setOpen(true); void search(); }}
    title={`Pick the asset ${value} meant`}>{label}…</Button>;
  return (
    <div className="match-ref small">
      <div className="row">
        <Input aria-label="Search the register" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void search(); }} style={{ width: "12rem" }} />
        <Button size="sm" onClick={() => void search()}>Search</Button>
        <Button size="sm" variant="link" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
      {hits && hits.length === 0 && <Empty>Nothing in the register matches “{q}”. Try part of a tag or name.</Empty>}
      {hits && hits.length > 0 && (
        <ul className="plain">{hits.map((h) => (
          <li key={h.fuzer_id}><label>
            <input type="radio" name={`pick-${value}`} checked={picked === h.fuzer_id} onChange={() => setPicked(h.fuzer_id)} />{" "}
            {h.label ?? h.fuzer_id} <span className="muted">{h.kind}{h.description ? ` · ${h.description}` : ""}</span>
          </label></li>
        ))}</ul>
      )}
      <div className="row">
        <label><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />{" "}
          remember “{bare}” as a name for it</label>
        <Button size="sm" variant="primary" disabled={!picked || busy} onClick={() => onPick(picked, remember)}>Use it</Button>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
    </div>
  );
}
