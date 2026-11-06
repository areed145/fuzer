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

test("Analytics renders declarative daily panels, linked hover/zoom, unknown days and saved JSON", async () => {
  const requests = [];
  await page.route("**/analytics/production-efficiency*", (route) => {
    const query = new URL(route.request().url()).searchParams;
    requests.push(Object.fromEntries(query));
    const end = query.get("end") || "2026-10-14";
    const points = Array.from({ length: 14 }, (_, i) => ({
      date: new Date(Date.parse(end) - (13 - i) * 86_400_000).toISOString().slice(0, 10),
      dynamic_max_sm3d: i === 5 ? null : 100 + i,
      allocated_sm3d: i === 5 ? null : i === 6 ? 132.5 : 80 + i,
      gap_sm3d: i === 5 ? null : i === 6 ? -26.5 : 20,
      efficiency_pct: i === 5 ? null : i === 6 ? 125 : 80,
      active_constraint_id: i === 5 || i === 6 ? null : "export",
      binding_constraint_ids: i === 5 || i === 6 ? [] : i === 3 ? ["export", "compressor"] : ["export"],
      capacity_basis: i === 5 ? "missing" : i === 6 ? "reported_potential" : "declared_limits",
      flags: i === 5 ? ["missing_day"] : [], source_ids: ["report-1"],
    }));
    return route.fulfill({ json: {
      since: points[0].date, until: new Date(Date.parse(end) + 86_400_000).toISOString().slice(0, 10),
      end, step: "day", unit: "Sm3/d", facility: { id: query.get("ref") || "plant", label: "Test plant" },
      source: "Daily production reports", points,
      constraints: [{ id: "export", label: "Export", category: "export" }, { id: "compressor", label: "Compressor", category: "equipment" }],
      summary: { efficiency_pct: 80, allocated_sm3: 1040, dynamic_max_sm3: 1300, paired_days: 13 },
      warnings: ["One production day is unknown"],
    } });
  });
  await page.goto(`${url}/#/analytics`);
  await page.getByRole("heading", { name: "Analytics", exact: true }).waitFor();
  const chart = page.getByRole("img", { name: /shared production and constraint daily charts/ });
  await page.waitForFunction(() => document.querySelector(".analytics-chart .js-plotly-plot")?._fullLayout?.xaxis);
  assert.deepEqual(requests[0], {}, "Blank controls let the API choose the facility's latest report");
  const layout = await chart.evaluate((node) => ({
    hover: node._fullLayout.hovermode, linked: node._fullLayout.hoversubplots,
    axes: node.data.map((trace) => trace.xaxis), unknown: node.data.some((trace) => trace.name === "Unknown"),
    gaps: node.data[0].connectgaps, sample: node.data[0].y[5],
  }));
  assert.equal(layout.hover, "x unified");
  assert.equal(layout.linked, "axis");
  assert.ok(layout.axes.every((axis) => axis === "x"));
  assert.equal(layout.unknown, true);
  assert.equal(layout.gaps, false);
  assert.equal(layout.sample, null);
  // Move over the upper subplot and verify hover details include the linked constraint band.
  await chart.evaluate((node) => node.scrollIntoView({ block: "start" }));
  const coordinates = await chart.evaluate((node) => {
    const box = node.getBoundingClientRect(), l = node._fullLayout;
    return { x: box.x + l.xaxis._offset + l.xaxis.d2p("2026-10-04"),
      y: box.y + l.yaxis._offset + l.yaxis._length / 2 };
  });
  await page.mouse.move(coordinates.x, coordinates.y);
  await page.waitForFunction(() => document.querySelector(".analytics-chart .hoverlayer")?.textContent.includes("Binding: Compressor [equipment] + Export [export] (tied)"));
  const hover = await chart.locator(".hoverlayer").textContent();
  assert.match(hover, /Sm3\/d/);
  assert.match(hover, /Capacity basis/);
  const lowerHover = await chart.evaluate((node) => {
    const box = node.getBoundingClientRect(), l = node._fullLayout;
    return { x: box.x + l.xaxis._offset + l.xaxis.d2p("2026-10-04"),
      y: box.y + l.yaxis2._offset + l.yaxis2._length / 2 };
  });
  await page.mouse.move(lowerHover.x, lowerHover.y);
  await page.waitForFunction(() => {
    const text = document.querySelector(".analytics-chart .hoverlayer")?.textContent || "";
    return text.includes("Dynamic maximum: 103 Sm3/d") && text.includes("Allocated production: 83 Sm3/d") && text.includes("(tied)");
  });
  assert.ok(await chart.evaluate((node) => node._fullLayout.yaxis.range[1] > 132.5), "Rates above capacity are not clipped");
  const originalRange = await chart.evaluate((node) => [...node._fullLayout.xaxis.range]);
  const drag = await chart.evaluate((node) => {
    const box = node.getBoundingClientRect(), l = node._fullLayout;
    return { x: box.x + l.xaxis._offset, y: box.y + l.yaxis._offset, w: l.xaxis._length, h: l.yaxis._length };
  });
  await page.mouse.move(drag.x + drag.w * .2, drag.y + drag.h * .2);
  await page.mouse.down();
  await page.mouse.move(drag.x + drag.w * .7, drag.y + drag.h * .8, { steps: 8 });
  await page.mouse.up();
  await page.waitForFunction((range) => JSON.stringify(document.querySelector(".analytics-chart .js-plotly-plot")._fullLayout.xaxis.range) !== JSON.stringify(range), originalRange);
  assert.ok(await chart.evaluate((node) => node._fullData.every((trace) => trace._xaxis === node._fullLayout.xaxis || trace.xaxis === "x")));
  // Reset, then zoom from the categorical subplot; both panels still use the same range.
  await page.mouse.dblclick(drag.x + drag.w * .5, drag.y + drag.h * .5);
  await page.waitForFunction((range) => JSON.stringify(document.querySelector(".analytics-chart .js-plotly-plot")._fullLayout.xaxis.range) === JSON.stringify(range), originalRange);
  const lowerDrag = await chart.evaluate((node) => {
    const box = node.getBoundingClientRect(), l = node._fullLayout;
    return { x: box.x + l.xaxis._offset, y: box.y + l.yaxis2._offset + l.yaxis2._length / 2, w: l.xaxis._length };
  });
  await page.mouse.move(lowerDrag.x + lowerDrag.w * .2, lowerDrag.y);
  await page.mouse.down();
  await page.mouse.move(lowerDrag.x + lowerDrag.w * .7, lowerDrag.y, { steps: 8 });
  await page.mouse.up();
  await page.waitForFunction((range) => JSON.stringify(document.querySelector(".analytics-chart .js-plotly-plot")._fullLayout.xaxis.range) !== JSON.stringify(range), originalRange);
  assert.ok(await chart.evaluate((node) => node._fullData.every((trace) => trace.xaxis === "x")));
  await page.getByRole("button", { name: "Show daily data", exact: true }).click();
  await page.getByRole("cell", { name: "2026-10-06", exact: true }).waitFor();
  assert.equal(await page.getByRole("row").filter({ hasText: "2026-10-06" }).getByRole("cell", { name: "Unknown", exact: true }).count(), 5);
  const potentialRow = page.getByRole("row").filter({ hasText: "2026-10-07" });
  await potentialRow.getByRole("cell", { name: "Reported potential · physical constraint unknown", exact: true }).waitFor();
  assert.equal(await potentialRow.getByRole("cell", { name: "125", exact: true }).count(), 1);
  const tiedRow = page.getByRole("row").filter({ hasText: "2026-10-04" });
  await tiedRow.getByRole("cell", { name: "Compressor [equipment] + Export [export] (tied)", exact: true }).waitFor();
  await page.getByLabel("End date (inclusive)").fill("2026-10-10");
  await page.getByLabel("Facility ref (optional)").fill("other-plant");
  await page.getByRole("button", { name: "Update", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".analytics-chart .js-plotly-plot")?.data[0].x[13] === "2026-10-10");
  assert.deepEqual(requests.at(-1), { end: "2026-10-10", ref: "other-plant" });
  await page.getByRole("button", { name: "Latest report", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".analytics-chart .js-plotly-plot")?.data[0].x[13] === "2026-10-14");
  assert.deepEqual(requests.at(-1), { ref: "other-plant" });
  await page.getByText("Dashboard JSON · customize & save", { exact: true }).click();
  const editor = page.getByLabel("Dashboard specification");
  const spec = JSON.parse(await editor.inputValue());
  spec.dataSource = "https://example.com";
  await editor.fill(JSON.stringify(spec));
  await page.getByRole("button", { name: "Apply JSON", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Only the production-efficiency" }).waitFor();
  spec.dataSource = "production-efficiency";
  spec.title = "Saved production dashboard";
  spec.panels[0].gap.visible = true;
  await editor.fill(JSON.stringify(spec));
  await page.getByRole("button", { name: "Save dashboard", exact: true }).click();
  await page.getByRole("heading", { name: "Saved production dashboard", exact: true }).waitFor();
  await page.reload();
  await page.getByRole("heading", { name: "Saved production dashboard", exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector(".analytics-chart .js-plotly-plot")?._fullLayout?.xaxis);
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.querySelector(".analytics-chart .js-plotly-plot")?._fullLayout?.width < 400);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.getByRole("button", { name: "Open menu", exact: true }).click();
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
