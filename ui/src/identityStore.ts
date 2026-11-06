import { useSyncExternalStore } from "react";
import { notifyChanged } from "./lib";

const KEY = "fuzer.reviewer";
const CHANGED = "fuzer:person";
let person = "";
try {
  person = localStorage.getItem(KEY) ?? "";
} catch {
  console.warn("Fuzer identity storage is unavailable; names last for this visit only.");
}

export const loadPerson = () => person;
window.addEventListener("storage", (event) => {
  if (event.key === KEY || event.key === null) {
    person = event.newValue ?? "";
    window.dispatchEvent(new Event(CHANGED));
    notifyChanged();
  }
});
const subscribe = (onChange: () => void) => {
  window.addEventListener(CHANGED, onChange);
  return () => window.removeEventListener(CHANGED, onChange);
};

/** Shared across pages and tabs, including when browser persistence is unavailable. */
export function usePerson(): [string, (name: string) => void] {
  const name = useSyncExternalStore(subscribe, loadPerson);
  return [name, (next) => {
    person = next;
    try {
      localStorage.setItem(KEY, person);
    } catch {
      console.warn("Fuzer identity could not be saved in browser storage; it lasts for this visit only.");
    }
    window.dispatchEvent(new Event(CHANGED));
    notifyChanged();
  }];
}
