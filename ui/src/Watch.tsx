/** Watch an asset or a case (P5.1b, #650): its new cases and changes show on the watcher's Home under Watching.
 * Watching never assigns the work to them. */
import { api } from "./api";
import { Button, ErrorText } from "./ui";
import { useAction, useAsync } from "./lib";
import { usePerson } from "./work";

export function WatchButton({ target, label }: { target: string; label: string }) {
  const [person] = usePerson();
  const list = useAsync(() => (person ? api.watching(person) : Promise.resolve([] as string[])), [person]);
  const { busy, error, run } = useAction();
  if (!person || !Array.isArray(list.data)) return null;
  const on = list.data.includes(target);
  const toggle = () => run("watch", () => api.watch(person, target, !on));
  return (
    <span className="watch">
      <Button variant="secondary" aria-pressed={on} disabled={!!busy} onClick={toggle}
        title={on ? `Stop showing changes on this ${label} on your Home` : `Show new cases and changes on this ${label} on your Home, without taking it on`}>
        {on ? "Watching" : "Watch"}
      </Button>
      <ErrorText inline>{error}</ErrorText>
    </span>
  );
}
