import { useSyncExternalStore } from "react";
import { Card, Field, Select } from "./ui";

export type ThemePreference = "system" | "light" | "dark";
const KEY = "fuzer.theme";
export const THEME_CHANGED = "fuzer:theme";
const scheme = window.matchMedia("(prefers-color-scheme: dark)");
const valid = (value: string | null): ThemePreference =>
  value === "light" || value === "dark" ? value : "system";
let preference: ThemePreference = "system";
let persistenceError = "";
try {
  preference = valid(localStorage.getItem(KEY));
} catch {
  persistenceError = "Browser storage is unavailable; appearance changes last only for this visit.";
}

function apply() {
  document.documentElement.dataset.theme = preference === "system" ? (scheme.matches ? "dark" : "light") : preference;
  window.dispatchEvent(new Event(THEME_CHANGED));
}

export function setTheme(value: ThemePreference) {
  preference = value;
  try {
    localStorage.setItem(KEY, value);
    persistenceError = "";
  } catch {
    persistenceError = "Browser storage is unavailable; appearance changes last only for this visit.";
  }
  apply();
}

apply();
scheme.addEventListener("change", apply);
window.addEventListener("storage", (event) => {
  if (event.key === KEY || event.key === null) {
    preference = valid(event.newValue);
    apply();
  }
});
const subscribe = (onChange: () => void) => {
  window.addEventListener(THEME_CHANGED, onChange);
  return () => window.removeEventListener(THEME_CHANGED, onChange);
};

export function useTheme() {
  return useSyncExternalStore(subscribe, () => document.documentElement.dataset.theme);
}

export function Appearance() {
  useSyncExternalStore(subscribe, () => `${preference}|${persistenceError}|${document.documentElement.dataset.theme}`);
  const theme = document.documentElement.dataset.theme;
  return (
    <Card title="Appearance">
      <Field label="Color theme" hint="Saved in this browser. System follows your device's appearance.">
        <Select value={preference} onChange={(e) => setTheme(valid(e.target.value))}>
          <option value="system">System</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </Select>
      </Field>
      <p className="muted small">Currently using {theme} mode.</p>
      {persistenceError && <p role="status" className="error small">{persistenceError}</p>}
    </Card>
  );
}
