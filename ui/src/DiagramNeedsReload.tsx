import { Button } from "./ui";

/** A built file the page asked for is gone: the app was deployed again since this page opened (P5.18a). */
export const staleBuild = (error: unknown) =>
  /dynamically imported module|Importing a module script failed|error loading dynamically imported|Loading chunk|module script/i
    .test(String((error as Error)?.message ?? error));

/** What a diagram says when its drawing code belongs to an earlier deploy: reload, and the source meanwhile. */
export function DiagramNeedsReload({ source }: { source: string }) {
  return <figure className="md-diagram">
    <p className="small muted" role="status">Fuzer was updated since this page opened. Reload to draw this diagram.{" "}
      <Button variant="link" size="sm" onClick={() => location.reload()}>Reload</Button></p>
    <pre><code>{source}</code></pre>
  </figure>;
}
