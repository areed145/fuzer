import { before, beforeEach, after, afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chromium } from "playwright";

const url = "http://127.0.0.1:5174";
let server, browser, context, page, errors;
before(async () => {
  server = spawn(process.execPath, ["node_modules/vite/bin/vite.js", "preview", "--host", "127.0.0.1", "--port", "5174", "--strictPort"], {
    cwd: new URL("..", import.meta.url), stdio: "ignore",
  });
  for (let attempt = 0; ; attempt++) {
    try { const response = await fetch(url); if (response.ok) break; } catch {}
    if (attempt === 100 || server.exitCode !== null) throw Error("UI preview did not start on port 5174");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  browser = await chromium.launch();
});
after(async () => { await browser?.close(); server?.kill("SIGTERM"); });
beforeEach(async () => {
  context = await browser.newContext();
  page = await context.newPage();
  errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && message.text().includes("Fuzer page rendering failed")) errors.push(message.text());
  });
  const fixtures = {
    "/stats": { facility: "Test facility", pack: "okan", records: 0, entities: {}, edges: 0, open_reviews: {}, rebuild: {} },
    "/settings": { project: null, projects: [], settings: [] },
    "/board": { cards: [], total: 0, page: 1, per_page: 50 },
    "/incidents": { items: [], total: 0 },
    "/jobs/health": { available: false, level: "unknown", summary: "" },
    "/jobs": { jobs: [], checks: [], health: { summary: "No jobs configured" } },
    "/ontology/declared": { groups: [], views: [], kinds: [], relations: [] },
  };
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/assets/") || path === "/" || path.endsWith(".svg") || path.endsWith(".png")) return route.continue();
    await route.fulfill({ json: fixtures[path] ?? [] });
  });
});
afterEach(async () => {
  await context.close();
  assert.deepEqual(errors, [], "No unhandled rendering errors");
});

test("Home guides empty-register setup and routes recover instead of silently showing Browse", async () => {
  await page.goto(url);
  await page.getByText("Connect data before exploring", { exact: true }).waitFor();
  assert.match(await page.getByRole("link", { name: "Open source setup" }).getAttribute("href"), /view=add/);
  await page.goto(`${url}/#/?q=pump`);
  await page.getByRole("heading", { name: "Browse", exact: true }).waitFor();
  await page.goto(`${url}/#/unknown`);
  await page.getByText("Page not found", { exact: true }).waitFor();
  await page.goto(`${url}/#/%E0%A4%A`);
  await page.getByText("Page not found", { exact: true }).waitFor();
});

test("Appearance persists, overrides the OS and stays controlled when the effective color is unchanged", async () => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(`${url}/#/settings`);
  const select = page.getByLabel("Color theme");
  await select.selectOption("dark");
  assert.equal(await select.inputValue(), "dark");
  await select.selectOption("light");
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
  const light = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await page.reload();
  assert.equal(await select.inputValue(), "light");
  await select.selectOption("dark");
  const dark = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  assert.notEqual(dark, light);
  await select.selectOption("system");
  await page.emulateMedia({ colorScheme: "light" });
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
  const second = await context.newPage();
  await second.goto(url);
  await second.evaluate(() => localStorage.setItem("fuzer.theme", "dark"));
  await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
});

test("mobile navigation traps focus, restores it on Escape and does not overlap the header", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(url);
  const menu = page.getByRole("button", { name: "Open menu", exact: true });
  await menu.click();
  await page.waitForFunction(() => document.body.style.overflow === "hidden");
  await page.waitForFunction(() => document.getElementById("sidebar").getBoundingClientRect().x === 0);
  assert.equal(await page.evaluate(() => document.getElementById("sidebar").contains(document.activeElement)), true);
  await page.keyboard.press("Shift+Tab");
  assert.equal(await page.evaluate(() => document.getElementById("sidebar").contains(document.activeElement)), true);
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.getElementById("sidebar").contains(document.activeElement)), true);
  assert.equal(await page.evaluate(() => {
    const drawer = document.getElementById("sidebar").getBoundingClientRect();
    return document.getElementById("sidebar").contains(document.elementFromPoint(drawer.x + 20, drawer.y + 20));
  }), true);
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Open menu");
  assert.equal(await page.evaluate(() => document.body.style.overflow), "");
  await page.getByRole("link", { name: "Skip to content" }).focus();
  await page.keyboard.press("Enter");
  assert.equal(await page.evaluate(() => document.activeElement?.id), "main-content");
});

test("identity changes clear the previous person's inbox while a new response is pending", async () => {
  await context.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ana"));
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route("**/work/inbox?*", async (route) => {
    const person = new URL(route.request().url()).searchParams.get("person");
    if (person === "ben") await held;
    await route.fulfill({ json: { person, roles: [`${person}-role`], mine: [], queue: [], counts: { mine: { total: 0 }, queue: { total: 0 } } } });
  });
  await page.goto(`${url}/#/inbox`);
  await page.getByText("ana-role", { exact: false }).waitFor();
  await page.getByRole("button", { name: "ana", exact: true }).click();
  await page.getByLabel("You are", { exact: true }).fill("ben");
  await page.getByRole("button", { name: "Use this name" }).click();
  await page.getByText("ana-role", { exact: false }).waitFor({ state: "hidden" });
  release();
  await page.getByText("ben-role", { exact: false }).waitFor();
});

test("refreshing the same resource keeps its current data visible until the update arrives", async () => {
  await context.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ana"));
  const inbox = (role) => ({ person: "ana", roles: [role], mine: [], queue: [], counts: { mine: { total: 0 }, queue: { total: 0 } } });
  await page.route("**/work/inbox?*", (route) => route.fulfill({ json: inbox("current-role") }));
  await page.goto(`${url}/#/inbox`);
  await page.getByText("current-role", { exact: false }).waitFor();
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route("**/work/inbox?*", async (route) => {
    await held;
    await route.fulfill({ json: inbox("updated-role") });
  });
  const request = page.waitForRequest((request) => new URL(request.url()).pathname === "/work/inbox");
  await page.evaluate(() => window.dispatchEvent(new Event("fuzer:changed")));
  await request;
  assert.equal(await page.getByText("current-role", { exact: false }).isVisible(), true);
  release();
  await page.getByText("updated-role", { exact: false }).waitFor();
});

test("API failures remain visible and offer guided recovery rather than breaking the shell", async () => {
  await page.route("**/entities?*", (route) => route.fulfill({ status: 503, json: { detail: "Register unavailable" } }));
  await page.goto(`${url}/#/browse?q=pump`);
  await page.getByRole("alert").filter({ hasText: "Register unavailable" }).waitFor();
  await page.getByRole("heading", { name: "Browse", exact: true }).waitFor();
  await page.getByRole("link", { name: "Home", exact: true }).first().click();
  await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
});

test("Ask opens and can be left without DOM effect-cleanup crashes", async () => {
  await page.goto(`${url}/#/ask`);
  await page.getByRole("heading", { name: "Ask", exact: true }).waitFor();
  await page.getByRole("link", { name: "Home", exact: true }).first().click();
  await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
});

test("a rendering failure has a usable recovery screen and Return home restores the shell", async () => {
  await page.route("**/entities?*", (route) => route.fulfill({ json: { length: 1 } }));
  await page.goto(`${url}/#/browse?q=pump`);
  await page.getByRole("heading", { name: "This page could not be displayed", exact: true }).waitFor();
  assert.ok(errors.length > 0, "Rendering failure was logged");
  assert.ok(errors.every((message) => message.includes("map is not a function")));
  errors.length = 0;
  await page.getByRole("button", { name: "Return home", exact: true }).click();
  await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
});
