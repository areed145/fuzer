import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Button, Field, Input } from "./index";

/** The app's one modal: a question the person answers before the action goes ahead. Call `confirmDialog` or
 * `promptDialog` from a handler and await the answer; questions asked while one is open wait their turn. Use these
 * instead of the browser's `confirm` and `prompt`. `DialogHost` is mounted once, by the shell. */
type Request = {
  id: number; message: string; confirm: string; danger: boolean;
  input?: { label: string; value: string };
  answer: (value: boolean | string | null) => void;
};

let queue: Request[] = [];
let next = 1;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((listener) => listener());
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };

function ask<T extends boolean | string | null>(request: Omit<Request, "id" | "answer">): Promise<T> {
  return new Promise<T>((resolve) => {
    const id = next++;
    queue = [...queue, { ...request, id, answer: (value) => {
      queue = queue.filter((r) => r.id !== id);
      changed();
      resolve(value as T);
    } }];
    changed();
  });
}

/** Ask before a shared-state change: resolves true when the person confirms. `message` says what will happen. */
export const confirmDialog = (message: string, options: { confirm?: string; danger?: boolean } = {}): Promise<boolean> =>
  ask<boolean>({ message, confirm: options.confirm ?? "Confirm", danger: !!options.danger });

/** Ask for one short value: resolves the trimmed text, or null when the person cancels or leaves it empty. */
export const promptDialog = (label: string, options: { confirm?: string; value?: string } = {}): Promise<string | null> =>
  ask<string | null>({ message: label, confirm: options.confirm ?? "Save", danger: false, input: { label, value: options.value ?? "" } });

export function DialogHost() {
  const current = useSyncExternalStore(subscribe, () => queue[0]);
  const ref = useRef<HTMLDialogElement>(null);
  const [text, setText] = useState("");
  useEffect(() => {
    if (!current) return;
    setText(current.input?.value ?? "");
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, [current]);
  if (!current) return null;
  const cancel = () => current.answer(current.input ? null : false);
  const accept = () => current.answer(current.input ? text.trim() || null : true);
  return <dialog ref={ref} className="ui-dialog" aria-label={current.message} onCancel={(e) => { e.preventDefault(); cancel(); }}>
    <form onSubmit={(e) => { e.preventDefault(); accept(); }}>
      {current.input
        ? <Field label={current.input.label} width="full"><Input autoFocus value={text} onChange={(e) => setText(e.target.value)} /></Field>
        : <p>{current.message}</p>}
      <div className="ui-dialog-actions">
        <Button type="button" autoFocus={!current.input} onClick={cancel}>Cancel</Button>
        <Button type="submit" variant={current.danger ? "danger" : "primary"}>{current.confirm}</Button>
      </div>
    </form>
  </dialog>;
}
