import { useEffect, useId, useRef, useState } from "react";
import { Text } from "./ui";
import { useTheme } from "./theme";
import { DiagramNeedsReload, staleBuild } from "./DiagramNeedsReload";

/** How long a diagram may take to draw before its source is shown instead. */
const DEADLINE_MS = 10_000;
const token = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();

/** A diagram from text an agent wrote. Mermaid's strict mode sanitizes what it draws and turns off
 *  scripts and click handlers. Anything it can't draw stays readable as its source. */
export function MermaidDiagram({ source }: { source: string }) {
  const element = useRef<HTMLDivElement>(null);
  const id = useId().replace(/\W/g, "");
  const theme = useTheme();
  const [failed, setFailed] = useState(false);
  const [stale, setStale] = useState(false);
  const [drawn, setDrawn] = useState(false);
  useEffect(() => {
    let gone = false;
    setFailed(false); setStale(false); setDrawn(false);
    // A diagram that never finishes drawing reads as its source, not "Drawing the diagram…" for ever (#1348).
    const late = window.setTimeout(() => { if (!gone) setFailed(true); }, DEADLINE_MS);
    import("mermaid").then(async ({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false, securityLevel: "strict", theme: "base", fontFamily: getComputedStyle(document.body).fontFamily,
        themeVariables: {
          primaryColor: token("panel"), primaryTextColor: token("text"), primaryBorderColor: token("line"),
          lineColor: token("muted"), textColor: token("text"), background: token("panel"), edgeLabelBackground: token("panel"),
        },
      });
      const { svg } = await mermaid.render(`mermaid-${id}`, source);
      if (gone || !element.current) return;
      window.clearTimeout(late);
      element.current.innerHTML = svg;
      setDrawn(true);
    }).catch((error) => { if (!gone) { setFailed(true); setStale(staleBuild(error)); } });
    return () => { gone = true; window.clearTimeout(late); };
  }, [source, theme, id]);
  if (stale) return <DiagramNeedsReload source={source} />;
  return <figure className="md-diagram">
    {failed
      ? <Text as="p" small muted>This diagram could not be drawn, so its source is shown below.</Text>
      : <>
        {!drawn && <Text as="p" small muted role="status">Drawing the diagram…</Text>}
        <div ref={element} role="img" aria-label="Diagram from the assistant; its source is below" />
      </>}
    {failed && <pre><code>{source}</code></pre>}
  </figure>;
}
