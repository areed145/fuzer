import { useSyncExternalStore } from "react";
import { Button, Card, Checkbox, Field, Notice, Select, Text } from "./ui";

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

/** Field mode (P5.17g): larger targets, larger type and high contrast for gloves and sun, kept on this device. */
const FIELD_KEY = "fuzer.fieldMode";
let fieldMode = false;
try { fieldMode = localStorage.getItem(FIELD_KEY) === "on"; } catch { /* storage unavailable: off */ }
function applyFieldMode() {
  document.documentElement.classList?.toggle("field-mode", fieldMode);
  window.dispatchEvent(new Event(THEME_CHANGED));
}
export function setFieldMode(on: boolean) {
  fieldMode = on;
  try { localStorage.setItem(FIELD_KEY, on ? "on" : "off"); } catch { /* lasts for this visit */ }
  applyFieldMode();
}
/** Whether this device has made a choice about field mode yet (so it is offered once). */
export const fieldModeChosen = () => { try { return localStorage.getItem(FIELD_KEY) !== null; } catch { return true; } };
applyFieldMode();
export const useFieldMode = () => useSyncExternalStore(subscribe, () => fieldMode);

/** The menu is a grid of app icons, as on an iPhone's home screen; their names under them are on unless turned off. */
const LABELS_KEY = "fuzer.menuLabels";
let menuLabels = true;
try { menuLabels = localStorage.getItem(LABELS_KEY) !== "off"; } catch { /* storage unavailable: on */ }
export function setMenuLabels(on: boolean) {
  menuLabels = on;
  try { localStorage.setItem(LABELS_KEY, on ? "on" : "off"); } catch { /* lasts for this visit */ }
  window.dispatchEvent(new Event(THEME_CHANGED));
}
window.addEventListener("storage", (event) => {
  if (event.key === LABELS_KEY) { menuLabels = event.newValue !== "off"; window.dispatchEvent(new Event(THEME_CHANGED)); }
});
export const useMenuLabels = () => useSyncExternalStore(subscribe, () => menuLabels);

export function useTheme() {
  return useSyncExternalStore(subscribe, () => document.documentElement.dataset.theme);
}

export function Appearance() {
  useSyncExternalStore(subscribe, () => `${preference}|${persistenceError}|${document.documentElement.dataset.theme}|${fieldMode}|${menuLabels}`);
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
      <Text as="p" small>Currently using {theme} mode.</Text>
      <label className="row">
        <input type="checkbox" checked={fieldMode} onChange={(e) => setFieldMode(e.target.checked)} />
        <span>Field mode: larger buttons for gloves and high contrast for sunlight, on this device</span>
      </label>
      <Checkbox checked={menuLabels} onChange={setMenuLabels}>Names under the menu icons</Checkbox>
      {persistenceError && <p role="status" className="error small">{persistenceError}</p>}
    </Card>
  );
}

/** Offered once, the first time the installed app opens on a phone (P5.17g). */
export function FieldModeOffer() {
  const on = useFieldMode();
  const offer = !on && !fieldModeChosen() && document.documentElement.classList.contains("installed") && window.innerWidth <= 900;
  if (!offer) return null;
  return <Notice className="row">
    <span>Gloves on, or in bright sun? Field mode makes buttons larger and the screen easier to read.</span>
    <Button variant="primary" onClick={() => setFieldMode(true)}>Turn on field mode</Button>
    <Button onClick={() => setFieldMode(false)}>Not now</Button>
  </Notice>;
}
