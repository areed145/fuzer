import { useSyncExternalStore } from "react";
import { notifyChanged } from "./lib";

const KEY = "fuzer.reviewer";
const CHANGED = "fuzer:person";
let person = "";
let trusted = false;
try {
  person = localStorage.getItem(KEY) ?? "";
} catch {
  console.warn("Fuzer identity storage is unavailable; names last for this visit only.");
}

export const loadPerson = () => person;
export function setTrustedPerson(name: string) {
  trusted = true;
  if (person === name) return;
  person = name;
  window.dispatchEvent(new Event(CHANGED));
  notifyChanged();
}
window.addEventListener("storage", (event) => {
  if (trusted) return;
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
    if (trusted) return;
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
