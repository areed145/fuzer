import { api } from "../api";
import { MapView } from "../MapView";
import { Status, useAsync } from "../lib";

/** Everything with a location: facilities, pipelines and flowlines, wells. */
export function MapPage() {
  const m = useAsync(() => api.map(), []);
  const n = m.data?.features.length ?? 0;
  return (
    <section>
      <div className="row spread">
        <h1>Map</h1>
        <span className="muted small">
          {n.toLocaleString()} located things{m.data?.truncated ? " (first 20,000)" : ""} · click one to open it
        </span>
      </div>
      <Status loading={m.loading && !m.data} error={m.error} empty={m.data && n === 0}>
        Nothing has a location yet. GIS exports (GeoJSON) load like any other source.
      </Status>
      {m.data && n > 0 && <MapView data={m.data} />}
    </section>
  );
}
