/** Every schedule on the plan (P5.18): turnarounds, projects and campaigns, each opening its Gantt board. */
import { api } from "../api";
import { Empty, PageHeader, Table } from "../ui";
import { Status, useAsync } from "../lib";
import { acronyms, dayStamp } from "../wording";

export function SchedulesPage() {
  const list = useAsync(() => api.schedules(), []);
  return (
    <section>
      <PageHeader subtitle="Turnarounds, projects and campaigns, each with its activities, logic and resources.">Schedules</PageHeader>
      <Status loading={list.loading} error={list.error} onRetry={list.reload} />
      {Array.isArray(list.data) && (list.data.length ? <Table variant="list" className="phone-rows" aria-label="Schedules">
        <thead><tr><th>Schedule</th><th>Kind</th><th>Runs</th><th>Activities</th></tr></thead>
        <tbody>{list.data.map((x) => <tr key={x.code}>
          <td><a href={`#/plan?schedule=${encodeURIComponent(x.code)}`}><strong>{acronyms(x.title)}</strong></a>{" "}
            <span className="mono small muted">{x.code}</span></td>
          <td data-label="Kind">{x.kind}</td>
          <td data-label="Runs">{dayStamp(x.first_start ?? x.start_at)}{x.last_finish || x.finish_by ? ` to ${dayStamp((x.last_finish ?? x.finish_by)!)}` : ""}</td>
          <td data-label="Activities">{x.activities}</td>
        </tr>)}</tbody>
      </Table> : <Empty>No schedules yet. A turnaround, project or campaign from the planning system appears here.</Empty>)}
    </section>
  );
}
