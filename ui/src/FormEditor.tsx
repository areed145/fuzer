import "./styles/form-editor.css";
// The form-js editor for task forms (#97). Fields beside the components (the form's `id`, `fuzer.bulk`
// bulk actions) are kept as they are when the schema is saved.

import { useEffect, useRef, useState } from "react";
import { FormEditor as Editor } from "@bpmn-io/form-js-editor";
import "@bpmn-io/form-js-viewer/dist/assets/form-js.css";
import "@bpmn-io/form-js-editor/dist/assets/form-js-editor.css";
import "@bpmn-io/form-js-editor/dist/assets/properties-panel.css";

export default function FormEditor({ schema, onReady, onDirty }: {
  schema: Record<string, unknown>;
  onReady: (save: (() => Record<string, unknown>) | undefined) => void;
  onDirty: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const editor = new Editor({ container: box.current! });
    let live = true;
    editor.importSchema(schema as any).then(
      () => {
        if (!live) return;
        onReady(() => editor.saveSchema() as Record<string, unknown>);
        editor.on("changed", onDirty);
      },
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
      onReady(undefined);
      editor.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema]);

  return (
    <>
      {error && <div className="status error">Couldn't open the form: {error}</div>}
      <div className="form-editor" ref={box} />
    </>
  );
}
