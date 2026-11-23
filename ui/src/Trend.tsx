// Measurements PI doesn't have (well tests, lab results, wall thickness at CMLs) on an entity, over their whole history:
// a trend spec from the server (a panel per quantity and location), drawn by the one chart.
import { api } from "./api";
import { Status, useAsync } from "./lib";
import { TrendChart } from "./trends/TrendChart";
import { Card as UICard, Disclosure, Text } from "./ui";

/** The entity's landed readings, closed until asked for; hidden when there are none. The trend above draws them over
 * its window. */
export function MeasurementsCard({ id }: { id: string }) {
  const m = useAsync(() => api.measurementsSpec(id), [id]);
  const spec = m.data;
  if (!m.loading && !m.error && (!spec || spec.error || !spec.series?.length)) return null;
  const on = new Set((spec?.series ?? []).map((s) => s.key));
  return (
    <UICard as="section">
      <Disclosure label={<strong>Tests and samples, whole history ({spec?.series?.length ?? 0})</strong>}>
        <Status of={m} />
        {spec && !spec.error && spec.series?.length > 0 && <TrendChart spec={spec} timeline={null} on={on}
          label={`${spec.series.length} measured quantities from ${spec.time.start} to ${spec.time.end}`} />}
      </Disclosure>
      {spec?.truncated && <Text as="p" small>Showing the latest readings only.</Text>}
    </UICard>
  );
}
