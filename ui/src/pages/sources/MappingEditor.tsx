/** Editing a mapping (#225): the YAML, saved as a new draft version. */
import { useState } from "react";
import { useAction } from "./common";

export function MappingEditor({ yaml, onSave }: { yaml: string; dataset: string;
                                                  onSave: (text: string, note?: string) => Promise<unknown> }) {
  const [text, setText] = useState(yaml);
  const [note, setNote] = useState("");
  const a = useAction();
  return (
    <>
      <textarea className="yaml mono" rows={Math.min(28, text.split("\n").length + 1)} value={text}
        onChange={(e) => setText(e.target.value)} spellCheck={false} />
      <div className="row">
        <input placeholder="what changed and why" value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="primary" disabled={!!a.busy || text === yaml} onClick={() => a.run("save", () => onSave(text, note || undefined))}>
          Save as draft</button>
        {text !== yaml && <button className="secondary" onClick={() => setText(yaml)}>Undo edits</button>}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}
