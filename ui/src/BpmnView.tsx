// A process diagram (bpmn-js viewer) with the path taken and the open steps marked. bpmn.io's licence
// needs its watermark to stay visible; the viewer draws it.

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

export default function BpmnView({ xml, marks }: { xml: string; marks: Marks }) {
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
        mark(marks.flows, "fz-taken");
        mark(marks.done.filter((id) => !marks.open.includes(id)), "fz-done");
        mark(marks.open, "fz-open");
        canvas.zoom("fit-viewport");
      },
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
      viewer.destroy();
    };
  }, [xml, marks]);

  return (
    <>
      {error && <div className="status error">Couldn't draw the process: {error}</div>}
      <div className="bpmn-canvas" ref={box} />
      <div className="legend">
        <span><i className="dot fz-legend-open" /> waiting now</span>
        <span><i className="dot fz-legend-done" /> done</span>
        <span><i className="dot fz-legend-taken" /> path taken</span>
      </div>
    </>
  );
}
