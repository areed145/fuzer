import "./styles/bpmn.css";
// A process diagram (bpmn-js viewer) with the path taken and the open steps marked, or any other markers
// and count overlays (the comparison and the monitor). bpmn.io's licence needs its watermark to stay
// visible; the viewer draws it.

import { useEffect, useRef, useState } from "react";
import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";

export interface Marks {
  done: string[];    // elements the process passed
  open: string[];    // elements waiting now
  flows: string[];   // sequence flows taken
}

export default function BpmnView({ xml, marks, classes, overlays, tall }: {
  xml: string;
  marks?: Marks;
  classes?: Record<string, string>;   // element id -> marker class
  overlays?: Record<string, string>;  // element id -> html drawn over its top right corner
  tall?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string>();

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
        canvas.zoom("fit-viewport");
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
      <div className={`bpmn-canvas${tall ? " tall" : ""}`} ref={box} />
      {marks && (
        <div className="legend">
          <span><i className="dot fz-legend-open" /> waiting now</span>
          <span><i className="dot fz-legend-done" /> done</span>
          <span><i className="dot fz-legend-taken" /> path taken</span>
        </div>
      )}
    </>
  );
}
