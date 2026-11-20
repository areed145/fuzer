import "../styles/board.css";
import { promptDialog, ErrorText, Line, Text, Tabs } from "../ui";
import { money, whenDay, count } from "../format";
import { Button, Input, PageHeader, Table, Select , Badge as UIBadge} from "../ui";
import { useEffect, useState } from "react";
import { api, type BoardCard, type BoardFilter } from "../api";
import { Status, entityHref, href, notifyChanged, useAsync, useDebounced } from "../lib";
import { loadPerson } from "../work";

// Per-viewer conveniences, kept in this browser only.
const SAVED = "fuzer.board.filters";
const LAST_VISIT = "fuzer.board.lastVisit";
const store = {
  get<T>(key: string, fallback: T): T {
    try {
      const v = localStorage.getItem(key);
      return v ? (JSON.parse(v) as T) : fallback;
    } catch {
      return fallback;
    }
  },
  set(key: string, value: unknown) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* private window: nothing kept */
    }
  },
};

type View = "ranked" | "owner" | "area" | "kind" | "new";
const VIEWS: [View, string][] = [["ranked", "Ranked"], ["owner", "By owner"], ["area", "By area"], ["kind", "By type"],
  ["new", "New since my last visit"]];

const valued = (v: number | null) => (v === null ? "not valued" : money(v));

/** Backlog by value: everything outstanding (SAP backlog, PMs, incidents, opportunities) ranked by what it is worth,
 * formerly All work's Ranked by value view. It ranks the backlog, not the routed work, so it sits under Plan. */
export function BoardPage({ query, filters }: { query: URLSearchParams; filters?: { type?: string; who?: string; q?: string } }) {
  const embedded = !!filters;
  const view = (query.get("view") as View) || "ranked";
  const [text, setText] = useState("");
  const [filter, setFilter] = useState<BoardFilter>({});
  const [page, setPage] = useState(1);
  const [lastVisit] = useState<string | null>(() => store.get<string | null>(LAST_VISIT, null));
  const [saved, setSaved] = useState<Record<string, BoardFilter>>(() => store.get(SAVED, {}));
  const words = useDebounced(text);

  useEffect(() => {
    store.set(LAST_VISIT, new Date().toISOString().slice(0, 19));
  }, []);

  // Under All work the shared bar sets kind, holder (a queue is its bare role name on the board) and words.
  const shared: BoardFilter = filters ? { kind: filters.type, owner: filters.who?.replace(/^role:/, ""), text: filters.q } : {};
  const f: BoardFilter = { ...filter, text: words || undefined, ...Object.fromEntries(Object.entries(shared).filter(([, v]) => v)),
    since: view === "new" ? lastVisit ?? undefined : undefined };
  const board = useAsync(() => api.board(f, page), [JSON.stringify(f), page]);
  const facets = board.data?.facets;

  const group = view === "owner" ? "owner" : view === "area" ? "area" : view === "kind" ? "kind" : null;
  const setOne = (k: keyof BoardFilter, v: string | undefined) => {
    setPage(1);
    setFilter((cur) => ({ ...cur, [k]: v || undefined }));
  };
  const save = async () => {
    const name = (await promptDialog("Name this filter"));
    if (!name) return;
    const next = { ...saved, [name]: { ...filter, text: text || undefined } };
    setSaved(next);
    store.set(SAVED, next);
  };

  return (
    <section>
      {!embedded && <PageHeader>Backlog by value</PageHeader>}
      <Line>
        <Tabs label="Board views" items={VIEWS.map(([v, label]) => ({
          key: v, label, current: view === v, href: href("/ranked", { view: v === "ranked" ? undefined : v }),
        }))} />
        {!embedded && <Input placeholder="Search titles and descriptions" value={text} onChange={(e) => setText(e.target.value)} />}
        {Object.keys(saved).length > 0 && (
          <Select value="" onChange={(e) => {
            const s = saved[e.target.value];
            if (s) { setFilter({ ...s, text: undefined }); setText(s.text ?? ""); setPage(1); }
          }}>
            <option value="">Saved filters…</option>
            {Object.keys(saved).map((k) => <option key={k} value={k}>{k}</option>)}
          </Select>
        )}
        <Button onClick={save}>Save filter</Button>
        <ProjectButton />
      </Line>
      {view === "new" && (
        <Text as="p" small>{lastVisit ? `Cards first seen since ${lastVisit.replace("T", " ")}.` : "First visit: everything is new."}</Text>
      )}
      {group && facets && (
        <div className="chips">
          <Button className={!filter[group as keyof BoardFilter] ? "chip on" : "chip"} onClick={() => setOne(group as keyof BoardFilter, undefined)}>
            all
          </Button>
          {group === "area"
            ? facets.area.map((a) => (
              <Button key={a.id ?? "none"} className={filter.area === a.id ? "chip on" : "chip"}
                onClick={() => setOne("area", a.id ?? undefined)} disabled={!a.id}>
                {a.label ?? "no area"} <Text muted>{a.n}</Text>
              </Button>))
            : Object.entries(facets[group as "owner" | "kind"]).map(([k, n]) => (
              <Button key={k} className={filter[group as keyof BoardFilter] === k ? "chip on" : "chip"}
                onClick={() => setOne(group as keyof BoardFilter, k)}>
                {k.replace("_", " ")} <Text muted>{n}</Text>
              </Button>))}
        </div>
      )}
      <Status of={board} empty={board.data?.total === 0}>
        Nothing on the board{view === "new" ? " since your last visit" : ""}.
      </Status>
      {board.data && board.data.total > 0 && (
        <>
          <Text as="p" small>
            {count(board.data.total)} cards · page {page} of {Math.ceil(board.data.total / board.data.per_page)}
          </Text>
          <ol className="board">
            {board.data.cards.map((c) => <Card key={c.key} card={c} reload={board.reload} />)}
          </ol>
          <Line>
            <Button disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
            <Button disabled={page * board.data.per_page >= board.data.total} onClick={() => setPage(page + 1)}>Next</Button>
          </Line>
        </>
      )}
    </section>
  );
}

function Card({ card: c, reload }: { card: BoardCard; reload: () => void }) {
  const [open, setOpen] = useState(false);
  const up = c.prev_rank !== null && c.prev_rank > c.rank;
  return (
    <li className={`card board-card${c.pinned ? " pinned" : ""}`}>
      <Line>
        <span className="rank mono">{c.rank}</span>
        {c.moved && <UIBadge tone={c.moved === "new" ? "ok" : up ? "ok" : "muted"} title={c.moved}>{c.moved.split(":")[0]}</UIBadge>}
        <UIBadge>{c.kind.replace("_", " ")}</UIBadge>
        {c.item_id ? <a href={href(`/cases/${c.item_id}`)}>{c.title}</a> : <span>{c.title}</span>}
        <span className="spacer" />
        <strong title={c.reason ?? ""}>{valued(c.effective_usd)}</strong>
        <Button variant="link" size="sm" onClick={() => setOpen(!open)}>{open ? "less" : "why"}</Button>
      </Line>
      <Line small muted>
        {c.unit && c.unit_id && <a href={entityHref(c.unit_id)}>{c.unit}</a>}
        {c.area && <span>{c.area}</span>}
        {c.owner && c.owner !== "unassigned" && <span>{c.owner}</span>}
        {c.pinned && <UIBadge title={c.pin_reason ?? ""}>pinned by {c.pinned_by}: {c.pin_reason}</UIBadge>}
        {c.records.length > 1 && <span>{c.records.length} records</span>}
      </Line>
      {open && <Detail card={c} reload={reload} />}
    </li>
  );
}

function Detail({ card: c, reload }: { card: BoardCard; reload: () => void }) {
  const hist = useAsync(() => api.boardHistory(c.key), [c.key]);
  const parts = c.breakdown.components ?? {};
  return (
    <div className="board-detail">
      {c.text && <Text as="p" small>{c.text}</Text>}
      {c.reason && <Text as="p" small>{c.reason}{c.moved && c.moved !== "new" && ` · ${c.moved}`}</Text>}
      {Object.keys(parts).length > 0 && (
        <Table density="compact">
          <tbody>
            {Object.entries(parts).map(([k, p]) => (
              <tr key={k}><td>{k}</td><td>{p.status === "unknown" ? "unknown" : valued(p.value_usd)}</td></tr>
            ))}
            {c.breakdown.urgency && <tr><td>urgency</td><td>× {c.breakdown.urgency.factor}</td></tr>}
            {c.breakdown.effort && <tr><td>effort</td><td>{valued(c.breakdown.effort.value_usd)}</td></tr>}
          </tbody>
        </Table>
      )}
      {c.breakdown.unknown && c.breakdown.unknown.length > 0 && (
        <Text as="p" small>Not known: {c.breakdown.unknown.join(", ")}</Text>
      )}
      <Text as="p" small>
        Records:{" "}
        {c.records.map((r) => {
          const id = r.split(":").slice(1).join(":");
          return (
            <span key={r} className="mono">
              {id.startsWith("fz-") ? <a href={entityHref(id)}>{r}</a> : r.startsWith("work_item:") ? <a href={href(`/cases/${id}`)}>{r}</a> : r}{" "}
            </span>
          );
        })}
      </Text>
      {hist.data && hist.data.length > 1 && (
        <Text as="p" small>
          Rank: {hist.data.map((h) => `${h.rank} (${whenDay(h.at)})`).join(" → ")}
        </Text>
      )}
      <PinForm card={c} reload={reload} />
    </div>
  );
}

function PinForm({ card: c, reload }: { card: BoardCard; reload: () => void }) {
  const [rank, setRank] = useState("");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState(c.pin_reason ?? "");
  const [error, setError] = useState<string | null>(null);
  const by = loadPerson() || "anonymous";
  const send = async (unpin = false) => {
    setError(null);
    try {
      await api.boardPin(c.key, by, unpin ? "" : reason, unpin ? undefined : Number(rank) || undefined,
        unpin || value === "" ? undefined : Number(value));
      notifyChanged();
      reload();
    } catch (e) {
      setError(String(e));
    }
  };
  return (
    <Line small>
      <span>Pin to rank</span>
      <Input width="xs" value={rank} onChange={(e) => setRank(e.target.value)} />
      <span>or value $</span>
      <Input width="sm" value={value} onChange={(e) => setValue(e.target.value)} />
      <Input placeholder="Why (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button disabled={!reason || (!rank && !value)} onClick={() => send()}>Pin</Button>
      {c.pinned && <Button onClick={() => send(true)}>Unpin</Button>}
      <ErrorText inline>{error}</ErrorText>
    </Line>
  );
}

function ProjectButton() {
  const add = async () => {
    const title = (await promptDialog("Project title"));
    if (!title) return;
    const benefit = (await promptDialog("Expected benefit over the horizon, USD (optional)"));
    await api.boardProject({ title, by: loadPerson() || "anonymous", owner: loadPerson() || undefined,
      benefit_usd: benefit ? Number(benefit) : undefined });
    notifyChanged();
    window.location.reload();
  };
  return <Button onClick={add}>Add project</Button>;
}
