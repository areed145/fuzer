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

const money = (v: number | null) =>
  v === null ? "not valued" : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `$${Math.round(v / 1e3)}k` : `$${Math.round(v)}`;

export function BoardPage({ query }: { query: URLSearchParams }) {
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

  const f: BoardFilter = { ...filter, text: words || undefined, since: view === "new" ? lastVisit ?? undefined : undefined };
  const board = useAsync(() => api.board(f, page), [JSON.stringify(f), page]);
  const facets = board.data?.facets;

  const group = view === "owner" ? "owner" : view === "area" ? "area" : view === "kind" ? "kind" : null;
  const setOne = (k: keyof BoardFilter, v: string | undefined) => {
    setPage(1);
    setFilter((cur) => ({ ...cur, [k]: v || undefined }));
  };
  const save = () => {
    const name = window.prompt("Name this filter");
    if (!name) return;
    const next = { ...saved, [name]: { ...filter, text: text || undefined } };
    setSaved(next);
    store.set(SAVED, next);
  };

  return (
    <section>
      <h1>Work board</h1>
      <p className="muted">
        Every outstanding piece of work in one list, ranked by value: SAP backlog, PMs and inspections, incidents and
        upsets the SRE found, workflow items, opportunities, reviews and projects. One card per piece of work.
      </p>
      <div className="row">
        <div className="tabs">
          {VIEWS.map(([v, label]) => (
            <a key={v} className={view === v ? "on" : ""} href={href("/board", { view: v === "ranked" ? undefined : v })}>
              {label}
            </a>
          ))}
        </div>
        <input placeholder="Search titles and descriptions" value={text} onChange={(e) => setText(e.target.value)} />
        {Object.keys(saved).length > 0 && (
          <select value="" onChange={(e) => {
            const s = saved[e.target.value];
            if (s) { setFilter({ ...s, text: undefined }); setText(s.text ?? ""); setPage(1); }
          }}>
            <option value="">Saved filters…</option>
            {Object.keys(saved).map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        )}
        <button onClick={save}>Save filter</button>
        <ProjectButton />
      </div>
      {view === "new" && (
        <p className="muted small">{lastVisit ? `Cards first seen since ${lastVisit.replace("T", " ")}.` : "First visit: everything is new."}</p>
      )}
      {group && facets && (
        <div className="chips">
          <button className={!filter[group as keyof BoardFilter] ? "chip on" : "chip"} onClick={() => setOne(group as keyof BoardFilter, undefined)}>
            all
          </button>
          {group === "area"
            ? facets.area.map((a) => (
              <button key={a.id ?? "none"} className={filter.area === a.id ? "chip on" : "chip"}
                onClick={() => setOne("area", a.id ?? undefined)} disabled={!a.id}>
                {a.label ?? "no area"} <span className="muted">{a.n}</span>
              </button>))
            : Object.entries(facets[group as "owner" | "kind"]).map(([k, n]) => (
              <button key={k} className={filter[group as keyof BoardFilter] === k ? "chip on" : "chip"}
                onClick={() => setOne(group as keyof BoardFilter, k)}>
                {k.replace("_", " ")} <span className="muted">{n}</span>
              </button>))}
        </div>
      )}
      <Status loading={board.loading && !board.data} error={board.error} empty={board.data?.total === 0}>
        Nothing on the board{view === "new" ? " since your last visit" : ""}.
      </Status>
      {board.data && board.data.total > 0 && (
        <>
          <p className="muted small">
            {board.data.total.toLocaleString()} cards · page {page} of {Math.ceil(board.data.total / board.data.per_page)}
          </p>
          <ol className="board">
            {board.data.cards.map((c) => <Card key={c.key} card={c} reload={board.reload} />)}
          </ol>
          <div className="row">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
            <button disabled={page * board.data.per_page >= board.data.total} onClick={() => setPage(page + 1)}>Next</button>
          </div>
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
      <div className="row">
        <span className="rank mono">{c.rank}</span>
        {c.moved && <span className={`badge ${c.moved === "new" ? "ok" : up ? "ok" : ""}`} title={c.moved}>{c.moved.split(":")[0]}</span>}
        <span className="badge">{c.kind.replace("_", " ")}</span>
        {c.item_id ? <a href={href(`/work/item/${c.item_id}`)}>{c.title}</a> : <span>{c.title}</span>}
        <span className="spacer" />
        <strong title={c.reason ?? ""}>{money(c.effective_usd)}</strong>
        <button className="link" onClick={() => setOpen(!open)}>{open ? "less" : "why"}</button>
      </div>
      <div className="row small muted">
        {c.unit && c.unit_id && <a href={entityHref(c.unit_id)}>{c.unit}</a>}
        {c.area && <span>{c.area}</span>}
        <span>{c.owner}</span>
        {c.pinned && <span className="badge key" title={c.pin_reason ?? ""}>pinned by {c.pinned_by}: {c.pin_reason}</span>}
        {c.records.length > 1 && <span>{c.records.length} records</span>}
      </div>
      {open && <Detail card={c} reload={reload} />}
    </li>
  );
}

function Detail({ card: c, reload }: { card: BoardCard; reload: () => void }) {
  const hist = useAsync(() => api.boardHistory(c.key), [c.key]);
  const parts = c.breakdown.components ?? {};
  return (
    <div className="board-detail">
      {c.text && <p className="small">{c.text}</p>}
      {c.reason && <p className="small">{c.reason}{c.moved && c.moved !== "new" && ` · ${c.moved}`}</p>}
      {Object.keys(parts).length > 0 && (
        <table className="small">
          <tbody>
            {Object.entries(parts).map(([k, p]) => (
              <tr key={k}><td>{k}</td><td>{p.status === "unknown" ? "unknown" : money(p.value_usd)}</td></tr>
            ))}
            {c.breakdown.urgency && <tr><td>urgency</td><td>× {c.breakdown.urgency.factor}</td></tr>}
            {c.breakdown.effort && <tr><td>effort</td><td>{money(c.breakdown.effort.value_usd)}</td></tr>}
          </tbody>
        </table>
      )}
      {c.breakdown.unknown && c.breakdown.unknown.length > 0 && (
        <p className="muted small">Not known: {c.breakdown.unknown.join(", ")}</p>
      )}
      <p className="small">
        Records:{" "}
        {c.records.map((r) => {
          const id = r.split(":").slice(1).join(":");
          return (
            <span key={r} className="mono">
              {id.startsWith("fz-") ? <a href={entityHref(id)}>{r}</a> : r.startsWith("work_item:") ? <a href={href(`/work/item/${id}`)}>{r}</a> : r}{" "}
            </span>
          );
        })}
      </p>
      {hist.data && hist.data.length > 1 && (
        <p className="muted small">
          Rank: {hist.data.map((h) => `${h.rank} (${h.at.slice(0, 10)})`).join(" → ")}
        </p>
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
    <div className="row small">
      <span>Pin to rank</span>
      <input style={{ width: 60 }} value={rank} onChange={(e) => setRank(e.target.value)} />
      <span>or value $</span>
      <input style={{ width: 100 }} value={value} onChange={(e) => setValue(e.target.value)} />
      <input placeholder="Why (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <button disabled={!reason || (!rank && !value)} onClick={() => send()}>Pin</button>
      {c.pinned && <button onClick={() => send(true)}>Unpin</button>}
      {error && <span className="error">{error}</span>}
    </div>
  );
}

function ProjectButton() {
  const add = async () => {
    const title = window.prompt("Project title");
    if (!title) return;
    const benefit = window.prompt("Expected benefit over the horizon, USD (optional)");
    await api.boardProject({ title, by: loadPerson() || "anonymous", owner: loadPerson() || undefined,
      benefit_usd: benefit ? Number(benefit) : undefined });
    notifyChanged();
    window.location.reload();
  };
  return <button onClick={add}>Add project</button>;
}
