/** Pick the entity a value meant (#476): search the register, tap the one it meant, and choose whether to remember it
 * (an alias, so the next record or exception with that value matches on its own). Where only some kinds will do (what
 * work can be about, P5.26f), the search offers only those, each with its kind, description and the place it sits in.
 * The rows are tapped, not picked from a dropdown, a single result comes picked, and the count says how many more the
 * search found than it shows (#1382). With `multiple`, several can be picked (a message about three drivers). */
import { useState } from "react";
import { errorMessage } from "../../lib";
import { api, type SearchHit } from "../../api";
import { count } from "../../format";
import { display } from "../../entityRefs";
import { kindWord } from "../../wording";
import { Button, Input, Empty, ErrorText, Line, List, Checkbox, Text } from "../../ui";

/** One register entity as a line a person can tell apart from the next: "30-PT-2001 · Tag · Test separator pressure ·
 * ST1". It is named as everywhere else (`display`): equipment by the tag it is installed at, never by its serial number
 * or its id (#1383). */
export const hitLine = (h: Pick<SearchHit, "fuzer_id" | "kind" | "label" | "description" | "parent_label" | "at">) => {
  const name = display(h.fuzer_id, { [h.fuzer_id]: { label: h.label, description: h.description, at: h.at ?? undefined } });
  const said = h.description && !name.includes(h.description) ? h.description : null;
  return [name, kindWord(h.kind), said, h.parent_label].filter(Boolean).join(" · ");
};

const SHOWN = 12;

export function MatchRef({ value, onPick, busy, remember: rememberDefault = true, label = "Match", canRemember = true,
  kinds, multiple = false }: {
  value: string; busy?: boolean; remember?: boolean; label?: string;
  /** false where a pick is kept some other way (a message's case, P2.8): no alias is offered. */
  canRemember?: boolean;
  /** Only these kinds are offered (the server holds the same list and refuses any other). */
  kinds?: string[];
  /** Several can be picked; `onPick` gets the first, and all of them third. */
  multiple?: boolean;
  onPick: (fuzerId: string, remember: boolean, all: string[]) => void;
}) {
  const bare = value.includes(":") ? value.slice(value.indexOf(":") + 1) : value;
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState(bare);
  const [found, setFound] = useState<{ hits: SearchHit[]; total: number } | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [remember, setRemember] = useState(rememberDefault && canRemember);
  const [error, setError] = useState<string | null>(null);
  const search = async () => {
    setError(null);
    try {
      const got = await api.searchCounted(q, kinds?.join(","), SHOWN);
      setFound(got);
      setPicked(got.hits.length === 1 ? [got.hits[0].fuzer_id] : []);  // one result: nothing left to choose
    } catch (e) { setError(errorMessage(e)); }
  };
  const tap = (id: string, on: boolean) =>
    setPicked(on ? (multiple ? [...picked, id] : [id]) : picked.filter((x) => x !== id));
  if (!open) return <Button size="sm" variant="link" onClick={() => { setOpen(true); if (q.trim()) void search(); }}
    title={`Pick the entity ${value} meant`}>{label}…</Button>;
  const hits = found?.hits ?? [];
  return (
    <div className="match-ref">
      <Line wrap>
        <Input aria-label="Search the register" width="md" value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void search(); }} />
        <Button onClick={() => void search()}>Search</Button>
        <Button variant="link" onClick={() => setOpen(false)}>Cancel</Button>
      </Line>
      {found && hits.length === 0 && <Empty>Nothing in the register matches “{q}”. Try part of a tag or name.</Empty>}
      {hits.length > 0 && <>
        {found!.total > hits.length && <Text as="div" small>{hits.length} of {count(found!.total)}</Text>}
        <List plain aria-label="Entities">{hits.map((h) => <li key={h.fuzer_id}>
          <Checkbox checked={picked.includes(h.fuzer_id)} disabled={busy} onChange={(on) => tap(h.fuzer_id, on)}>
            {hitLine(h)}</Checkbox></li>)}</List>
      </>}
      <Line wrap>
        {canRemember && <Checkbox checked={remember} onChange={setRemember}>remember “{bare}” as a name for it</Checkbox>}
        <Button variant="primary" disabled={!picked.length || busy} onClick={() => onPick(picked[0], remember, picked)}>
          {picked.length > 1 ? `Use these ${picked.length}` : "Use it"}</Button>
      </Line>
      <ErrorText>{error}</ErrorText>
    </div>
  );
}
