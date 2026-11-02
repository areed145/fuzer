// A task's form-js form, started from the process data. Submitting returns only the form's own fields.

import { useEffect, useRef, useState } from "react";
import { Form } from "@bpmn-io/form-js-viewer";
import "@bpmn-io/form-js-viewer/dist/assets/form-js.css";

export default function TaskForm({ schema, data, disabled, busy, onSubmit }: {
  schema: Record<string, unknown>;
  data: Record<string, unknown>;
  disabled?: boolean;
  busy?: boolean;
  onSubmit: (data: Record<string, unknown>) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const form = useRef<Form>(null);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const f = new Form({ container: box.current! });
    form.current = f;
    f.importSchema(schema, data).catch((e: Error) => setError(e.message));
    return () => {
      form.current = null;
      f.destroy();
    };
  }, [schema, data]);

  useEffect(() => {
    form.current?.setProperty("readOnly", !!disabled);
  }, [disabled, schema, data]);

  const submit = () => {
    if (!form.current) return;
    const { data: values, errors } = form.current.submit();
    if (Object.keys(errors).length) {
      setError("Fill in the highlighted fields.");
      return;
    }
    setError(undefined);
    onSubmit(values);
  };

  return (
    <div className="task-form">
      <div ref={box} />
      {error && <p className="error small">{error}</p>}
      <div className="actions">
        <button className="primary" disabled={disabled || busy} onClick={submit}>Complete</button>
      </div>
    </div>
  );
}
