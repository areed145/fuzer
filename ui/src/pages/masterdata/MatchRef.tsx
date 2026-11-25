/** Pick the asset a value meant (#476): search the register, pick one, and choose whether to remember it (an alias,
 * so the next record or exception with that value matches on its own). Where only some kinds will do (what work can be
 * about, P5.26f), the search offers only those, each with its kind, description and the place it sits in. */
import { useState } from "react";
import { errorMessage } from "../../lib";
import { api, type SearchHit } from "../../api";
import { kindWord } from "../../wording";
import { Button, Input, Empty, ErrorText, Field, Line, Select, Checkbox } from "../../ui";

/** One register entity as a line a person can tell apart from the next: "30-PT-2001 · Tag · Test separator pressure ·
 * ST1". */
export const hitLine = (h: Pick<SearchHit, "fuzer_id" | "kind" | "label" | "description" | "parent_label">) =>
  [h.label ?? h.fuzer_id, kindWord(h.kind), h.description, h.parent_label].filter(Boolean).join(" · ");

export function MatchRef({ value, onPick, busy, remember: rememberDefault = true, label = "Match", canRemember = true, kinds }: {
  value: string; busy?: boolean; remember?: boolean; label?: string;
  /** false where a pick is kept some other way (a message's case, P2.8): no alias is offered. */
  canRemember?: boolean;
  /** Only these kinds are offered (the server holds the same list and refuses any other). */
  kinds?: string[];
  onPick: (fuzerId: string, remember: boolean) => void;
}) {
  const bare = value.includes(":") ? value.slice(value.indexOf(":") + 1) : value;
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState(bare);
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [picked, setPicked] = useState<string>("");
  const [remember, setRemember] = useState(rememberDefault && canRemember);
  const [error, setError] = useState<string | null>(null);
  const search = async () => {
    setError(null);
    setPicked("");
    try { setHits((await api.search(q, kinds?.join(","), 12)).slice(0, 12)); } catch (e) { setError(errorMessage(e)); }
  };
  if (!open) return <Button size="sm" variant="link" onClick={() => { setOpen(true); if (q.trim()) void search(); }}
    title={`Pick the asset ${value} meant`}>{label}…</Button>;
  return (
    <div className="match-ref">
      <Line wrap>
        <Input aria-label="Search the register" width="md" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void search(); }} />
        <Button onClick={() => void search()}>Search</Button>
        <Button variant="link" onClick={() => setOpen(false)}>Cancel</Button>
      </Line>
      {hits && hits.length === 0 && <Empty>Nothing in the register matches “{q}”. Try part of a tag or name.</Empty>}
      {hits && hits.length > 0 && <Field label="Entity" width="full">
        <Select width="full" aria-label="Entity" value={picked} onChange={(e) => setPicked(e.target.value)}>
          <option value="">Choose one of {hits.length}</option>
          {hits.map((h) => <option key={h.fuzer_id} value={h.fuzer_id}>{hitLine(h)}</option>)}
        </Select>
      </Field>}
      <Line wrap>
        {canRemember && <Checkbox checked={remember} onChange={setRemember}>remember “{bare}” as a name for it</Checkbox>}
        <Button variant="primary" disabled={!picked || busy} onClick={() => onPick(picked, remember)}>Use it</Button>
      </Line>
      <ErrorText>{error}</ErrorText>
    </div>
  );
}
