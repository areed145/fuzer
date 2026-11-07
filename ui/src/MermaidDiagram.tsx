import { useEffect, useId, useRef, useState } from "react";
import { useTheme } from "./theme";

const token = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();

/** A diagram from text an agent wrote. Mermaid's strict mode sanitizes what it draws and turns off
 *  scripts and click handlers. Anything it can't draw stays readable as its source. */
export function MermaidDiagram({ source }: { source: string }) {
  const element = useRef<HTMLDivElement>(null);
  const id = useId().replace(/\W/g, "");
  const theme = useTheme();
  const [failed, setFailed] = useState(false);
  const [drawn, setDrawn] = useState(false);
  useEffect(() => {
    let gone = false;
    setFailed(false); setDrawn(false);
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
      element.current.innerHTML = svg;
      setDrawn(true);
    }).catch(() => { if (!gone) setFailed(true); });
    return () => { gone = true; };
  }, [source, theme, id]);
  return <figure className="md-diagram">
    {failed
      ? <p className="small muted">This diagram could not be drawn, so its source is shown below.</p>
      : <>
        {!drawn && <p className="small muted" role="status">Drawing the diagram…</p>}
        <div ref={element} role="img" aria-label="Diagram from the assistant; its source is below" />
      </>}
    <details open={failed}>
      <summary className="small muted">Diagram source</summary>
      <pre><code>{source}</code></pre>
    </details>
  </figure>;
}
