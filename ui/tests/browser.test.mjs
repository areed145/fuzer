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
    "/auth/me": { mode: "local", configured: true, user: null },
    "/work/inbox": { person: "", roles: [], mine: [], queue: [],
      counts: { mine: { total: 0 }, queue: { total: 0 } } },
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

const investigation = {
  title: "Why investigate train B's March trips?",
  since: "2024-03-01", until: "2024-04-01", asset_id: "fz-compressor", asset_label: "20-K-2200B",
  asset_fields: [{ name: "Description", value: "Train B second-stage compressor", source: "sap" }],
  asset_records: [{ source: "sap", record_type: "floc", key: "OKAN-20-K2200B", observed_at: "2026-11-06T12:00:00Z" }],
  evidence: [{
    id: "fz-downtime", kind: "downtime", label: "OKDT-00001", at: "2024-03-14T03:20:00",
    text: "Compressor tripped; lift gas shortfall recorded.", lost_oil_sm3: 423,
    records: [{ source: "ec", record_type: "downtime", key: "OKDT-00001", observed_at: "2026-11-06T12:00:00Z" }],
  }],
  findings: [{ text: "The linked record reports 423 Sm3 of lost oil, not a daily rate.", evidence_ids: ["fz-downtime"] }],
  limitations: ["Historical synthetic data does not prove a root cause or the effectiveness of a repair."],
};

const signedInUser = {
  id: "test-account", actor: "user:test-account", display_name: "Test participant", enabled: true,
  owner: false, roles: ["reader"], permissions: ["read"], csrf: "browser-csrf-test",
};

const setupSettings = {
  project: "okan", projects: ["okan"],
  deployment: { mode: "microsoft", configured: true, bootstrap_present: true },
  settings: [
    { key: "agents.provider", group: "Agents", type: "choice", value: "anthropic", default: "anthropic",
      global: null, project: null, from: "default", restart: true, scopes: ["global", "project"], description: "Assistant provider" },
    { key: "agents.api_key", group: "Agents", type: "secret", value: "env:DEMO_AI_KEY", default: "env:ANTHROPIC_API_KEY",
      global: null, project: "env:DEMO_AI_KEY", from: "project", resolves: false, restart: true,
      scopes: ["global", "project"], description: "Assistant secret reference" },
    { key: "assistant.timeout_seconds", group: "Agents", type: "int", value: 300, default: 300,
      global: null, project: null, from: "default", restart: false, scopes: ["global", "project"],
      description: "Question timeout", min: 30, max: 900 },
    { key: "sources.pi.url", group: "Sources", type: "str", value: "https://legacy.invalid", default: "",
      global: null, project: null, from: "environment", restart: true, scopes: ["global", "project"],
      description: "Legacy source seeding" },
  ],
};

test("Source readiness belongs in Sources and Settings remains application-wide on desktop/mobile", async () => {
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true,
    user: { ...signedInUser, roles: ["administrator"], permissions: ["read", "configure"] } } }));
  await page.route("**/settings?*", (route) => route.fulfill({ json: setupSettings }));
  await page.route("**/settings", (route) => route.fulfill({ json: setupSettings }));
  let catalogFails = true;
  await page.route("**/sources/catalog", (route) => route.fulfill(catalogFails
    ? { status: 503, json: { detail: "Catalog unavailable. Try again." } }
    : { json: { systems: [], types: { connections: {} } } }));
  await page.route("**/jobs/health", (route) => route.fulfill({ json: {
    available: false, level: "unknown", summary: "Scheduler has not run here yet.",
  } }));
  const mutations = [];
  page.on("request", (request) => { if (!["GET", "HEAD"].includes(request.method())) mutations.push(request.url()); });
  await page.goto(url);
  await page.getByRole("link", { name: "Source readiness", exact: false }).click();
  await page.getByRole("heading", { name: "Source readiness", exact: true }).waitFor();
  await page.getByText("Catalog unavailable. Try again.", { exact: false }).waitFor();
  catalogFails = false;
  await page.getByRole("button", { name: "Refresh source readiness" }).click();
  await page.getByText("0 connections", { exact: false }).waitFor();
  assert.match(await page.getByRole("link", { name: "Connect & test" }).getAttribute("href"), /view=systems/);
  if (process.env.FUZER_REVIEW_DIR) await page.screenshot({ path: `${process.env.FUZER_REVIEW_DIR}/setup-readiness-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (process.env.FUZER_REVIEW_DIR) await page.screenshot({ path: `${process.env.FUZER_REVIEW_DIR}/setup-readiness-mobile.png`, fullPage: true });
  await page.getByRole("main").getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("heading", { name: "Application configuration", exact: true }).waitFor();
  await page.getByText("Attention: the owner bootstrap secret", { exact: false }).waitFor();
  assert.equal(await page.getByRole("heading", { name: "Source readiness", exact: true }).count(), 0);
  await page.getByRole("link", { name: "Configure assistant", exact: true }).click();
  await page.getByRole("textbox", { name: "Filter settings" }).waitFor();
  assert.equal(await page.getByRole("textbox", { name: "Filter settings" }).inputValue(), "agents.");
  await page.getByText("agents.api_key", { exact: true }).waitFor();
  assert.equal(await page.getByText("sources.pi.url", { exact: true }).count(), 0);
  await page.goBack();
  await page.getByRole("heading", { name: "Application configuration", exact: true }).waitFor();
  await page.getByRole("link", { name: "Assistant limits", exact: true }).click();
  assert.equal(await page.getByRole("textbox", { name: "Filter settings" }).inputValue(), "assistant.");
  await page.getByText("assistant.timeout_seconds", { exact: true }).waitFor();
  assert.deepEqual(mutations, []);
});

test("reader Settings and Sources do not request protected configuration or connection details", async () => {
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true, user: signedInUser } }));
  const administrative = [];
  page.on("request", (request) => {
    if (["/settings", "/sources/catalog", "/jobs/health"].includes(new URL(request.url()).pathname)) administrative.push(request.url());
  });
  await page.goto(`${url}/#/settings`);
  await page.getByText("Facility configuration requires administrator access.", { exact: false }).waitFor();
  await page.goto(`${url}/#/sources`);
  await page.getByText("Source configuration requires administrator access.", { exact: false }).waitFor();
  assert.deepEqual(administrative, []);
  const nav = page.getByRole("navigation", { name: "Main", exact: true });
  assert.equal(await nav.getByRole("link", { name: "Sources", exact: true }).count(), 0);
  assert.equal(await nav.getByRole("link", { name: "Jobs", exact: true }).count(), 0);
  await page.route("**/demo/okan/investigation", (route) => route.fulfill({ json: investigation }));
  await page.goto(`${url}/#/investigate/okan`);
  await page.getByRole("navigation", { name: "Investigate pages" }).waitFor();
  assert.equal(await page.getByRole("navigation", { name: "Investigate pages" }).getByRole("link", { name: "Ask", exact: true }).count(), 0);
  await page.goto(`${url}/#/settings`);
  assert.equal(await page.getByRole("link", { name: "Users", exact: true }).count(), 0);
});

test("owner administration remains available within Settings secondary navigation", async () => {
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true,
    user: { ...signedInUser, owner: true } } }));
  await page.goto(`${url}/#/settings`);
  const area = page.getByRole("navigation", { name: "Settings pages", exact: true });
  assert.equal(await area.getByRole("link", { name: "Users", exact: true }).getAttribute("href"), "#/users");
});

async function usersFixture(user = { ...signedInUser, owner: true }) {
  const permissions = { read: "Browse evidence", operate: "Operate shared work", configure: "Configure application", ai: "Run paid AI" };
  const roles = [
    { name: "reader", description: "Read evidence", permissions: ["read"] },
    { name: "operator", description: "Review and act", permissions: ["read", "operate"] },
  ];
  const accounts = [
    { ...signedInUser, id: "owner", actor: "user:owner", display_name: "Owner", owner: true },
    { id: "applicant", actor: "user:applicant", display_name: "New participant", enabled: false, owner: false, roles: [] },
  ];
  const people = [{ name: "user:applicant", roles: [], assigned_roles: ["engineer"], owner: false,
    assigned: 0, overdue: 0, away: false, away_until: null, delegate: null }];
  const workflow = [
    { name: "engineer", kind: "person", description: "Human engineering review", assignment: "queue", default_sla: null,
      escalate_to: null, members: [], processes: ["upset"], waiting: 1, overdue: 0, uncovered: true },
    { name: "operator", kind: "person", description: "Human operations", assignment: "queue", default_sla: null,
      escalate_to: null, members: [], processes: [], waiting: 0, overdue: 0, uncovered: false },
  ];
  const writes = [], administration = [];
  await page.route("**/auth/me", route => route.fulfill({ json: { mode: "microsoft", configured: true, user } }));
  await page.route("**/auth/access", route => { administration.push(route.request().url()); return route.fulfill({ json: { accounts, roles, permissions } }); });
  await page.route("**/work/roles/catalog", route => route.fulfill({ json: workflow }));
  await page.route("**/work/people", route => route.fulfill({ json: people }));
  await page.route("**/work/workload", route => route.fulfill({ json: { people, agents: [], agent_failures: [], roles: {} } }));
  await page.route("**/auth/accounts/*", route => {
    const body = route.request().postDataJSON(), id = new URL(route.request().url()).pathname.split("/").at(-1);
    writes.push({ path: `/auth/accounts/${id}`, body });
    if (id === "owner") return route.fulfill({ status: 400, json: { detail: "Keep at least one enabled owner" } });
    Object.assign(accounts.find(a => a.id === id), body);
    people[0].roles = body.enabled ? people[0].assigned_roles : [];
    return route.fulfill({ json: { ok: true } });
  });
  await page.route("**/work/people/*", route => {
    const body = route.request().postDataJSON();
    writes.push({ path: new URL(route.request().url()).pathname, body });
    people[0].assigned_roles = body.roles;
    people[0].roles = accounts[1].enabled ? body.roles : [];
    return route.fulfill({ json: { name: people[0].name, roles: body.roles } });
  });
  await page.route("**/auth/roles/*", route => {
    const body = route.request().postDataJSON(), name = new URL(route.request().url()).pathname.split("/").at(-1);
    writes.push({ path: `/auth/roles/${name}`, body });
    Object.assign(roles.find(r => r.name === name), body);
    return route.fulfill({ json: { ok: true } });
  });
  return { writes, administration };
}

test("Users unifies guided approval, effective access, stable workflow assignment and role definitions", async () => {
  const { writes } = await usersFixture();
  await page.goto(`${url}/#/access`);
  await page.waitForURL(/#\/users$/);
  await page.getByRole("heading", { name: "Users", exact: true }).waitFor();
  const participant = page.locator(".access-account").filter({ has: page.getByRole("heading", { name: /New participant/ }) });
  await participant.getByText("None: account not enabled", { exact: true }).waitFor();
  await participant.getByText("Human workflow assignments (1)", { exact: true }).click();
  await participant.getByText("Assignments are retained", { exact: false }).waitFor();
  assert.equal(await participant.getByRole("checkbox", { name: "engineer", exact: true }).isChecked(), true);
  await participant.getByRole("checkbox", { name: "Enabled", exact: true }).check();
  await participant.getByRole("checkbox", { name: "operator", exact: true }).first().check();
  await participant.getByRole("button", { name: "Save account access", exact: true }).click();
  assert.equal(writes.length, 0);
  await participant.getByRole("button", { name: "Confirm Save account access", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Account access saved for New participant" }).waitFor();
  await participant.getByText("Browse evidence, Operate shared work", { exact: true }).waitFor();
  await participant.getByText("Human workflow assignments (1)", { exact: true }).click();
  await participant.getByRole("checkbox", { name: "operator", exact: true }).check();
  await participant.getByRole("button", { name: "Save workflow assignments", exact: true }).click();
  assert.equal(writes.length, 1);
  await participant.getByRole("button", { name: "Confirm Save workflow assignments", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Workflow assignments saved for New participant" }).waitFor();
  assert.equal(writes[1].path, "/work/people/user%3Aapplicant");
  assert.deepEqual(writes[1].body.roles, ["engineer", "operator"]);
  await page.getByText("Advanced: application role definitions", { exact: false }).click();
  const role = page.locator(".access-role").filter({ has: page.getByRole("heading", { name: "operator", exact: true }) });
  await role.getByRole("checkbox", { name: "Operate shared work", exact: true }).uncheck();
  await role.getByRole("button", { name: "Save application role", exact: true }).click();
  assert.equal(writes.length, 2);
  await role.getByRole("button", { name: "Confirm Save application role", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Application role operator saved" }).waitFor();
  await participant.getByText("Browse evidence", { exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await participant.getByText("Change account access", { exact: true }).click();
  await participant.getByRole("checkbox", { name: "Enabled", exact: true }).uncheck();
  await participant.getByRole("button", { name: "Save account access", exact: true }).press("Enter");
  await participant.getByRole("button", { name: "Cancel", exact: true }).press("Enter");
  assert.equal(writes.length, 3);
  await participant.getByRole("button", { name: "Save account access", exact: true }).click();
  await participant.getByRole("button", { name: "Confirm Save account access", exact: true }).click();
  await participant.getByText("None: account not enabled", { exact: true }).waitFor();
});

test("Users keeps last-owner failures actionable and legacy roster links compatible", async () => {
  const { writes } = await usersFixture();
  await page.goto(`${url}/#/users`);
  const owner = page.locator(".access-account").filter({ has: page.getByRole("heading", { name: "Owner Owner", exact: true }) });
  await owner.getByText("Change account access", { exact: true }).click();
  await owner.getByRole("checkbox", { name: "Owner (all capabilities)", exact: true }).uncheck();
  await owner.getByRole("button", { name: "Save account access", exact: true }).click();
  await owner.getByRole("button", { name: "Confirm Save account access", exact: true }).click();
  await owner.getByRole("alert").filter({ hasText: "Keep at least one enabled owner" }).waitFor();
  assert.equal(writes.length, 1);
  await page.goto(`${url}/#/settings?view=people`);
  await page.waitForURL(/#\/users\?view=workflow$/);
  await page.getByRole("heading", { name: "Workflow assignments & routing", exact: true }).waitFor();
  await page.goto(`${url}/#/teams?role=engineer`);
  await page.getByRole("heading", { name: "Team workload", exact: true }).waitFor();
  assert.equal(await page.getByRole("navigation", { name: "Main", exact: true }).getByRole("link", { name: "Work", exact: true }).getAttribute("aria-current"), "page");
  assert.equal(await page.getByRole("navigation", { name: "Work pages", exact: true }).getByRole("link", { name: "Inbox", exact: true }).getAttribute("aria-current"), "page");
  assert.equal(await page.getByRole("button", { name: "Save workflow assignments", exact: true }).count(), 0);
  assert.match(await page.getByRole("main").getByRole("link", { name: "Users", exact: true }).last().getAttribute("href"), /view=workflow.*role=engineer/);
});

test("Users restricts non-owner administrators to workflow routing and blocks readers without admin requests", async () => {
  const { administration } = await usersFixture({ ...signedInUser, permissions: ["read", "configure"], roles: ["administrator"] });
  await page.goto(`${url}/#/users`);
  await page.getByRole("heading", { name: "Workflow assignments & routing", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Accounts & access", exact: true }).count(), 0);
  assert.deepEqual(administration, []);
  await page.route("**/auth/me", route => route.fulfill({ json: { mode: "microsoft", configured: true, user: signedInUser } }));
  await page.reload();
  await page.getByText("Users administration requires configuration access.", { exact: false }).waitFor();
  assert.deepEqual(administration, []);
  await page.goto(`${url}/#/teams?role=engineer`);
  await page.getByRole("heading", { name: "Team workload", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Save", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "Save workflow assignments", exact: true }).count(), 0);
});

test("Users workflow routing and coverage reuse confirmed actions without granting application access", async () => {
  const { writes } = await usersFixture({ ...signedInUser, roles: ["administrator"], permissions: ["read", "configure"] });
  let routing;
  await page.route("**/work/roles/engineer", route => {
    routing = route.request().postDataJSON();
    return route.fulfill({ json: { ok: true } });
  });
  let away;
  await page.route("**/work/people/user%3Aapplicant/away", route => {
    away = route.request().postDataJSON();
    return route.fulfill({ json: { ok: true } });
  });
  await page.goto(`${url}/#/users?view=workflow&role=engineer`);
  const engineer = page.locator("details.card").filter({ has: page.locator("summary strong").filter({ hasText: /^engineer$/ }) });
  await engineer.getByRole("textbox", { name: "engineer default SLA", exact: true }).fill("PT8H");
  await engineer.getByRole("button", { name: "Save routing for engineer", exact: true }).click();
  assert.equal(routing, undefined);
  await engineer.getByRole("button", { name: "Confirm Save routing for engineer", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Workflow change saved" }).waitFor();
  assert.equal(routing.default_sla, "PT8H");
  const person = page.locator(".access-account");
  await person.getByText("Availability and coverage", { exact: true }).click();
  await person.getByRole("button", { name: "set away", exact: true }).click();
  await person.getByLabel("Away until for user:applicant", { exact: true }).fill("2027-01-01T10:00");
  await person.getByRole("button", { name: "Set away", exact: true }).click();
  assert.equal(away, undefined);
  await person.getByRole("button", { name: "Confirm Set away", exact: true }).click();
  await person.getByRole("button", { name: "set away", exact: true }).waitFor();
  assert.match(away.until, /^2027-01-01T/);
  assert.equal(writes.length, 0, "No account or application-role writes");
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test("Users loading failures recover explicitly and access-refresh errors remain visible", async () => {
  await usersFixture();
  let failed = true, attempts = 0;
  await page.route("**/auth/access", route => {
    attempts++;
    return route.fulfill(failed ? { status: 503, json: { detail: "Access catalog unavailable" } }
      : { json: { accounts: [], roles: [], permissions: {} } });
  });
  await page.goto(`${url}/#/users`);
  await page.getByRole("alert").filter({ hasText: "Access catalog unavailable" }).waitFor();
  assert.equal(attempts, 1);
  failed = false;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await page.getByText("No accounts have signed in yet.", { exact: true }).waitFor();
  await page.route("**/auth/me", route => route.fulfill({ status: 503, json: { detail: "Session check unavailable" } }));
  await page.evaluate(() => window.dispatchEvent(new Event("fuzer:access-changed")));
  await page.getByRole("alert").filter({ hasText: "Access could not be refreshed" }).waitFor();
  await page.route("**/auth/me", route => route.fulfill({ json: { mode: "microsoft", configured: true, user: signedInUser } }));
  await page.getByRole("button", { name: "Reload access", exact: true }).click();
  await page.getByText("Users administration requires configuration access.", { exact: false }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Save account access", exact: true }).count(), 0);
});

test("source load lifecycle confirms consequences, exposes progress and recovers without automatic replay", async () => {
  const detail = {
    dataset: { name: "sap_equipment", source: "sap", status: "active", load_mode: "snapshot", schedule: null },
    connection: { id: 1, name: "files", type: "folder", enabled: true, secrets: {} },
    runs: [], health: null, work_items: [], reviews: [], suggested_checks: [], next_load: null,
  };
  await page.route("**/sources/datasets/sap_equipment", (route) => route.fulfill({ json: detail }));
  let attempts = 0, release;
  await page.route("**/sources/datasets/sap_equipment/run*", async (route) => {
    attempts++;
    if (attempts === 1) {
      await new Promise((resolve) => { release = resolve; });
      await route.fulfill({ status: 503, json: { detail: "Export unavailable" } });
    } else await route.fulfill({ json: { loaded: ["sap_equipment"], held: [], unchanged: [], paused: [],
      records: 2, inserted: 2, updated: 0, removed: 0 } });
  });
  await page.goto(`${url}/#/sources?view=dataset&name=sap_equipment`);
  await page.getByRole("button", { name: "Load now", exact: true }).click();
  await page.getByRole("group", { name: "Confirm Load now", exact: true }).waitFor();
  assert.equal(attempts, 0);
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.equal(await page.getByRole("button", { name: "Load now", exact: true }).evaluate(el => el === document.activeElement), true);
  assert.equal(attempts, 0);
  await page.getByRole("button", { name: "Load now", exact: true }).click();
  await page.getByRole("button", { name: "Confirm Load now", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Loading source records" }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Load now", exact: true }).isDisabled(), true);
  release();
  await page.getByRole("alert").filter({ hasText: "Export unavailable" }).waitFor();
  assert.equal(attempts, 1);
  await page.getByRole("button", { name: "Load now", exact: true }).click();
  await page.getByRole("button", { name: "Confirm Load now", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Loaded 2 records" }).waitFor();
  assert.equal(attempts, 2);
  assert.match(await page.getByRole("link", { name: "Inspect recorded source runs" }).getAttribute("href"), /tab=runs/);
  detail.dataset.status = "paused";
  await page.reload();
  await page.getByText("Before loading, resume the dataset", { exact: false }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Load now", exact: true }).isDisabled(), true);
  detail.dataset.status = "active";
  detail.secrets = { token: false };
  await page.reload();
  await page.getByText("Before loading, resume the dataset", { exact: false }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Load now", exact: true }).isDisabled(), true);
  assert.equal(attempts, 2);
  detail.secrets = {};
  await page.route("**/sources/datasets/sap_equipment/run*", (route) =>
    route.fulfill({ json: { loaded: [], held: ["sap_equipment"], unchanged: [], records: 0 } }));
  await page.reload();
  await page.getByRole("button", { name: "Load now", exact: true }).press("Enter");
  await page.getByRole("button", { name: "Confirm Load now", exact: true }).press("Enter");
  await page.getByRole("status").filter({ hasText: "Load held" }).waitFor();
  assert.equal(await page.getByRole("status").filter({ hasText: "Loaded 2" }).count(), 0);
});

test("review lifecycle confirms shared decisions and exposes failures without marking them done", async () => {
  await page.route("**/review?*", (route) => route.fulfill({ json: [
    { id: 7, kind: "check", payload: { dataset: "sap_equipment", failed: [{ kind: "duplicate keys", value: 2 }] } },
  ] }));
  let attempts = 0;
  await page.route("**/review/7", (route) => {
    attempts++;
    return route.fulfill(attempts === 1 ? { status: 429, json: { detail: "Server busy; check recorded decision" } }
      : { json: { ok: true } });
  });
  await page.goto(`${url}/#/review?kind=check`);
  await page.getByRole("button", { name: "Seen", exact: true }).click();
  await page.getByRole("group", { name: "Confirm review decision" }).waitFor();
  assert.equal(attempts, 0);
  await page.getByRole("button", { name: "Confirm decision", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Server busy" }).waitFor();
  assert.equal(attempts, 1);
  await page.getByRole("button", { name: "Confirm decision", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Saved #7" }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Seen", exact: true }).count(), 0);
  assert.equal(attempts, 2);
});

test("review custom-value confirmation and cancellation do not resubmit the containing form", async () => {
  await page.route("**/review?*", (route) => route.fulfill({ json: [
    { id: 8, kind: "conflict", payload: { fuzer_id: "tag-1", field: "description", chosen: "a",
      values: [{ value: "a", source: "sap", source_key: "1" }] } },
  ] }));
  const writes = [];
  await page.route("**/review/8", (route) => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({ json: { decided: 8 } });
  });
  await page.goto(`${url}/#/review?kind=conflict`);
  await page.getByPlaceholder("or type the correct value").fill("corrected value");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.equal(await page.getByRole("group", { name: "Confirm review decision" }).count(), 0);
  assert.deepEqual(writes, []);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("button", { name: "Confirm decision", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Saved #8" }).waitFor();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].value, "corrected value");
});

test("seven primary destinations expose compatible specialist routes through area navigation", async () => {
  await page.goto(url);
  const nav = page.getByRole("navigation", { name: "Main", exact: true });
  const expected = ["#/", "#/browse", "#/investigate/okan", "#/board", "#/sources", "#/jobs", "#/settings"];
  const links = await nav.locator("a").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href")));
  assert.deepEqual(new Set(links), new Set(expected));
  assert.equal(links.length, new Set(links).size, "One navigation entry per existing surface");
  assert.deepEqual(await nav.locator("a > span:first-child").allTextContents(), ["Home", "Explore", "Investigate", "Work", "Sources", "Jobs", "Settings"]);
  await page.route("**/demo/okan/investigation", (route) => route.fulfill({ json: investigation }));
  await page.goto(`${url}/#/investigate/okan`);
  await nav.locator('a[href="#/investigate/okan"][aria-current="page"]').waitFor();
  const investigate = page.getByRole("navigation", { name: "Investigate pages", exact: true });
  assert.deepEqual(await investigate.locator("a").evaluateAll((nodes) => nodes.map((n) => n.getAttribute("href"))),
    ["#/investigate/okan", "#/ask", "#/downtime", "#/incidents", "#/reports", "#/analytics", "#/lessons", "#/studies"]);
  await page.getByRole("navigation", { name: "Breadcrumb", exact: true }).getByText("Okan investigation", { exact: true }).waitFor();
  await page.goto(`${url}/#/work`);
  await page.waitForURL(/#\/inbox$/);
  await nav.locator('a[href="#/board"][aria-current="page"]').waitFor();
  const work = page.getByRole("navigation", { name: "Work pages", exact: true });
  assert.equal(await work.getByRole("link", { name: "Inbox", exact: true }).getAttribute("aria-current"), "page");
  assert.deepEqual(await work.locator("a").evaluateAll((nodes) => nodes.map((n) => n.getAttribute("href"))),
    ["#/board", "#/inbox", "#/cases", "#/review", "#/processes"]);
  await nav.getByRole("link", { name: "Explore", exact: true }).click();
  await page.getByRole("navigation", { name: "Explore pages", exact: true }).getByRole("link", { name: "Ontology", exact: true }).waitFor();
  await nav.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings pages", exact: true }).getByRole("link", { name: "Docs", exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open menu", exact: true }).click();
  await nav.getByRole("link", { name: "Sources", exact: true }).click();
  await page.waitForURL(/#\/sources$/);
  await page.getByRole("button", { name: "Open menu", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test("fresh Microsoft visitor gets a responsive sign-in entry without preloading protected APIs", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true, user: null } }));
  const protectedRequests = [];
  page.on("request", (request) => {
    if (["/stats", "/jobs", "/assistant/ask", "/demo/okan/investigation"].includes(new URL(request.url()).pathname)) {
      protectedRequests.push(request.url());
    }
  });
  await page.goto(url);
  await page.getByRole("heading", { name: "Investigate with evidence, not guesswork" }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Continue with Microsoft" }).getAttribute("href"), "/auth/login");
  await page.getByText("Personal Microsoft and work/school accounts are supported.", { exact: false }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(protectedRequests, []);
});

test("sign-in setup and session failures have useful retry paths", async () => {
  let configured = false;
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured, user: null } }));
  await page.goto(url);
  await page.getByRole("alert").filter({ hasText: "not been configured" }).waitFor();
  configured = true;
  await page.getByRole("button", { name: "Check again" }).click();
  await page.getByRole("link", { name: "Continue with Microsoft" }).waitFor();
  await page.goto(`${url}/#/?signin=expired`);
  await page.getByRole("alert").filter({ hasText: "Sign-in expired" }).waitFor();
});

test("approved reader completes the keyless journey with trusted identity and no administrative API calls", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "system"));
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true, user: signedInUser } }));
  await page.route("**/stats", (route) => route.fulfill({ json: {
    facility: "OKAN", pack: "okan", records: 100, entities: { tag: 1 }, edges: 1, open_reviews: {}, rebuild: {},
  } }));
  await page.route("**/demo/okan/investigation", (route) => route.fulfill({ json: investigation }));
  const administrative = [];
  page.on("request", (request) => {
    if (/^\/(jobs|sources|settings)(\/|$)/.test(new URL(request.url()).pathname)) administrative.push(request.url());
  });
  await page.goto(url);
  await page.getByRole("link", { name: "Start guided investigation", exact: true }).click();
  await page.getByRole("link", { name: "Next: Meet the asset", exact: true }).click();
  await page.getByRole("link", { name: "Next: Inspect the evidence", exact: true }).click();
  await page.getByRole("link", { name: "Next: Read the finding", exact: true }).click();
  await page.getByText("What the loaded records establish", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Finish investigation", exact: true }).click();
  await page.getByText("Test participant", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "system", exact: true }).count(), 0);
  assert.equal(await page.getByRole("link", { name: "Sources", exact: true }).count(), 0);
  assert.deepEqual(administrative, []);
});

test("pending account can bootstrap only with a setup code and then reach owner-managed access", async () => {
  let owner = false;
  await page.route("**/auth/me", (route) => route.fulfill({ json: {
    mode: "microsoft", configured: true, needs_owner: !owner,
    user: { ...signedInUser, enabled: owner, owner },
  } }));
  await page.route("**/auth/bootstrap", async (route) => {
    assert.equal(route.request().headers()["x-fuzer-csrf"], "browser-csrf-test");
    assert.equal(route.request().postDataJSON().token, "test-setup-code-at-least-thirty-two-characters");
    owner = true;
    await route.fulfill({ json: { ok: true } });
  });
  await page.route("**/auth/access", (route) => route.fulfill({ json: {
    accounts: [{ ...signedInUser, owner: true }], roles: [{ name: "reader", description: "Read", permissions: ["read"] }],
    permissions: { read: "Browse evidence", ai: "Run paid AI" },
  } }));
  await page.goto(url);
  await page.getByRole("heading", { name: "Your account is ready for approval" }).waitFor();
  await page.getByLabel("Owner setup code").fill("test-setup-code-at-least-thirty-two-characters");
  await page.getByRole("button", { name: "Set up owner access" }).click();
  await page.getByRole("link", { name: "Manage users" }).click();
  await page.getByRole("heading", { name: "Users", exact: true }).waitFor();
  await page.getByText("How Users works:", { exact: false }).click();
  await page.getByText("Keep at least one enabled owner;", { exact: false }).waitFor();
  await page.getByText("Advanced: application role definitions", { exact: false }).click();
  await page.getByRole("heading", { name: "Add an application role", exact: true }).waitFor();
});

test("expired server session exits protected pages and preserves a sign-in recovery path", async () => {
  let expired = false;
  await page.route("**/auth/me", (route) => route.fulfill({ json: {
    mode: "microsoft", configured: true, user: expired ? null : signedInUser,
  } }));
  await page.route("**/demo/okan/investigation", (route) => {
    expired = true;
    return route.fulfill({ status: 401, json: { detail: "Sign in with Microsoft to continue" } });
  });
  await page.goto(`${url}/#/investigate/okan`);
  await page.getByRole("alert").filter({ hasText: "Your session has ended" }).waitFor();
  await page.getByRole("button", { name: "Reload sign-in" }).click();
  await page.getByRole("link", { name: "Continue with Microsoft" }).waitFor();
});

test("Okan journey connects Home, asset, source evidence and a cited finding without writes or AI", async () => {
  const methods = [];
  await page.route("**/stats", (route) => route.fulfill({ json: {
    facility: "OKAN", pack: "okan", records: 100, entities: { tag: 1 }, edges: 1, open_reviews: {}, rebuild: {},
  } }));
  await page.route("**/demo/okan/investigation", (route) => {
    methods.push(route.request().method());
    return route.fulfill({ json: investigation });
  });
  const paid = [];
  page.on("request", (request) => { if (request.url().includes("/assistant/")) paid.push(request.url()); });
  await page.goto(url);
  await page.getByRole("link", { name: "Start guided investigation", exact: true }).click();
  await page.getByRole("heading", { name: "Understand the issue", exact: true }).waitFor();
  await page.getByText("Synthetic demo data", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Next: Meet the asset", exact: true }).click();
  await page.getByRole("heading", { name: "Meet the asset", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.id), "investigation-step");
  await page.getByText("Train B second-stage compressor", { exact: false }).waitFor();
  await page.getByRole("link", { name: "Next: Inspect the evidence", exact: true }).click();
  await page.locator("summary").click();
  await page.getByText("423 Sm³ recorded lost oil", { exact: true }).waitFor();
  await page.getByText("Last observed during ingestion:", { exact: false }).waitFor();
  await page.getByRole("link", { name: "Next: Read the finding", exact: true }).click();
  await page.getByText("What the loaded records establish", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Evidence 1", exact: true }).click();
  assert.equal(await page.locator("details").getAttribute("open"), "");
  await page.reload();
  await page.getByText("423 Sm³ recorded lost oil", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Next: Read the finding", exact: true }).click();
  await page.getByRole("link", { name: "Finish investigation", exact: true }).click();
  await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
  assert.ok(methods.length > 0 && methods.every((method) => method === "GET"));
  assert.deepEqual(paid, []);
});

test("Okan deep links support mobile, keyboard, dark theme and history without overflow", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.route("**/demo/okan/investigation", (route) => route.fulfill({ json: investigation }));
  await page.goto(`${url}/#/investigate/okan?step=evidence&evidence=fz-downtime`);
  const summary = page.locator("summary");
  await summary.waitFor();
  await page.waitForFunction(() => document.activeElement?.tagName === "SUMMARY");
  await page.keyboard.press("Enter");
  assert.equal(await page.locator("details").getAttribute("open"), null);
  await page.keyboard.press("Enter");
  assert.equal(await page.locator("details").getAttribute("open"), "");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "dark");
  await page.getByRole("link", { name: "Next: Read the finding", exact: true }).click();
  await page.getByRole("heading", { name: "Read the finding", exact: true }).waitFor();
  await page.goBack();
  await page.getByRole("heading", { name: "Inspect the evidence", exact: true }).waitFor();
});

test("Okan unavailable records and network failures have retry and exit paths", async () => {
  let ready = false;
  await page.route("**/demo/okan/investigation", (route) => ready
    ? route.fulfill({ json: investigation })
    : route.fulfill({ status: 404, json: { detail: "The March Okan records are not loaded." } }));
  await page.goto(`${url}/#/investigate/okan`);
  await page.getByRole("alert").filter({ hasText: "not loaded" }).waitFor();
  await page.getByRole("link", { name: "Return home", exact: true }).waitFor();
  ready = true;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await page.getByRole("heading", { name: "Understand the issue", exact: true }).waitFor();
});

test("Okan loading and transport errors keep recovery and navigation usable", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route("**/demo/okan/investigation", async (route) => {
    await held;
    await route.abort("failed");
  });
  await page.goto(`${url}/#/investigate/okan`);
  await page.getByRole("status").filter({ hasText: "Loading" }).waitFor();
  await page.getByRole("heading", { name: "Okan investigation", exact: true }).waitFor();
  release();
  await page.getByRole("alert").waitFor();
  await page.getByRole("button", { name: "Try again", exact: true }).waitFor();
  await page.getByRole("link", { name: "Return home", exact: true }).click();
  await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
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
    const overflow = query.get("ref") === "overflow-plant";
    const points = Array.from({ length: 14 }, (_, i) => ({
      date: new Date(Date.parse(end) - (13 - i) * 86_400_000).toISOString().slice(0, 10),
      dynamic_max_sm3d: i === 5 ? null : 100 + i,
      allocated_sm3d: i === 5 ? null : i === 6 ? 132.5 : 80 + i,
      gap_sm3d: i === 5 ? null : i === 6 ? -26.5 : 20,
      efficiency_pct: i === 5 || (overflow && i === 6) ? null : i === 6 ? 125 : 80,
      active_constraint_id: i === 5 || i === 6 ? null : "export",
      binding_constraint_ids: i === 5 || i === 6 ? [] : i === 3 ? ["export", "compressor"] : ["export"],
      capacity_basis: i === 5 ? "missing" : i === 6 ? "reported_potential" : "declared_limits",
      flags: i === 5 ? ["missing_day"] : overflow && i === 6 ? ["derived_metric_overflow"] : [], source_ids: ["report-1"],
    }));
    return route.fulfill({ json: {
      since: points[0].date, until: new Date(Date.parse(end) + 86_400_000).toISOString().slice(0, 10),
      end, step: "day", unit: "Sm3/d", facility: { id: query.get("ref") || "plant", label: "Test plant" },
      source: "Daily production reports", points,
      constraints: [{ id: "export", label: "Export", category: "export" }, { id: "compressor", label: "Compressor", category: "equipment" }],
      summary: { efficiency_pct: 80, allocated_sm3: overflow ? null : 1040, dynamic_max_sm3: overflow ? null : 1300, paired_days: 13 },
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
  await page.getByLabel("Facility ref (optional)").fill("overflow-plant");
  await page.getByRole("button", { name: "Update", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll(".analytics-summary strong").length === 3 &&
    document.querySelector(".analytics-summary").textContent.includes("Unknown"));
  assert.equal(await page.locator(".analytics-summary strong").filter({ hasText: "Unknown" }).count(), 2);
  await page.getByRole("cell", { name: /reported potential · derived_metric_overflow/ }).waitFor();
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
