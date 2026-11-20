import { PageHeader, Line, Text } from "../ui";
import { count } from "../format";
import { api } from "../api";
import { MapView } from "../MapView";
import { Status, useAsync } from "../lib";

/** Everything with a location: facilities, pipelines and flowlines, wells. */
export function MapPage() {
  const m = useAsync(() => api.map(), []);
  const n = m.data?.features.length ?? 0;
  return (
    <section>
      <Line spread>
        <PageHeader>Map</PageHeader>
        <Text small muted>
          {count(n)} located things{m.data?.truncated ? " (first 20,000)" : ""} · select one to open it
        </Text>
      </Line>
      <Status of={m} empty={m.data && n === 0}>
        Nothing has a location yet. GIS exports (GeoJSON) load like any other source.
      </Status>
      {m.data && n > 0 && <MapView data={m.data} />}
    </section>
  );
}
