import "./styles/bpmn.css";
// A process diagram (bpmn-js viewer) with the path taken and the open steps marked, or any other markers
// and count overlays (the comparison and the monitor). bpmn.io's licence needs its watermark to stay
// visible; the viewer draws it.

import { useEffect, useRef, useState } from "react";
import { Button } from "./ui";
import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";

export interface Marks {
  done: string[];    // elements the process passed
  open: string[];    // elements waiting now
  flows: string[];   // sequence flows taken
}

// Below this zoom task labels are too small to read (#406); a bigger process then opens on its waiting step at
// this zoom, with the whole process one click away.
const MIN_ZOOM = 0.75;

export default function BpmnView({ xml, marks, classes, overlays, tall }: {
  xml: string;
  marks?: Marks;
  classes?: Record<string, string>;   // element id -> marker class
  overlays?: Record<string, string>;  // element id -> html drawn over its top right corner
  tall?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<any>(null);
  const focusRef = useRef<any>(null);
  const [error, setError] = useState<string>();
  const [zoomed, setZoomed] = useState<"fit" | "step" | null>(null);

  useEffect(() => {
    const viewer = new NavigatedViewer({ container: box.current! });
    let live = true;
    viewer.importXML(xml).then(
      () => {
        if (!live) return;
        const canvas = viewer.get("canvas") as any;
        const registry = viewer.get("elementRegistry") as any;
        const mark = (ids: string[], cls: string) => ids.forEach((id) => registry.get(id) && canvas.addMarker(id, cls));
        if (marks) {
          mark(marks.flows, "fz-taken");
          mark(marks.done.filter((id) => !marks.open.includes(id)), "fz-done");
          mark(marks.open, "fz-open");
        }
        Object.entries(classes ?? {}).forEach(([id, cls]) => mark([id], cls));
        const ov = viewer.get("overlays") as any;
        Object.entries(overlays ?? {}).forEach(([id, html]) => {
          if (registry.get(id)) ov.add(id, { position: { top: -14, right: 10 }, html: `<div class="fz-count">${html}</div>` });
        });
        canvasRef.current = canvas;
        const fit = canvas.zoom("fit-viewport");
        const first = (marks?.open ?? []).map((id) => registry.get(id)).find(Boolean)
          ?? registry.filter((e: any) => e.type === "bpmn:StartEvent")[0];
        focusRef.current = first ?? null;
        if (fit < MIN_ZOOM && first) {
          canvas.zoom(MIN_ZOOM);
          canvas.scrollToElement(first, { top: 60, bottom: 60, left: 120, right: 120 });
          setZoomed("step");
        } else setZoomed(null);
      },
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
      viewer.destroy();
    };
  }, [xml, marks, classes, overlays]);

  return (
    <>
      {error && <div className="status error">Couldn't draw the process: {error}</div>}
      {zoomed && (
        <div className="row small bpmn-zoom">
          <span className="muted">{zoomed === "step" ? "Showing the waiting step at a readable size." : "Showing the whole process."}
            {" "}Drag to pan; scroll to zoom.</span>
          <Button type="button" size="sm" onClick={() => {
            const canvas = canvasRef.current;
            if (!canvas) return;
            if (zoomed === "step") { canvas.zoom("fit-viewport"); setZoomed("fit"); }
            else { canvas.zoom(MIN_ZOOM); if (focusRef.current) canvas.scrollToElement(focusRef.current); setZoomed("step"); }
          }}>{zoomed === "step" ? "Show the whole process" : "Zoom to the waiting step"}</Button>
        </div>
      )}
      <div className={`bpmn-canvas${tall ? " tall" : ""}`} ref={box} />
      {marks && (
        <div className="legend bpmn-legend">
          <span><i className="dot fz-legend-open" /> waiting now</span>
          <span><i className="dot fz-legend-done" /> done</span>
          <span><i className="dot fz-legend-taken" /> path taken</span>
        </div>
      )}
    </>
  );
}
