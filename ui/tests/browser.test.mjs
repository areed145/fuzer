import { before, beforeEach, after, afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { readFileSync } from "node:fs";

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
    "/demo/config": { enabled: true },
    "/assistant/agents": { agents: [], unavailable: [] },
    "/work/items": { items: [], total: 0, overdue: 0, due_soon: 0 },
    "/board": { cards: [], total: 0, page: 1, per_page: 50 },
    "/incidents": { items: [], total: 0 },
    "/related": { subjects: [], incidents: [], studies: [], lessons: [] },
    "/downtime": { lost_oil_sm3: 0, events: 0, hours: 0, items: [] },
    "/jobs/health": { available: false, level: "unknown", summary: "" },
    "/jobs": { available: false, dagster: { url: "", reachable: false },
      scheduler: { running: false, last_heartbeat: null }, jobs: [], sensors: [], checks: [],
      health: { summary: "No jobs configured" }, claude: null },
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

async function waveScreenshot(name) {
  if (!process.env.WAVE_SCREENSHOTS) return;
  await mkdir(process.env.WAVE_SCREENSHOTS, { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${process.env.WAVE_SCREENSHOTS}/${name}.png`, fullPage: true });
}

test("Wave 1 Downtime rankings fit mobile and recover a failed ranking read", async () => {
  let failed = true;
  await page.route("**/downtime?*", route => route.fulfill({ json: { lost_oil_sm3: 423, events: 1, hours: 2, items: [] } }));
  await page.route("**/downtime/ranking?*", route => {
    const by = new URL(route.request().url()).searchParams.get("by");
    return route.fulfill(by === "reason" && failed
      ? { status: 503, json: { detail: "Reason ranking unavailable" } }
      : { json: [{ label: `Very-long-${by}-equipment-reference-without-spaces-0123456789`,
        description: "Compressor equipment with a long descriptive name", fuzer_id: "fz-compressor", lost_oil_sm3: 423 }] });
  });

  await page.goto(`${url}/#/downtime`);
  const reason = page.locator("section").filter({ has: page.getByRole("heading", { name: "By reason", exact: true }) }).last();
  await reason.getByRole("alert").filter({ hasText: "Reason ranking unavailable" }).waitFor();
  failed = false;
  await reason.getByRole("button", { name: "Try again", exact: true }).click();
  await reason.getByRole("link").waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    for (const name of ["By equipment", "By equipment class", "By reason"]) {
      const heading = page.getByRole("heading", { name, exact: true });
      await heading.scrollIntoViewIfNeeded();
      const box = await heading.boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= width);
    }
    await page.getByText("Oil lost (Sm³)", { exact: true }).first().waitFor();
    const bar = await page.locator(".downtime-rankings .ui-bar").first().boundingBox();
    assert.ok(bar && bar.width > 0 && bar.height > 0, "Ranking bars must remain visible without loading a graph page");
    await waveScreenshot(`downtime-${width}`);
  }
});

  test("Wave 1 sign-in distinguishes approval from enabled accounts lacking application access", async () => {
    let user = { ...signedInUser, enabled: false };
    const protectedRequests = [];
    await page.route("**/auth/me", route => route.fulfill({ json: { mode: "microsoft", configured: true, user } }));
    page.on("request", request => {
      if (["/stats", "/work/inbox", "/auth/access"].includes(new URL(request.url()).pathname)) protectedRequests.push(request.url());
    });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`${url}/#/browse?q=pump`);
    await page.getByRole("heading", { name: "Your account is ready for approval", exact: true }).waitFor();
    await page.getByRole("list", { name: "Access next steps" }).waitFor();
    await waveScreenshot("sign-in-approval-1440");
    await page.setViewportSize({ width: 390, height: 900 });
    await waveScreenshot("sign-in-approval-390");
    user = { ...signedInUser, enabled: true, permissions: [], roles: [] };
    await page.getByRole("button", { name: "Check approval", exact: true }).click();
    await page.getByRole("heading", { name: "Your account needs application access", exact: true }).waitFor();
    await page.getByRole("button", { name: "Check access", exact: true }).waitFor();
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await waveScreenshot(`sign-in-no-access-${width}`);
    }
    assert.deepEqual(protectedRequests, []);
  });

  test("Wave 1 Users account filters compose and restore from URL without writes", async () => {
    const { writes } = await usersFixture();
    await page.goto(`${url}/#/users?status=not-enabled&q=participant`);
    await page.getByRole("searchbox", { name: "Find accounts", exact: true }).waitFor();
    assert.equal(await page.locator(".access-account").count(), 1);
    await page.getByText("1 of 2 accounts", { exact: true }).waitFor();
    await page.getByRole("searchbox", { name: "Find accounts", exact: true }).fill("missing");
    await page.getByText("No accounts match", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Clear account filters", exact: true }).click();
    await page.getByText("2 of 2 accounts", { exact: true }).waitFor();
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.evaluate(() => window.scrollTo(0, 0));
      // The account list and its search start in the first viewport; the guidance above it stays folded.
      const search = await page.getByRole("searchbox", { name: "Find accounts", exact: true }).boundingBox();
      assert.ok(search && search.y + search.height < 900, "Account search should be in the first viewport");
      await waveScreenshot(`users-all-accounts-${width}`);
    }
    await page.getByLabel("Account status filter", { exact: true }).selectOption("owners");
    await page.reload();
    await page.getByText("Owner recovery", { exact: true }).waitFor();
    assert.equal(await page.locator(".access-account").count(), 1);
    assert.deepEqual(writes, []);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await waveScreenshot(`users-accounts-${width}`);
    }
  });

  test("Wave 1 workflow people separate saved assignments from effective roles and expose recovery", async () => {
    await usersFixture({ ...signedInUser, roles: ["administrator"], permissions: ["read", "configure"] });
    const people = [{ name: "user:applicant", display_name: "New participant", roles: [], assigned_roles: ["engineer"],
      account_enabled: false, owner: false, assigned: 2, overdue: 1, away: false, away_until: null, delegate: null }];
    await page.route("**/work/workload", route => route.fulfill({ json: { people, agents: [], agent_failures: [], roles: {} } }));
    await page.route("**/work/people/user%3Aapplicant", route => route.fulfill({ status: 503, json: { detail: "Assignment save unavailable" } }));
    await page.goto(`${url}/#/users?view=workflow&person=user%3Aapplicant`);
    const card = page.locator(".access-account");
    await card.getByText("Saved workflow assignments: engineer", { exact: true }).waitFor();
    await card.getByText("Effective workflow roles: None (account disabled)", { exact: true }).waitFor();
    await page.getByRole("navigation", { name: "Workflow administration sections" }).getByRole("link", { name: "People", exact: true }).click();
    await card.getByRole("checkbox", { name: "operator", exact: true }).check();
    await card.getByRole("button", { name: "Save workflow assignments", exact: true }).click();
    await card.getByRole("button", { name: "Confirm Save workflow assignments", exact: true }).click();
    await card.getByRole("alert").filter({ hasText: "Assignment save unavailable" }).waitFor();
    await card.getByRole("button", { name: "Reload saved assignments", exact: true }).click();
    await card.getByRole("checkbox", { name: "operator", exact: true }).waitFor();
    assert.equal(await card.getByRole("checkbox", { name: "operator", exact: true }).isChecked(), false);
    assert.equal(await page.getByRole("status").filter({ hasText: "Workflow change saved" }).count(), 0,
      "Inspecting recorded assignments must not report a successful save");
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await waveScreenshot(`users-workflow-${width}`);
    }
  });

test("Wave 1 disabled owners retain status but have no effective workflow roles on any surface", async () => {
  await usersFixture();
  const account = { id: "disabled-owner", actor: "user:disabled-owner", display_name: "Disabled owner",
    owner: true, enabled: false, roles: [] };
  const people = [{ name: account.actor, display_name: account.display_name, owner: true, account_enabled: false,
    roles: [], assigned_roles: ["engineer"], assigned: 1, overdue: 0, away: false, away_until: null, delegate: null }];
  await page.route("**/auth/access", route => route.fulfill({ json: { accounts: [account], roles: [], permissions: { read: "Browse evidence" } } }));
  await page.route("**/work/workload", route => route.fulfill({ json: { people, agents: [], agent_failures: [], roles: {} } }));
  await page.goto(`${url}/#/users`);
  await page.getByText("Workflow access: none while this account is disabled.", { exact: false }).waitFor();
  await page.goto(`${url}/#/users?view=workflow`);
  const card = page.locator(".access-account");
  await card.getByText("Effective workflow roles: None (account disabled)", { exact: true }).waitFor();
  assert.equal(await card.getByText("All human roles from owner status;", { exact: false }).count(), 0);
  await page.goto(`${url}/#/teams`);
  const row = page.getByRole("row").filter({ has: page.getByRole("cell", { name: "Disabled owner", exact: true }) });
  await row.getByRole("cell", { name: "None (account disabled)", exact: true }).waitFor();
});

test("Wave 1 account search updates the URL without adding per-character Back steps", async () => {
  await usersFixture();
  await page.goto(`${url}/#/exceptions`);
  await page.getByRole("heading", { name: "All work", exact: true }).waitFor();
  await page.evaluate(() => { location.hash = "#/users"; });
  const search = page.getByRole("searchbox", { name: "Find accounts", exact: true });
  await search.waitFor();
  const length = await page.evaluate(() => history.length);
  await search.pressSequentially("participant");
  assert.equal(await page.evaluate(() => history.length), length);
  await page.reload();
  assert.equal(await search.inputValue(), "participant");
  await page.goBack();
  await page.getByRole("heading", { name: "All work", exact: true }).waitFor();
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

test("Core consistency Home separates historical reports, unavailable counts and scheduler history", async () => {
  let failed = true, attempts = 0;
  await page.route("**/stats", route => route.fulfill({ status: 503, json: { detail: "Statistics unavailable" } }));
  await page.route("**/reports/daily", route => {
    attempts++;
    return route.fulfill(failed ? { status: 503, json: { detail: "Reports unavailable" } }
      : { json: [{ day: "2024-03-14" }] });
  });
  await page.route("**/reports/daily/2024-03-14", route => route.fulfill({ json: {
    facts: { sections: { Headline: [{ text: "Historical report fact", cites: [] }] } }, checks: { ok: true },
  } }));
  await page.goto(url);
  const report = page.locator("section.home-card").filter({ has: page.getByRole("heading", { name: "Latest daily report", exact: true }) });
  await report.getByRole("alert").waitFor();
  assert.equal(attempts, 1);
  assert.equal(await page.getByRole("link", { name: "0 to review", exact: true }).count(), 0);
  await page.getByText("Scheduler history unavailable.", { exact: false }).waitFor();
  assert.equal(await page.getByText("No failed runs or checks.", { exact: true }).count(), 0);
  failed = false;
  await report.getByRole("button", { name: "Try again", exact: true }).click();
  await report.getByText("Historical report fact", { exact: true }).waitFor();
  await report.getByText("Report date", { exact: false }).waitFor();
  assert.equal(attempts, 2);
  assert.equal(await page.getByRole("heading", { name: "Today", exact: true }).count(), 0);
});

test("a report waiting on an agent no worker has claimed says the agent worker may not be running (#393)", async () => {
  const report = (unclaimed, minutesAgo) => ({ day: "2025-12-30", version: 1, versions: [1], drafted_by: "template", markdown: "# Report",
    checks: { ok: true, uncited_lines: [], unknown_citations: [], unknown_numbers: [] }, seconds: null, changes: null,
    production_day: null, facts: { sections: {} }, review: { item_id: 5, status: "drafting", outcome: null, closed_at: null, comments: null,
      step: { id: 9, element: "draft", name: "Draft report", kind: "agent", assignee: null, role: "reporter", due_at: null, error: null,
        created_at: new Date(Date.now() - minutesAgo * 60_000).toISOString(), unclaimed } } });
  let current = report(true, 90);
  await page.route("**/reports/daily", route => route.fulfill({ json: [{ day: "2025-12-30", status: "drafting" }] }));
  await page.route("**/reports/daily/2025-12-30*", route => route.fulfill({ json: current }));
  await page.goto(`${url}/#/reports?day=2025-12-30`);
  const note = page.getByRole("note").filter({ hasText: "Waiting for an agent worker" });
  await note.waitFor();
  assert.match(await note.innerText(), /none has picked this step up in 90 min/);
  assert.equal(await note.getByRole("link", { name: "application settings" }).count(), 1);
  current = report(false, 90);  // a worker holds it: just drafting
  await page.reload();
  await page.getByText("the reporter agent is drafting it", { exact: false }).waitFor();
  assert.equal(await page.getByRole("note").filter({ hasText: "Waiting for an agent worker" }).count(), 0);
});

test("a case's process diagram opens on the waiting step at a readable size, the whole process a click away (#406)", async () => {
  const xml = readFileSync(new URL("../../src/fuzer/workflow/definitions/upset.bpmn", import.meta.url), "utf8");
  const step = { id: 3, process_id: 1, element: "triage", name: "Triage", kind: "user", state: "waiting", flow: null, role: "engineer",
    assignee: null, requires_human: false, due_at: null, escalated_at: null, attempts: 0, error: null,
    created_at: "2026-11-09T09:00:00Z", finished_at: null, finished_by: null };
  await page.route("**/work/items/7", route => route.fulfill({ json: { id: 7, title: "Upset case", type: "upset", state: "open",
    subjects: [], created_by: "agent:sre", created_at: "2026-11-09T09:00:00Z", events: [],
    processes: [{ id: 1, process_key: "upset", version: 1, definition_id: 1, parent_token: null, state: "running", outcome: null,
      data: {}, started_by: "agent:sre", started_at: "2026-11-09T09:00:00Z", path: [], open: [step] }] } }));
  await page.route("**/work/definitions/1/bpmn", route => route.fulfill({ body: xml, contentType: "application/xml" }));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`${url}/#/cases/7`);
  const whole = page.getByRole("button", { name: "Show the whole process", exact: true });
  await whole.waitFor();
  await page.getByText("Showing the waiting step at a readable size.", { exact: false }).waitFor();
  await whole.click();
  await page.getByRole("button", { name: "Zoom to the waiting step", exact: true }).waitFor();
  const gap = await page.locator(".bpmn-legend").evaluate((el) => getComputedStyle(el).columnGap);
  assert.ok(parseFloat(gap) >= 12, `legend entries need space between them, got ${gap}`);
});

test("a case shows what each agent did on it, its versions and cost, against the case's cap (P4.4)", async () => {
  const ledger = { agent: "maintenance-integrity", agents: ["maintenance-integrity"], models: ["claude-test"],
    skills: { "maintenance-integrity/rotating": "3f9c0a1b2c4d" }, cost_usd: 0.42, seconds: 9,
    tools: [{ agent: "maintenance-integrity", tool: "calc", args: { check: "vibration", ref: "20-K-2200B" } }] };
  await page.route("**/work/items/7", route => route.fulfill({ json: { id: 7, title: "Agent case", type: "upset", state: "open",
    subjects: [], created_by: "agent:sre", created_at: "2026-11-09T09:00:00Z", processes: [],
    agent_spend: { usd: 2.1, cap_usd: 2 },
    events: [{ id: 1, kind: "agent_result", actor: "agent:maintenance-integrity", element: "diagnose", at: "2026-11-09T09:05:00Z",
      data: { step: "Diagnose", data: { diagnose: { answer: "Bearing wear on the drive end.", ledger } } } }] } }));
  await page.goto(`${url}/#/cases/7`);
  const card = page.locator(".ui-card, .card").filter({ has: page.getByText("Agent work", { exact: true }) }).first();
  await card.waitFor();
  await card.getByText("$2.10 of $2.00 cap", { exact: false }).waitFor();
  await card.getByText("cap reached", { exact: true }).waitFor();
  await card.getByRole("cell", { name: "claude-test" }).waitFor();
  await card.getByRole("cell", { name: "$0.42" }).waitFor();
  await card.getByRole("button", { name: "What Diagnose did" }).click();
  await card.getByText("Bearing wear on the drive end.", { exact: true }).waitFor();
  await card.getByRole("cell", { name: "calc", exact: true }).waitFor();
  await card.getByText("3f9c0a1b2c4d", { exact: false }).waitFor();
});

test("a case links to Ask about it and shows an answer attached from Ask in its history (P4.5)", async () => {
  await page.route("**/work/items/8", route => route.fulfill({ json: { id: 8, title: "Pump vibration rising", type: "machine_health",
    state: "open", subjects: [], created_by: "detector:machine_health", created_at: "2026-11-09T09:00:00Z", processes: [],
    events: [{ id: 1, kind: "attached", actor: "user:ana", at: "2026-11-09T10:00:00Z",
      data: { answer_id: "a1", conversation_id: "c1", question: "Is it the bearing?", answer: "The 1x peak points at the coupling.", figures: [] } }] } }));
  await page.goto(`${url}/#/cases/8`);
  await page.getByRole("heading", { name: "Pump vibration rising" }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Ask about this case", exact: true }).getAttribute("href"), "#/ask?case=8");
  const entry = page.locator(".ev-attached");
  await entry.getByText("attached an answer from Ask", { exact: false }).waitFor();
  await entry.getByText("Is it the bearing?", { exact: true }).waitFor();
  await entry.getByText("The 1x peak points at the coupling.", { exact: true }).waitFor();
});

test("a case reaches its incidents, studies and lessons (P4.5c)", async () => {
  await page.route("**/work/items/8", route => route.fulfill({ json: { id: 8, title: "Pump vibration rising", type: "machine_health",
    state: "open", subjects: ["fz-p-1"], created_by: "detector:machine_health", created_at: "2026-11-09T09:00:00Z", processes: [], events: [] } }));
  const asked = [];
  await page.route("**/related?*", route => { asked.push(new URL(route.request().url()).search); return route.fulfill({ json: {
    subjects: ["fz-p-1"],
    incidents: [{ id: "fz-incident-1", description: "Trip on high vibration", unit: "P-1", start: "2026-10-01", end: "2026-10-01",
      signals: 1, events: 2, downtime_records: 1, notifications: 0, lost_oil_sm3: 0 }],
    studies: [{ id: 4, kind: "correlate", status: "done", summary: "suction pressure leads vibration", created_by: "user:ana", created_at: "2026-10-02T10:00:00Z" }],
    lessons: [{ id: 3, title: "Check the coupling first", status: "approved", failure_mode: "VIB", scope_kind: "equipment_class", scope_key: "PU" }] } }); });
  await page.goto(`${url}/#/cases/8`);
  const card = page.locator(".related-work");
  await card.getByRole("heading", { name: "Incidents, studies and lessons" }).waitFor();
  assert.deepEqual(asked, ["?case=8"]);
  assert.equal(await card.getByRole("link", { name: "Trip on high vibration" }).getAttribute("href"), "#/e/fz-incident-1");
  assert.equal(await card.getByRole("link", { name: "suction pressure leads vibration" }).getAttribute("href"), "#/studies?id=4");
  assert.equal(await card.getByRole("link", { name: "Check the coupling first" }).getAttribute("href"), "#/lessons?status=approved");
});

test("a case opens with the specialist's brief above the decision, and the person says whether they followed it (P4.3)", async () => {
  const posted = [];
  let verdict = null;
  const item = () => ({ id: 19, title: "Machine health on 20-K-2100C", type: "machine_health", state: "open", subjects: [],
    created_by: "detector:machine_health", created_at: "2026-11-12T03:00:00Z", events: [], processes: [],
    brief: { expected: true, due: "2026-11-13T03:00:00Z", verdict, entry: { pack: "maintenance-integrity/compression",
      ledger: { cost_usd: 0.62 }, budget_usd: 0.5, over_budget: true, late: false, brief: {
        what_happened: "Stage 1 ran within 4 % of its surge line for most of the week.",
        evidence: [{ ref: "tag:20-FT-2101", says: "suction flow fell 12 %" }],
        analysis: [{ method: "surge", says: "5th percentile margin 3.8 %" }],
        similar: [{ case: "INC-2024-07", fixed_by: "anti-surge valve recalibrated" }],
        proposal: { disposition: "Plan an anti-surge valve check", confidence: 0.72, why: "margin under 5 %" },
        could_not_check: ["no OEM map revision date"] } } } });
  await page.route("**/work/items/19", route => route.fulfill({ json: item() }));
  await page.route("**/work/items/19/brief/verdict", route => {
    const body = route.request().postDataJSON();
    posted.push(body);
    verdict = { by: body.by, at: "2026-11-12T04:00:00Z", followed: body.followed, note: body.note ?? null };
    return route.fulfill({ json: verdict });
  });
  await page.goto(`${url}/#/cases/19`);
  await page.evaluate(() => localStorage.setItem("fuzer.reviewer", "pat"));
  await page.reload();
  const card = page.locator(".ui-card, .card").filter({ has: page.getByText("Specialist brief", { exact: true }) }).first();
  await card.waitFor();
  await card.getByText("Stage 1 ran within 4 % of its surge line", { exact: false }).waitFor();
  await card.getByText("Proposed: Plan an anti-surge valve check", { exact: true }).waitFor();
  await card.getByText("72 % confident", { exact: false }).waitFor();
  await card.getByText("no OEM map revision date", { exact: true }).waitFor();
  await card.getByText("$0.62 of $0.50 budget", { exact: false }).waitFor();
  await card.getByText("over budget", { exact: true }).waitFor();
  await card.getByRole("button", { name: "Evidence, analysis and similar cases" }).click();
  await card.getByRole("cell", { name: "anti-surge valve recalibrated" }).waitFor();
  await card.getByLabel("Note on the proposal").fill("checked the valve first");
  await card.getByRole("button", { name: "Partly followed" }).click();
  await card.getByText("Partly followed by", { exact: false }).waitFor();
  assert.equal(posted.length, 1);
  assert.equal(posted[0].followed, "partly");
  assert.equal(posted[0].note, "checked the valve first");
  assert.equal(posted[0].by, "pat");
  await page.evaluate(() => localStorage.removeItem("fuzer.reviewer"));
});

test("a standing condition is shelved with a reason and an expiry, and shows as shelved (P3.5c)", async () => {
  const row = (extra = {}) => ({ id: 5, type: "machine_health", key: "K-1", title: "Seal weeping", source: "screen", state: "open",
    priority: null, value_usd: null, subjects: [], unmatched: [], item_id: null, case_state: null, assignee: null, role: null,
    raised_count: 1, first_seen: "2026-11-01T00:00:00Z", last_seen: "2026-11-01T00:00:00Z", ...extra });
  let rows = [row()];
  const shelved = [];
  await page.route("**/exceptions?*", (route) => route.fulfill({ json: rows }));
  await page.route("**/exceptions/5/shelve", (route) => {
    const body = route.request().postDataJSON();
    shelved.push(body);
    rows = [row({ shelved_until: "2099-01-01T23:59:00Z", shelved_by: body.by, shelved_reason: body.reason })];
    return route.fulfill({ json: rows[0] });
  });
  await page.goto(`${url}/#/exceptions?list=open`);
  await page.evaluate(() => localStorage.setItem("fuzer.reviewer", "pat"));
  await page.reload();
  await page.getByText("Seal weeping").waitFor();
  const shelve = page.getByRole("button", { name: "Shelve", exact: true });
  assert.equal(await shelve.isDisabled(), true);
  await page.getByLabel("Shelve until").fill("2099-01-01");
  await page.getByLabel("Why shelve it").fill("Repair at the next outage");
  await shelve.click();
  await page.getByRole("button", { name: "Back on the list" }).waitFor();
  await page.getByText("Repair at the next outage", { exact: false }).waitFor();
  assert.deepEqual(shelved, [{ by: "pat", reason: "Repair at the next outage", until: "2099-01-01T23:59:00Z" }]);
  await page.evaluate(() => localStorage.removeItem("fuzer.reviewer"));
});

test("Metrics lists every registered KPI with its value and drills by a grain to its records (P7.1b)", async () => {
  const decl = { key: "context.completeness", label: "Asset context completeness", group: "Context", unit: "%",
    definition: "Of what an asset's class usually has, the share it has.", formula: "mean over assets", standard: null,
    grains: ["area", "class"], period: "now", better: "higher", records: "assets", dated: false };
  await page.route("**/metrics", route => route.fulfill({ json: [decl] }));
  await page.route("**/metrics/context.completeness*", route => {
    const grain = new URL(route.request().url()).searchParams.get("grain");
    return route.fulfill({ json: { ...decl, value: 71.4, as_of: null, note: null, grain, refs: ["E-1"],
      rows: grain ? [{ key: "B1", label: "Train B1", value: 64.2, refs: ["E-1", "E-2"] }] : [] } });
  });
  await page.goto(`${url}/#/metrics`);
  await page.getByRole("heading", { name: "Metrics" }).waitFor();
  await page.getByText("71.4%").waitFor();
  await page.getByRole("link", { name: "area" }).click();
  await page.getByRole("heading", { name: "Asset context completeness" }).waitFor();
  await page.getByRole("cell", { name: "Train B1" }).waitFor();
  await page.getByRole("button", { name: "2 records" }).click();
  assert.equal(await page.getByRole("link", { name: "E-2" }).getAttribute("href"), "#/e/E-2");
});

test("All work counts what planned work and shutdowns held back (P3.5b)", async () => {
  await page.route("**/exceptions/suppressed", route => route.fulfill({ json: { findings: 1, raised: 3,
    windows: [{ kind: "shutdown", id: 4, reason: "Train B1 shutdown", start_at: "2026-11-10T00:00:00Z",
      end_at: "2026-11-14T00:00:00Z", created_by: "sap", raised: 3, findings: 1 }],
    items: [{ id: 1, type: "machine_trip", key: "B1@1", title: "Train B1 tripped", source: "trips", subjects: [],
      value_usd: null, window_kind: "shutdown", window_id: 4, reason: "Train B1 shutdown", raised: 3,
      first_seen: "2026-11-11T03:20:00Z", last_seen: "2026-11-11T05:20:00Z" }] } }));
  await page.goto(`${url}/#/exceptions?list=open`);
  await page.getByRole("heading", { name: "Suppressed: 1 finding, raised 3 times" }).waitFor();
  await page.getByText("Train B1 shutdown", { exact: false }).first().waitFor();
  await page.getByRole("button", { name: "What was held back" }).click();
  await page.getByRole("cell", { name: "Train B1 tripped" }).waitFor();
});

test("a case shows the alarms and operator log entries around its event (P3.5a)", async () => {
  await page.route("**/work/items/8", route => route.fulfill({ json: { id: 8, title: "Train B1 tripped", type: "machine_trip",
    state: "open", subjects: [], created_by: "trips", created_at: "2026-11-11T03:20:00Z", events: [], processes: [] } }));
  await page.route("**/work/items/8/around", route => route.fulfill({ json: {
    window: { start: "2026-11-10T23:10", end: "2026-11-11T09:10" },
    alarms: [{ point_id: "fz-timeseries-1", point: "KRGP:B1.VT2003.PV", type: "hihi", level: "1", limit: 11.2, peak: 11.956,
      start: "2026-11-11T02:55", end: "2026-11-11T03:10" }],
    log: [{ id: "fz-log_entry-1", at: "2026-11-11 03:12", author: "Control Room Operator",
      text: "B1-K-2001 tripped on high vibration (B1-VT-2003). Sales gas to zero." }],
    basis: "Alarms on points of the assets this event is on" } }));
  await page.goto(`${url}/#/cases/8`);
  await page.getByRole("heading", { name: "Around the event" }).waitFor();
  await page.getByRole("link", { name: "KRGP:B1.VT2003.PV" }).waitFor();
  await page.getByText("High-high", { exact: true }).waitFor();
  await page.getByRole("link", { name: /tripped on high vibration/ }).waitFor();
});

test("a case says why it stopped for a person, and Settings shows the gate policy (#622)", async () => {
  await page.route("**/work/items/8", route => route.fulfill({ json: { id: 8, title: "Alarm on P-101", type: "false_alarm",
    state: "open", subjects: [], created_by: "agent:sre", created_at: "2026-11-12T06:00:00Z", events: [], processes: [] } }));
  await page.route("**/work/items/8/gates", route => route.fulfill({ json: [
    { id: 1, kind: "auto_close", actor: "agent:sre", confidence: 0.7, threshold: 0.95, criticality: "C", could_not_check: [],
      fired: true, reason: "An automatic close as duplicate or false alarm stopped for a person: its confidence 70% is below the 95% needed on a C-criticality asset.",
      at: "2026-11-12T06:01:00Z" }] }));
  await page.goto(`${url}/#/cases/8`);
  await page.getByRole("heading", { name: "Gates" }).waitFor();
  await page.getByText("Stopped for a person", { exact: true }).waitFor();
  await page.getByText("its confidence 70% is below the 95% needed", { exact: false }).waitFor();

  const set = [];
  let policy = { fixed: [{ kind: "field_dispatch", label: "Dispatching people to the field" }], safety_case_types: ["iow_breach"],
    confidence: [{ kind: "auto_close", label: "An automatic close as duplicate or false alarm", default: 0.95,
      by_criticality: { A: null }, updated_by: null, updated_at: null }] };
  await page.route("**/settings/gates", route => route.fulfill({ json: policy }));
  await page.route("**/settings/gates/auto_close", route => {
    const body = route.request().postDataJSON();
    set.push(body);
    policy = { ...policy, confidence: [{ ...policy.confidence[0], default: body.default, updated_by: body.by }] };
    return route.fulfill({ json: policy.confidence[0] });
  });
  await page.goto(`${url}/#/settings`);
  await page.getByRole("heading", { name: "Human gates" }).waitFor();
  await page.getByText("dispatching people to the field", { exact: false }).waitFor();
  const row = page.getByRole("row").filter({ hasText: "An automatic close" });
  await row.getByText("always a person", { exact: true }).waitFor();
  await row.getByRole("button", { name: "Change" }).click();
  await row.getByLabel(/Goes ahead at/).fill("97");
  await row.getByRole("button", { name: "Save" }).click();
  await row.getByText("97%", { exact: true }).waitFor();
  assert.equal(set[0].default, 0.97);
  assert.deepEqual(set[0].by_criticality, {});
});

test("a write-back waits on the case for a named approver and then for its batch (#636)", async () => {
  await page.route("**/work/items/9", route => route.fulfill({ json: { id: 9, title: "Write back to SAP 10004002", type: "writeback",
    state: "open", subjects: [], created_by: "agent:maintenance", created_at: "2026-11-12T06:00:00Z", events: [], processes: [] } }));
  let row = { id: 21, system: "sap", kind: "notification", record: "10004002", action: "complete", payload: { text: "Gland repacked" },
    state: "awaiting_approval", drafted_by: "agent:maintenance", drafted_at: "2026-11-12T06:00:00Z", approver_role: "maintenance_planner",
    item_id: 9, source_ref: null, approved_by: null, approved_at: null, due_at: null, sent_at: null, confirmed_at: null, error: null,
    reason: null };
  const approvals = [];
  await page.route(/\/writebacks\?/, route => route.fulfill({ json: [row] }));
  await page.route(/\/writebacks\/21$/, route => route.fulfill({ json: { ...row, label: "Notification",
    approvers: ["maintenance_planner", "ops_lead"], cadence: "in the next 15-minute batch", adapter: "export",
    history: [{ id: 1, state: "awaiting_approval", by: "agent:maintenance", note: null, at: "2026-11-12T06:00:00Z" }] } }));
  await page.route(/\/writebacks\/21\/approve$/, route => {
    approvals.push(route.request().postDataJSON());
    row = { ...row, state: "approved", approved_by: "pat", due_at: "2026-11-12T06:15:00Z" };
    return route.fulfill({ json: row });
  });
  await page.goto(`${url}/#/cases/9`);
  await page.evaluate(() => localStorage.setItem("fuzer.reviewer", "pat"));
  await page.reload();
  await page.getByRole("heading", { name: "Notification for SAP 10004002" }).waitFor();
  await page.getByText("waiting for approval", { exact: true }).waitFor();
  await page.getByText("then sent in the next 15-minute batch", { exact: false }).waitFor();
  await page.getByLabel("Change to write").fill("Gland repacked; no leak");
  await page.getByRole("button", { name: "Approve for SAP" }).click();
  await page.getByText("approved, waiting for its batch", { exact: true }).waitFor();
  assert.equal(approvals[0].by, "pat");
  assert.equal(approvals[0].payload.text, "Gland repacked; no leak");
  assert.equal(await page.getByRole("button", { name: "Approve for SAP" }).count(), 0);
  await page.evaluate(() => localStorage.removeItem("fuzer.reviewer"));
});

test("Ask says up front when no AI provider is configured, without calling one (#404)", async () => {
  const posts = [];
  page.on("request", (request) => { if (request.method() !== "GET") posts.push(request.url()); });
  await page.route("**/assistant/agents", route => route.fulfill({ json: { agents: [], unavailable: [], provider: "anthropic", provider_configured: false } }));
  await page.goto(`${url}/#/ask`);
  const note = page.getByRole("note").filter({ hasText: "No AI provider is configured." });
  await note.waitFor();
  assert.equal(await note.getByRole("link", { name: "application settings" }).count(), 1);
  assert.deepEqual(posts, []);
});

test("Ask opened from a case names it, links back and sends it only with the first question (P4.5a)", async () => {
  await page.route("**/assistant/agents", route => route.fulfill({ json: { agents: [], unavailable: [], provider: "anthropic", provider_configured: true } }));
  await page.route("**/assistant/context?*", route => route.fulfill({ json: { item_id: 9, subject: "fz-p-1",
    label: "case 9: Pump vibration rising", text: "This conversation is about case 9" } }));
  const asked = [];
  await page.route("**/assistant/ask", route => {
    asked.push(route.request().postDataJSON());
    return route.fulfill({ json: { job_id: `j${asked.length}`, conversation_id: "c1" } });
  });
  await page.route("**/assistant/jobs/*", route => route.fulfill({ json: { id: "j", conversation_id: "c1", question: "q",
    status: "done", answer: "Looks like the coupling.", steps: [], model_calls: 1, seconds: 1,
    usage: { cost_usd: 0.01, models: {}, model_calls: 1 } } }));
  await page.goto(`${url}/#/ask?case=9`);
  const about = page.getByRole("note").filter({ hasText: "Asking about" });
  await about.waitFor();
  assert.equal(await about.getByRole("link", { name: "case 9: Pump vibration rising" }).getAttribute("href"), "#/cases/9");
  const box = page.getByRole("textbox", { name: "Question for the operations assistant" });
  assert.equal(await box.getAttribute("placeholder"), "Ask about case 9: Pump vibration rising…");
  await box.fill("Is it the bearing?");
  await box.press("Enter");
  await page.getByText("Looks like the coupling.").waitFor();
  // P4.5b: one action puts the answer and its charts on the case
  const attached = [];
  await page.route("**/assistant/answers/*/attach", route => {
    attached.push({ url: route.request().url(), body: route.request().postDataJSON() });
    return route.fulfill({ json: { item_id: 9, figures: [] } });
  });
  await page.getByRole("button", { name: "Attach to case 9", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Attached to case 9" }).waitFor();
  assert.equal(attached.length, 1);
  assert.equal(attached[0].body.item_id, 9);
  assert.match(attached[0].url, /\/assistant\/answers\/j\/attach$/);
  await box.fill("And the seal?");
  await Promise.all([page.waitForRequest("**/assistant/ask"), box.press("Enter")]);
  assert.equal(asked[0].case_id, 9);
  assert.equal(asked[0].question, "Is it the bearing?");
  assert.equal(asked[1].case_id, undefined, "a follow-up has the case in its conversation already");
  assert.equal(asked[1].conversation_id, "c1");
});

test("the Work badge says what it counts and the Cases filters are labelled (#397, #409)", async () => {
  await context.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ana"));
  await page.route("**/work/inbox?*", route => route.fulfill({ json: { person: "ana", roles: ["engineer"], mine: [], queue: [],
    counts: { mine: { total: 1 }, queue: { total: 0 } } } }));
  await page.route("**/stats", route => route.fulfill({ json: { facility: "OKAN", pack: "okan", records: 1, entities: {}, edges: 0,
    open_reviews: { match: 14 }, rebuild: {} } }));
  await page.goto(`${url}/#/cases`);
  const badge = page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: /^Work/ }).locator(".count");
  await page.waitForFunction(() => document.querySelector("nav[aria-label=Main] .count")?.getAttribute("title")
    === "1 workflow step waiting on you, 14 data reviews");
  assert.equal(await badge.locator("[aria-hidden=true]").innerText(), "15");
  // Under All work the shared filter bar labels them (#502).
  await page.getByRole("combobox", { name: "Held by", exact: true }).waitFor();
  assert.equal(await page.getByRole("combobox", { name: "Kind of work", exact: true }).count(), 1);
  assert.equal(await page.getByRole("option", { name: "Anyone", exact: true }).count(), 1);
});

test("Core consistency Sources uses keyboard links, UTC ingestion times and explicit unknown state", async () => {
  await page.route("**/sources/datasets/sap_equipment", route => route.fulfill({ status: 503, json: { detail: "Dataset unavailable" } }));
  await page.route("**/sources/catalog", route => route.fulfill({ json: {
    systems: [{ id: 1, name: "sap", enabled: true, connections: [], datasets: [
      { name: "sap_equipment", record_type: "equipment", present: true, status: "active", health: null,
        last_run: null, rows: null, loaded_at: null },
      { name: "sap_floc", record_type: "floc", present: true, status: "active",
        health: { state: "failed", problems: ["read interrupted"] }, last_run: "failed", rows: 0,
        loaded_at: "2026-11-06T14:00:00+02:00" },
      { name: "legacy_export", record_type: "row", present: true, status: "active", health: null,
        last_run: null, rows: 1, loaded_at: "2024-03-14T03:20:00" },
    ] }], types: { connections: {} },
  } }));
  await page.goto(`${url}/#/sources`);
  const table = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "Last ingested (not event date)", exact: true }) });
  await table.getByText("Health unknown", { exact: true }).first().waitFor();
  await table.getByText("Time unknown", { exact: true }).waitFor();
  await table.getByText("2026-11-06 12:00 UTC", { exact: true }).waitFor();
  await table.getByText("2024-03-14T03:20:00 (timezone not supplied)", { exact: true }).waitFor();
  // the how-to lives in the docs, so the table is on the first screen (#415)
  assert.equal(await page.locator("details, summary").count(), 0);
  assert.equal(await table.getByText("failed", { exact: true }).first().getAttribute("class").then(c => c.includes("ui-badge-danger")), true);
  const dataset = table.getByRole("link", { name: "sap_equipment", exact: true });
  await dataset.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("alert").waitFor();
  await page.getByRole("link", { name: "Return to Sources", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Try again", exact: true }).count(), 1);
});

test("Core consistency asset failure has retry and Explore exit without replacing provenance", async () => {
  let failed = true;
  await page.route("**/entities/asset-consistency/map", route => route.fulfill({ json: { features: [], focus: null } }));
  await page.route("**/entities/asset-consistency", route => route.fulfill(failed
    ? { status: 503, json: { detail: "Record unavailable" } }
    : { json: { fuzer_id: "asset-consistency", kind: "log_entry", label: "Historical evidence",
      golden: {}, values: {}, records: [], keys: [], edges: [], edge_counts: {} } }));
  await page.goto(`${url}/#/e/asset-consistency`);
  await page.getByRole("link", { name: "Return to Explore", exact: true }).waitFor();
  failed = false;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await page.getByRole("heading", { name: "Historical evidence", exact: true }).waitFor();
  assert.match(await page.getByRole("main").innerText(), /No direct source records are linked/);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await page.locator(".entity .grid").evaluate(e => getComputedStyle(e).gridTemplateColumns.split(" ").length), 1);
});

test("Core consistency Jobs preserves metadata units, missing outcomes and actionable failures", async () => {
  let failed = true;
  await page.route("**/jobs", route => route.fulfill({ json: {
    available: true, dagster: { reachable: false, url: "http://scheduler.invalid" },
    scheduler: { running: true, last_heartbeat: null }, health: { level: "ok", summary: "Scheduler observed" },
    jobs: [{ name: "refresh_all", triggers: [], recent_runs: [],
      last_run: { run_id: "consistency", status: "FAILURE", start: null, end: null, seconds: null } }],
    checks: [], sensors: [], claude: null,
  } }));
  await page.route("**/jobs/runs/consistency", route => route.fulfill(failed
    ? { status: 503, json: { detail: "Run unavailable" } }
    : { json: { run_id: "consistency", job: "refresh_all", status: "FAILURE", trigger: "manual", start: null,
      seconds: 0.5, tags: {}, errors: [{ step: "sap_equipment", kind: "failure", message: "Read failed" }],
      assets: [{ asset: "optional_export", time: "2026-11-06T14:00:00+02:00",
        metadata: { missing: true, loaded: false, seconds: 0.5 } }], log_tail: [] } }));
  await page.goto(`${url}/#/jobs`);
  await page.getByRole("link", { name: "Inspect refresh_all last run", exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("link", { name: "Return to Jobs", exact: true }).waitFor();
  failed = false;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await page.getByText("some earlier steps may have completed", { exact: false }).waitFor();
  await page.getByText("Scheduler start: Time unknown", { exact: false }).waitFor();
  await page.getByText("Scheduler observation: 2026-11-06 12:00 UTC", { exact: true }).waitFor();
  assert.equal(await page.locator(".ui-badge-danger").first().evaluate(e => {
    const probe = document.createElement("span");
    probe.style.color = "var(--danger)";
    document.body.append(probe);
    const matches = getComputedStyle(e).color === getComputedStyle(probe).color;
    probe.remove();
    return matches;
  }), true, "Shared badge tone must not be overridden by legacy badge styles");
  assert.equal(await page.getByRole("cell", { name: "0.5", exact: true }).count(), 1);
  assert.equal(await page.getByText("50.0%", { exact: true }).count(), 0);
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (process.env.FUZER_REVIEW_DIR) await page.screenshot({ path: `${process.env.FUZER_REVIEW_DIR}/consistency-jobs-${width}.png`, fullPage: true });
  }
});

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
  await page.goto(`${url}/#/sources`);
  await page.getByRole("heading", { name: "Source readiness", exact: true }).waitFor();
  await page.getByText("Catalog unavailable. Try again.", { exact: false }).waitFor();
  catalogFails = false;
  await page.getByRole("button", { name: "Refresh source readiness" }).click();
  await page.getByText("0 connections", { exact: false }).waitFor();
  assert.match(await page.getByRole("link", { name: "Systems & connections" }).getAttribute("href"), /view=systems/);
  if (process.env.FUZER_REVIEW_DIR) await page.screenshot({ path: `${process.env.FUZER_REVIEW_DIR}/setup-readiness-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (process.env.FUZER_REVIEW_DIR) await page.screenshot({ path: `${process.env.FUZER_REVIEW_DIR}/setup-readiness-mobile.png`, fullPage: true });
  await page.goto(`${url}/#/settings`);
  await page.getByRole("heading", { name: "Application readiness", exact: true }).waitFor();
  await page.getByText("Attention: the owner bootstrap secret", { exact: false }).waitFor();
  assert.equal(await page.getByRole("heading", { name: "Source readiness", exact: true }).count(), 0);
  await page.getByRole("link", { name: "Configure assistant", exact: true }).click();
  await page.getByRole("textbox", { name: "Filter settings" }).waitFor();
  assert.equal(await page.getByRole("textbox", { name: "Filter settings" }).inputValue(), "agents.");
  await page.getByText("agents.api_key", { exact: true }).waitFor();
  assert.equal(await page.getByText("sources.pi.url", { exact: true }).count(), 0);
  await page.goBack();
  await page.getByRole("heading", { name: "Application readiness", exact: true }).waitFor();
  await page.getByRole("link", { name: "Assistant limits", exact: true }).click();
  assert.equal(await page.getByRole("textbox", { name: "Filter settings" }).inputValue(), "assistant.");
  await page.getByText("assistant.timeout_seconds", { exact: true }).waitFor();
  assert.deepEqual(mutations, []);
});

test("Detection under Settings switches a detector and sets a threshold for one equipment class", async () => {
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true,
    user: { ...signedInUser, roles: ["administrator"], permissions: ["read", "configure"] } } }));
  await page.route("**/settings?*", (route) => route.fulfill({ json: setupSettings }));
  await page.route("**/settings", (route) => route.fulfill({ json: setupSettings }));
  const param = { key: "sre.drift_rise", description: "Share of its starting value a drift must climb", type: "float",
    value: 0.05, default: 0.05, from: "default", updated_by: null, updated_at: null, min: 0, max: 1, by_class: true,
    classes: [{ qualifier: "CO", value: 0.3, updated_by: "ana", updated_at: "2026-11-12T00:00:00" }] };
  const detectors = { project: "okan", classes: [{ code: "CO", name: "Compressors", units: 12 }, { code: "PU", name: "Pumps", units: 30 }],
    detectors: [
      { name: "drifts", title: "A slow climb in what wears and fouls", kind_label: "Statistical", cadence_label: "Once a day",
        raises: ["upset"], about: "Vibration climbing.", on: true, switched: null, applicable: true, note: null,
        last_run: { status: "ran", note: null, found: 3, at: "2026-11-12T00:00:00" },
        score: { recall: 0.8, precision: 0.9, false_alarms_per_week: 1, since: "2024-11-01", until: "2024-11-08" },
        eval: "replay:sre:drifts", settings: [param] },
      { name: "hydraulics", title: "A flowline pressure drop still rising", kind_label: "Model", cadence_label: "Once a day",
        raises: ["upset"], about: "", on: true, switched: null, applicable: false, note: "not applicable: missing a well with a choke",
        last_run: null, score: null, eval: "replay:sre:hydraulics", settings: [] },
    ] };
  const writes = [];
  await page.route("**/settings/detectors**", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: detectors });
    writes.push({ path: new URL(route.request().url()).pathname, body: route.request().postDataJSON() });
    return route.fulfill({ json: {} });
  });
  await page.goto(`${url}/#/settings?view=detection`);
  await page.getByText("A slow climb in what wears and fouls", { exact: false }).waitFor();
  await page.getByText("Recall 80%, precision 90%", { exact: false }).waitFor();
  await page.getByText("not applicable: missing a well with a choke", { exact: false }).first().waitFor();
  await page.getByRole("button", { name: "Change" }).first().click();
  await page.getByText("Compressors: 0.3 (ana)", { exact: false }).waitFor();
  await page.getByLabel("Equipment class").selectOption("PU");
  await page.getByLabel("Value", { exact: true }).fill("0.2");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("Give a short reason", { exact: false }).waitFor();
  assert.deepEqual(writes, []);
  await page.getByLabel("Reason for Share of its starting value a drift must climb").fill("pumps foul quickly");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("Applies from the detector's next run", { exact: false }).waitFor();
  assert.equal(writes[0].path, "/settings/detectors/drifts/params/sre.drift_rise");
  assert.equal(writes[0].body.class, "PU");
  assert.equal(writes[0].body.value, "0.2");
  assert.equal(writes[0].body.reason, "pumps foul quickly");
  await page.getByLabel("Reason to switch A slow climb in what wears and fouls").fill("under review");
  await page.getByRole("button", { name: "Switch off" }).click();
  await page.getByText("Switched off from its next run", { exact: false }).waitFor();
  assert.deepEqual(writes[1], { path: "/settings/detectors/drifts/on", body: { on: false, by: writes[1].body.by, reason: "under review" } });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test("Detection shows close-out rates beside each detector and mutes a noisy provider for one class (P3.8b)", async () => {
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true,
    user: { ...signedInUser, roles: ["administrator"], permissions: ["read", "configure"] } } }));
  await page.route("**/settings?*", (route) => route.fulfill({ json: setupSettings }));
  await page.route("**/settings", (route) => route.fulfill({ json: setupSettings }));
  const rates = (cases, closed, real, false_alarm) => ({ cases, closed, real, false_alarm,
    confirmed_rate: closed ? real / closed : null, false_alarm_rate: closed ? false_alarm / closed : null });
  const detectors = { project: "okan", classes: [{ code: "CO", name: "Compressors", units: 12 }],
    trust_levels: { route: "Route directly", triage: "Triage first", mute: "Muted" },
    detectors: [{ name: "trips", title: "A machine that tripped", kind_label: "Rule", cadence_label: "Every 30 minutes",
      raises: ["machine_trip"], about: "", on: true, switched: null, applicable: true, note: null, last_run: null,
      score: null, eval: "test:trips", settings: [], closeouts: rates(10, 8, 6, 2) }],
    providers: [{ system: "healthwatch", name: "healthwatch", label: "HealthWatch predictive health", type: "health_alert",
      enabled: true, closeouts: rates(4, 4, 1, 3),
      trust: [{ class: "", trust: "triage", reason: "new vendor", updated_by: "ana", updated_at: "2026-11-12T00:00:00" }] }] };
  const writes = [];
  await page.route("**/settings/detectors**", (route) => route.fulfill({ json: detectors }));
  await page.route("**/settings/providers/**", (route) => {
    writes.push({ path: new URL(route.request().url()).pathname, body: route.request().postDataJSON() });
    return route.fulfill({ json: {} });
  });
  await page.goto(`${url}/#/settings?view=detection`);
  await page.getByText("Confirmed 75%, false alarm 25%", { exact: false }).waitFor();
  await page.getByText("Confirmed 25%, false alarm 75%", { exact: false }).waitFor();
  await page.getByText("Triage first", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Change" }).nth(1).click();
  await page.getByText('"new vendor"', { exact: false }).waitFor();
  await page.getByLabel("Equipment class").selectOption("CO");
  await page.getByRole("combobox", { name: "Trust" }).selectOption("mute");
  await page.getByLabel("Reason to change trust in HealthWatch predictive health").fill("three false alarms on compressors");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("Applies to its next alerts", { exact: false }).waitFor();
  assert.deepEqual(writes, [{ path: "/settings/providers/healthwatch/healthwatch/trust",
    body: { trust: "mute", class: "CO", by: writes[0].body.by, reason: "three false alarms on compressors" } }]);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
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
  await page.route("**/demo/investigation", (route) => route.fulfill({ json: investigation }));
  await page.goto(`${url}/#/investigate/okan`);
  await nav.locator('a[href="#/demo"][aria-current="page"]').waitFor();
  await nav.getByRole("link", { name: "Investigate", exact: true }).click();
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
  await page.getByRole("button", { name: "Application role definitions" }).click();
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
  assert.equal(await page.getByRole("navigation", { name: "Work pages", exact: true }).getByRole("link", { name: "My work", exact: true }).getAttribute("aria-current"), "page");
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
  const engineer = page.locator(".ui-disclosure.card").filter({ has: page.locator(".ui-disclosure-toggle strong").filter({ hasText: /^engineer$/ }) });
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

test("a dataset shows its four quality measures with a trend, and marks the one that dropped (P2.6)", async () => {
  const m = (key, label, now, best, trend, dropped = false) => ({ key, label, now, best, dropped, trend });
  await page.route("**/sources/datasets/sap_equipment", (route) => route.fulfill({ json: {
    dataset: { name: "sap_equipment", source: "sap", status: "active", load_mode: "snapshot", schedule: null },
    connection: { id: 1, name: "files", type: "folder", enabled: true, secrets: {} },
    runs: [], health: null, work_items: [{ id: 7, title: "sap_equipment: completeness fell from 97% to 41%", state: "open",
      assignee: "bo", role: null, created_at: "2026-11-12T05:00:00Z" }], reviews: [], suggested_checks: [], next_load: null,
    scorecard: { drop: 15, window_days: 30, at: "2026-11-12T05:00:00Z", measures: [
      m("completeness", "Completeness", 41.2, 97.4, [97.4, 97.1, 41.2], true), m("validity", "Validity", 100, 100, [100, 100, 100]),
      m("timeliness", "Timeliness", null, null, [null, null, null]), m("agreement", "Agreement", 98.5, 99, [99, 98.5, 98.5])] } } }));
  await page.goto(`${url}/#/sources?view=dataset&name=sap_equipment`);
  const card = page.getByRole("region", { name: "Quality scorecard", exact: true });
  await card.getByText("41%", { exact: true }).waitFor();
  await card.getByText("dropped", { exact: true }).waitFor();
  await card.getByText("97 · 97 · 41 (best 97%)", { exact: false }).waitFor();
  await card.getByText("can't be said yet", { exact: true }).waitFor();
  assert.equal(await card.getByText("dropped", { exact: true }).count(), 1);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflows at ${width}`);
    await card.scrollIntoViewIfNeeded();
    await waveScreenshot(`scorecard-${width}`);
  }
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

test("a drawing disagreement records which side needs changing and says neither was changed (#475)", async () => {
  await page.route("**/review?*", (route) => route.fulfill({ json: [{ id: 11, kind: "pid_mismatch", label: "20-PT-2201B", payload: {
    check: "placement", sheet: "OKAN-20-PID-005", document: "fz-document-1", tag: "20-PT-2201B", fuzer_id: "fz-instrument-1",
    point: null, drawn_on: "20-K-2200B", register: [{ parent: "20-V-2201B", sources: ["sap"] }],
    detail: "the drawing shows it on 20-K-2200B; sap has it under 20-V-2201B",
    question: "OKAN-20-PID-005 shows 20-PT-2201B on 20-K-2200B, but sap has it under 20-V-2201B. Which is right, the drawing or the register?",
    finding: finding("agentic", "A drawing read by a model, compared with the register", [
      { label: "Drawing", value: "OKAN-20-PID-005", kind: "text" }, { label: "Tag", value: "20-PT-2201B", kind: "text" },
      { label: "Drawn on", value: "20-K-2200B", kind: "text" }, { label: "Register", value: "sap has it under 20-V-2201B", kind: "text" }]) } }] }));
  let sent;
  await page.route("**/review/11", (route) => { sent = route.request().postDataJSON(); return route.fulfill({ json: { decided: 11 } }); });
  await page.goto(`${url}/#/review?kind=pid_mismatch`);
  await page.getByRole("heading", { name: /Instrument drawn on another item/ }).waitFor();
  assert.match(await page.locator(".review .finding").innerText(), /Found by\s+Agent\s+Method\s+A drawing read by a model[\s\S]*Drawn on\s+20-K-2200B/);
  assert.equal(await page.getByRole("link", { name: "Open OKAN-20-PID-005" }).count(), 1);
  await page.getByRole("button", { name: "Drawing is right, change SAP" }).click();
  await page.getByRole("button", { name: "Confirm decision", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Nothing was changed in either system" }).waitFor();
  assert.equal(sent.decision, "choose");
  assert.equal(sent.value, "drawing");
});

test("a match review spells out its reasons and where its score sits against the thresholds (#410)", async () => {
  const side = (ref, source, m) => ({ ref, source, key: null, block: "76-HCV-1003", fields: { description: "Control valve", ...(m ? { manufacturer: m } : {}) } });
  await page.route("**/review?*", (route) => route.fulfill({ json: [{ id: 9, kind: "match", payload: {
    left: side("sap:equipment:1", "sap", "Fisher"), right: side("maximo:asset:2", "maximo"), score: 0.7, review_min: 0.6, auto_accept: 0.9,
    why: "same tag 76-HCV-1003; description similarity 1.00; manufacturer unknown on at least one side, counted as neutral (0.50); the score weighs manufacturer 60%, description 40%" } }] }));
  await page.goto(`${url}/#/review?kind=match`);
  await page.getByText("Same tag 76-HCV-1003; description similarity 1.00", { exact: false }).waitFor();
  await page.getByText("scores from 0.60 to 0.90 come here for a person to decide", { exact: false }).waitFor();
  await page.getByLabel("Match score 0.70 of 1").waitFor();
});

test("an event grouped on two units says which unit each incident is on and links the other (#402)", async () => {
  const inc = (id, unit) => ({ id, description: "Train C trip", unit, start: "2024-11-05", end: "2024-11-05", signals: 2, events: 1,
    downtime_records: 1, notifications: 0, lost_oil_sm3: 300 });
  await page.route("**/incidents?*", (route) => route.fulfill({ json: { total: 2,
    items: [inc("fz-incident-000000000001", "20-V-2001"), inc("fz-incident-000000000002", "20-V-2101C")] } }));
  await page.route("**/feedback/briefs", (route) => route.fulfill({ json: { incidents: 0, rated: 0, ratings: 0, items: [] } }));
  await page.goto(`${url}/#/incidents`);
  await page.getByText("on 20-V-2001", { exact: true }).waitFor();
  const first = page.locator(".timeline li").first();
  await first.getByText("also on", { exact: false }).waitFor();
  assert.equal(await first.getByRole("link", { name: "20-V-2101C", exact: true }).getAttribute("href"), "#/e/fz-incident-000000000002");
});

test("the map fits the field's wells and platforms, not the export line, and says when base tiles fail (#398)", async () => {
  const pt = (id, kind, lon, lat) => ({ type: "Feature", id, geometry: { type: "Point", coordinates: [lon, lat] }, properties: { kind, label: id } });
  await page.route("**/map", (route) => route.fulfill({ json: { type: "FeatureCollection", features: [
    pt("W1", "well", 5.000, 4.000), pt("W2", "well", 5.010, 4.010), pt("P1", "area", 5.005, 4.005),
    { type: "Feature", id: "L1", geometry: { type: "LineString", coordinates: [[5.005, 4.005], [6.5, 5.5]] },
      properties: { kind: "pipeline", service: "oil export", label: "Export" } }] } }));
  const zooms = [];
  await page.route("https://tile.openstreetmap.org/**", (route) => {
    zooms.push(Number(new URL(route.request().url()).pathname.split("/")[1]));
    return route.fulfill({ status: 503, body: "" });
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`${url}/#/map`);
  await page.getByText("Base map unavailable", { exact: false }).waitFor();
  // Fitted to points about 1 km apart the tiles come at a close zoom; fitting the 200 km export line would ask for about 8.
  assert.ok(Math.max(...zooms) >= 12, `expected a close fit to the field, tiles came at zoom ${[...new Set(zooms)]}`);
});

test("Add a source offers a connection for a subfolder when its folder's tables are all loaded (#412)", async () => {
  const system = { id: 1, name: "contractor", kind: null, description: null, owner: "ana", steward: null, timezone: null, encoding: null,
    decimal: null, date_format: null, rank: null, enabled: true, notes: null, datasets: [],
    connections: [{ id: 4, name: "files", type: "folder", config: {}, display: "data folder" }] };
  await page.route("**/sources/catalog", (route) => route.fulfill({ json: { systems: [system],
    types: { connections: { folder: { config: { path: "" }, secrets: [] } }, load_modes: [], drift_policies: [], statuses: [] },
    check_kinds: {}, data_dir: "/data", rebuild: { built_at: null, needed: false, changes: [] } } }));
  await page.route("**/sources/connections/4/test", (route) => route.fulfill({ json: { ok: true, steps: [] } }));
  await page.route("**/sources/connections/4/tables", (route) => route.fulfill({ json: { connection: "files", system: "contractor",
    type: "folder", tables: [{ table: "equipment", dataset: "contractor_equipment" }],
    subfolders: [{ name: "incoming", path: "incoming", tables: 6 }] } }));
  await page.goto(`${url}/#/sources?view=add&system=contractor&connection=4`);
  await page.getByText("Only tables directly in its folder are read.", { exact: false }).waitFor();
  await page.getByRole("button", { name: "Add a connection for incoming/ (6 tables)" }).click();
  await page.getByRole("heading", { name: /Where is the data\?/ }).waitFor();
  assert.equal(await page.getByPlaceholder("name (default folder)").inputValue(), "incoming");
  assert.equal(await page.locator("input[value='incoming']").count() >= 2, true);  // the name and the path
});

test("a system says how its records matched, what is left for a steward and every automatic match (P2.1)", async () => {
  const system = { id: 1, name: "inspect", kind: null, description: null, owner: "ana", steward: null, timezone: null, encoding: null,
    decimal: null, date_format: null, rank: null, enabled: true, notes: null, datasets: ["inspections"], connections: [] };
  await page.route("**/sources/catalog", (route) => route.fulfill({ json: { systems: [system],
    types: { connections: {}, load_modes: [], drift_policies: [], statuses: [] },
    check_kinds: {}, data_dir: "/data", rebuild: { built_at: null, needed: false, changes: [] } } }));
  await page.route("**/sources/systems/inspect/workflows", (route) => route.fulfill({ json: { types: [], rules: [],
    webhook: { path: "/webhooks/inspect", secret: null, ready: false } } }));
  await page.route("**/sources/systems/inspect/context", (route) => route.fulfill({ json: {
    source: "inspect", records: 61, built_at: "2026-11-12T04:00:00Z", expects_match: ["tag"], needs_steward: 2, share: 0.0328, target: 0.1,
    decided: { key: 58, score: 1, pinned: 0, new: 2 }, links: { pct: 100, resolved: 1, total: 1, by_rel: {},
      listed: [{ src: "fz-tag-1", rel: "located_at", dst: "fz-area-1", src_label: "20-P-1001A", dst_label: "Train A", dst_kind: "area",
        confidence: 1, with_others: false, basis: "named by its key" }], unlinked: [] },
    steward: { to_confirm: 1, disagree: 0, unresolved: 0, unmatched: 1 },
    automatic: [{ ref: "inspect:inspection:20-p-1001a", source_key: "20-p-1001a", fuzer_id: "fz-tag-1", label: "20-P-1001A", kind: "tag",
      method: "key", confidence: 1, basis: "same key as the register", with: ["pi_af", "sap"] },
      { ref: "inspect:inspection:20 K 2100C", source_key: "20 K 2100C", fuzer_id: "fz-tag-2", label: "20-K-2100C", kind: "tag",
        method: "score", confidence: 0.993, basis: "matched on its values", with: ["sap"] }],
    unmatched: [{ source_key: "99-X-9999", fuzer_id: "fz-tag-9", label: "99-X-9999", kind: "tag" }] } }));
  await page.goto(`${url}/#/sources?view=systems`);
  await page.getByRole("button", { name: /inspect/ }).first().click();
  await page.getByText("61 records: 59 matched on their own, 2 new.", { exact: false }).waitFor();
  await page.getByText("2 need a steward (3%)", { exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "1 match to confirm", exact: true }).getAttribute("href"), "#/masterdata?tab=questions&kind=match");
  assert.equal(await page.getByRole("link", { name: "99-X-9999", exact: true }).getAttribute("href"), "#/e/fz-tag-9");
  let accepted = null;
  await page.route("**/sources/systems/inspect/context/new", (route) => {
    accepted = route.request().postDataJSON();
    return route.fulfill({ json: { accepted: 1 } });
  });
  await page.getByRole("button", { name: "They are new items", exact: true }).click();
  await page.getByText("The record stays as new items", { exact: false }).waitFor();
  await page.getByRole("button", { name: "Confirm They are new items", exact: true }).click();
  await page.getByRole("button", { name: "Automatic matches (59)", exact: true }).click();
  await page.getByRole("cell", { name: "99%", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "20-K-2100C", exact: true }).getAttribute("href"), "#/e/fz-tag-2");
  assert.equal(accepted?.reason, "new items, not ones the register has");
  let unlinked = null;
  await page.route("**/sources/systems/inspect/links/unlink", (route) => {
    unlinked = route.request().postDataJSON();
    return route.fulfill({ json: { unlinked: {} } });
  });
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Links it states (1)", exact: true }).click();
  await page.getByRole("button", { name: "Not this link", exact: true }).click();
  for (let i = 0; i < 50 && !unlinked; i++) await page.waitForTimeout(50);
  assert.deepEqual([unlinked?.src, unlinked?.rel, unlinked?.dst], ["fz-tag-1", "located_at", "fz-area-1"]);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflows at ${width}`);
    await waveScreenshot(`system-context-${width}`);
  }
});

test("installing a source pack says what it added on Sources, Routing and Detection, linked there (P1.5)", async () => {
  await page.route("**/sources/catalog", (route) => route.fulfill({ json: { systems: [],
    types: { connections: {}, load_modes: [], drift_policies: [], statuses: [] },
    check_kinds: {}, data_dir: "/data", rebuild: { built_at: null, needed: false, changes: [] } } }));
  let posted = null;
  await page.route("**/sources/packs", (route) => {
    posted = route.request().postDataJSON();
    return route.fulfill({ json: { system: ["system measurement"], connections: ["export"],
      datasets: ["measurement_meters", "measurement_daily_volumes_with_a_very_long_name"],
      mappings: ["measurement/meter"], checks: ["measurement_meters: not_null meter_id"],
      exception_types: ["short_gas_day"], exception_rules: ["short day"], detectors: ["source_health"],
      unchanged: ["mapping measurement/day"] } });
  });
  await page.goto(`${url}/#/sources?view=systems`);
  await page.getByRole("button", { name: "Import a source pack…" }).click();
  await page.getByPlaceholder("fuzer_source_pack: 1 …").fill("fuzer_source_pack: 1\nsystem: {name: measurement}\n");
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await page.getByRole("heading", { name: "On Detection" }).waitFor();
  assert.equal(posted.yaml.startsWith("fuzer_source_pack: 1"), true);
  for (const [name, target] of [["measurement_meters", "#/sources?view=dataset&name=measurement_meters"],
    ["short_gas_day", "#/routing?type=short_gas_day"], ["source_health", "#/settings?view=detection"]]) {
    assert.equal(await page.getByRole("link", { name, exact: true }).getAttribute("href"), target);
  }
  await page.getByRole("button", { name: "1 already in place" }).waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflows at ${width}`);
    await waveScreenshot(`pack-installed-${width}`);
  }
});

test("Rules shows each source's match thresholds with the precision measured at them, and adopts them (P2.2)", async () => {
  const row = (source, inUse, recommended, at) => ({ kind: "equipment", source, labels: "answer key and steward decisions",
    measured_at: "2026-11-12T02:00:00Z", measured_by: "ana", pairs: 5, same: 3, ambiguous: 0, target: 0.99,
    recommended, in_use: inUse, at_auto_accept: at });
  let rules = { ranks: [{ name: "sap", rank: 10, enabled: true }], rules: [],
    calibration: [row("maximo", { auto_accept: 0.9, review_min: 0.6 }, { auto_accept: 1.0, review_min: 0.6 },
      { threshold: 0.92, precision: 0.75, lower: 0.3, pairs: 4, recall: 1 })] };
  let adopted = 0;
  await page.route("**/sources/rules", (route) => route.fulfill({ json: rules }));
  await page.route("**/sources/calibration/adopt", (route) => {
    adopted++;
    rules = { ...rules, calibration: [row("maximo", { auto_accept: 1.0, review_min: 0.6 }, { auto_accept: 1.0, review_min: 0.6 },
      { threshold: 1.0, precision: 1, lower: 0.44, pairs: 3, recall: 0.67 })] };
    return route.fulfill({ json: { maximo: { auto_accept: 1.0, review_min: 0.6 } } });
  });
  await page.goto(`${url}/#/sources?view=rules`);
  const card = page.locator(".match-confidence");
  await card.getByText("75% correct of 4 pairs").waitFor();
  await card.getByText("measured: 1.00").waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflows at ${width}`);
    await waveScreenshot(`match-confidence-${width}`);
  }
  await card.getByRole("button", { name: /Use the measured thresholds for maximo/ }).click();
  await card.getByText("100% correct of 3 pairs").waitFor();
  assert.equal(adopted, 1);
  assert.equal(await card.getByRole("button", { name: /Use the measured thresholds/ }).count(), 0);
});

test("Data review is the Questions tab of Master data, old links land on the same kind, and a case opens its question (P2.3)", async () => {
  await page.route("**/review?*", (route) => {
    const kind = new URL(route.request().url()).searchParams.get("kind");
    return route.fulfill({ json: kind === "conflict" ? [
      { id: 4, kind: "conflict", label: "20-P-1001A", description: "Export pump", payload: { fuzer_id: "fz-1", field: "manufacturer",
        chosen: "Flowserve", values: [{ value: "Flowserve", source: "sap", source_key: "1" }, { value: "FLOWSERVE", source: "maximo", source_key: "MX-1" }] } },
      { id: 9, kind: "conflict", label: "20-K-2200B", description: "Compressor", payload: { fuzer_id: "fz-2", field: "model",
        chosen: "JGK/4", values: [{ value: "JGK/4", source: "sap", source_key: "2" }, { value: "JGK4", source: "maximo", source_key: "MX-2" }] } },
    ] : [] });
  });
  await page.goto(`${url}/#/review?kind=conflict`);
  await page.waitForURL(/#\/masterdata\?tab=questions&kind=conflict/);
  await page.getByRole("heading", { level: 1, name: "Master data" }).waitFor();
  assert.equal(await page.getByRole("tab", { name: /Questions/ }).getAttribute("aria-selected"), "true");
  await page.getByText("20-P-1001A").first().waitFor();
  // Opened from its case: that question comes first.
  await page.goto(`${url}/#/masterdata?tab=questions&kind=conflict&item=9`);
  await page.getByText("20-K-2200B").first().waitFor();
  const first = await page.locator(".review").first().innerText();
  assert.match(first, /20-K-2200B/);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflows at ${width}`);
    await waveScreenshot(`masterdata-questions-${width}`);
  }
});

test("ten like answers become a rule a steward accepts, and it answers the rest (P6.5)", async () => {
  await context.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ana"));
  const rule = { source: "ptw", kind: "sap:equipment", prefix: "eq", suffix: "",
    rule: "A PTW value that is a SAP equipment key with 'EQ' before names that item" };
  let learned = { proposals: [{ ...rule, taught: 10, would_answer: 2, rows: 2,
    examples: [{ id: 1, value: "EQ-30000003", kind: "tag", key: "20-K-2100B" }],
    values: [{ value: "EQ-30000011", rows: 1, rel: "covers", fuzer_id: "fz-1", key: "20-P-2101A" },
             { value: "EQ-30000019", rows: 1, rel: "covers", fuzer_id: "fz-2", key: "20-V-2001" }] }], rules: [] };
  let sent = null;
  await page.route("**/stewardship/unresolved", (route) => route.fulfill({ json: [
    { source: "ptw", rel: "covers", value: "EQ-30000011", rows: 1, aliased: false, built_at: "2026-11-12T00:00:00Z" }] }));
  await page.route("**/stewardship/learned", (route) => route.fulfill({ json: learned }));
  await page.route("**/stewardship/learned/accept", (route) => {
    sent = route.request().postDataJSON();
    learned = { proposals: [], rules: [{ ...rule, id: 4, status: "accepted", decided_by: "ana", decided_at: "2026-11-12T08:00:00Z",
      reason: null, answers: 2 }] };
    return route.fulfill({ json: { recorded: 2 } });
  });
  await page.goto(`${url}/#/masterdata?tab=unresolved`);
  await page.getByText("A PTW value that is a SAP equipment key with 'EQ' before names that item.").waitFor();
  await page.getByRole("button", { name: "It would answer 2 more values on 2 rows" }).click();
  assert.equal(await page.getByRole("link", { name: "20-V-2001" }).getAttribute("href"), "#/e/fz-2");
  await page.getByRole("button", { name: "Accept rule" }).click();
  await page.getByRole("button", { name: "Confirm Accept rule" }).click();
  await page.getByRole("cell", { name: rule.rule }).waitFor();
  assert.deepEqual(sent, { source: "ptw", kind: "sap:equipment", prefix: "eq", suffix: "", by: "ana", reason: "10 like answers" });
});

test("like questions are answered once, for new ones too when asked (P2.3c)", async () => {
  await context.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ana"));
  let groups = [{ kind: "match", key: "match|jde+maximo|the maker is unknown on one side", count: 6, items: [1, 2, 3, 4, 5, 6],
    label: "jde and maximo records at the same tag where the maker is unknown on one side", standing: null,
    options: [{ decision: "accept", label: "The same item, each pair" }, { decision: "reject", label: "Keep each pair apart" }] }];
  let sent = null;
  await page.route("**/review/groups?*", (route) => route.fulfill({ json: groups }));
  await page.route("**/review/groups", (route) => {
    sent = route.request().postDataJSON();
    groups = [];
    return route.fulfill({ json: { decided: [1, 2, 3, 4, 5], skipped: [{ id: 6, why: "already pinned the other way" }], rebuild: null } });
  });
  page.on("dialog", (d) => d.accept());
  await page.goto(`${url}/#/masterdata?tab=questions&kind=match`);
  const card = page.locator(".like-questions");
  await card.getByText("6 × jde and maximo records at the same tag where the maker is unknown on one side").waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflows at ${width}`);
    await waveScreenshot(`like-questions-${width}`);
  }
  await card.getByLabel("and new ones like these").check();
  await card.getByRole("button", { name: "Keep each pair apart" }).click();
  await page.getByText("Answered 5 of 6; 1 left for you (already pinned the other way).").waitFor();
  assert.deepEqual(sent, { key: "match|jde+maximo|the maker is unknown on one side", decision: "reject", standing: true, by: "ana" });
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

test("a question that came back says why, and can be sent for a field check (P2.4)", async () => {
  await page.route("**/review?*", (route) => route.fulfill({ json: [
    { id: 9, kind: "conflict", label: "20-P-1001A", description: null, payload: { fuzer_id: "tag-1", field: "manufacturer",
      chosen: "Flowserve", values: [{ value: "Flowserve", source: "sap", source_key: "1" }, { value: "Sulzer", source: "jde", source_key: "2" }],
      returned: { kind: "master_data", system: "jde", why: "JDE's owner rejected the correction: the nameplate says Sulzer. Look at it again." } } },
  ] }));
  const writes = [];
  await page.route("**/review/9", (route) => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({ json: { decided: 9 } });
  });
  await page.goto(`${url}/#/masterdata?tab=questions&kind=conflict`);
  await page.getByText("JDE's owner rejected the correction: the nameplate says Sulzer.").waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflows at ${width}`);
    await waveScreenshot(`question-returned-${width}`);
  }
  await page.getByRole("button", { name: "Check it in the field", exact: true }).click();
  await page.getByRole("button", { name: "Confirm decision", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "Saved #9" }).waitFor();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].decision, "field");
});

test("an asset says what is known, what is missing and what each gap holds back; Master data ranks the gaps (P2.5)", async () => {
  await page.route("**/entities/tag-e5002/map", (route) => route.fulfill({ json: { features: [], focus: null } }));
  await page.route("**/entities/tag-e5002", (route) => route.fulfill({ json: { fuzer_id: "tag-e5002", kind: "tag",
    label: "E1-E-5002", golden: { description: { value: "Propane Chiller" } }, values: {}, records: [], keys: [], edges: [], edge_counts: {} } }));
  await page.route("**/entities/tag-e5002/context", (route) => route.fulfill({ json: {
    fuzer_id: "tag-e5002", label: "E1-E-5002", description: "Propane Chiller", class_name: "Heat exchangers", area: "Train E1",
    criticality: null, score: 0.571, blocked: ["excursions", "iow_breaches"], of: null, item_id: 41,
    known: [{ check: "class", label: "Equipment class", has: true, expected: true },
      { check: "limits", label: "Limits", has: false, expected: true },
      { check: "documents", label: "Documents", has: false, expected: false }],
    gaps: [{ check: "limits", label: "Limits", missing: "no alarm limits, IOWs or normal ranges", prevents: "excursion and IOW cases",
      usual: "89% of heat exchangers have it", detectors: [{ name: "excursions", title: "Process excursions" }, { name: "iow_breaches", title: "IOW breaches" }] }],
    suggestions: [{ target: "doc-pc", target_label: "Propane chiller datasheet", rel: "applies_to", confidence: 0.4,
      kinds: ["class_guide"], basis: ["it applies to its equipment class"] }] } }));
  const answers = [];
  await page.route("**/entities/tag-e5002/placement", (route) => { answers.push(route.request().postDataJSON()); return route.fulfill({ json: { accepted: true } }); });
  await page.goto(`${url}/#/e/tag-e5002`);
  const panel = page.locator(".asset-context");
  await panel.getByText("Holds back Process excursions; IOW breaches; excursion and IOW cases. 89% of heat exchangers have it.").waitFor();
  await panel.getByText("57% of what heat exchangers usually have").waitFor();
  assert.equal(await panel.getByText("Missing", { exact: true }).count(), 1);
  assert.equal(await panel.getByText("Not recorded", { exact: true }).count(), 1);
  assert.equal(await panel.getByRole("link", { name: "Open case", exact: true }).getAttribute("href"), "#/cases/41");
  // a ranked suggestion for the gap, with its evidence; the steward's answer is posted (P2.1d)
  await panel.getByText("it applies to its equipment class").waitFor();
  await panel.getByRole("button", { name: "Yes", exact: true }).click();
  for (let i = 0; i < 50 && !answers.length; i++) await page.waitForTimeout(50);
  assert.deepEqual(answers[0] && { rel: answers[0].rel, target: answers[0].target, accept: answers[0].accept },
    { rel: "applies_to", target: "doc-pc", accept: true });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `asset overflows at ${width}`);
    await panel.scrollIntoViewIfNeeded();
    await waveScreenshot(`asset-context-${width}`);
  }
  await page.route("**/stewardship/context", (route) => route.fulfill({ json: {
    checks: [{ check: "limits", label: "Limits" }, { check: "measured", label: "Historian points" }],
    top: [{ fuzer_id: "tag-e5002", label: "E1-E-5002", description: "Propane Chiller", class_name: "Heat exchangers", area: "Train E1",
      criticality: null, score: 0.571, missing: ["measured", "limits"], blocked: ["excursions", "iow_breaches", "drifts"], value: 3, item_id: 41 }],
    areas: [{ name: "Train E1", assets: 6, blocked: 2, score: 0.842, gaps: [{ check: "limits", label: "Limits", assets: 2 }] }],
    classes: [{ name: "Wells", assets: 2, blocked: 0, score: null, gaps: [] }] } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${url}/#/masterdata?tab=context`);
  const top = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "Checks held back", exact: true }) });
  await top.getByText("historian points, limits", { exact: true }).waitFor();
  await top.getByText("Propane Chiller · Heat exchangers · criticality not assessed", { exact: true }).waitFor();
  assert.equal(await top.getByRole("link", { name: "Open", exact: true }).getAttribute("href"), "#/cases/41");
  await page.getByText("limits (2)", { exact: true }).waitFor();
  await page.getByText("too few to say", { exact: true }).waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `master data overflows at ${width}`);
    await waveScreenshot(`context-tab-${width}`);
  }
});

test("optional Demo preserves existing primary destinations and specialist routes", async () => {
  await page.goto(url);
  const nav = page.getByRole("navigation", { name: "Main", exact: true });
  await nav.getByRole("link", { name: "Demo", exact: true }).waitFor();
  const expected = ["#/", "#/demo", "#/browse", "#/downtime", "#/exceptions", "#/sources", "#/jobs", "#/settings"];
  const links = await nav.locator("a").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href")));
  assert.deepEqual(new Set(links), new Set(expected));
  assert.equal(links.length, new Set(links).size, "One navigation entry per existing surface");
  assert.deepEqual(await nav.locator("a > span:first-child").allTextContents(), ["Home", "Demo", "Explore", "Investigate", "Work", "Sources", "Jobs", "Settings"]);
  await page.route("**/demo/investigation", (route) => route.fulfill({ json: investigation }));
  await page.goto(`${url}/#/investigate/okan`);
  await nav.locator('a[href="#/demo"][aria-current="page"]').waitFor();
  // Demo is one entry (#556): the tour starts the walkthrough, which keeps its URL.
  assert.equal(await page.getByRole("navigation", { name: "Demo pages", exact: true }).count(), 0);
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  await breadcrumb.getByText("Guided investigation", { exact: true }).waitFor();
  assert.equal(await breadcrumb.getByText("Guided investigation", { exact: true }).count(), 1);
  await nav.getByRole("link", { name: "Investigate", exact: true }).click();
  const investigate = page.getByRole("navigation", { name: "Investigate pages", exact: true });
  await investigate.waitFor();
  assert.deepEqual(await investigate.locator("a").evaluateAll((nodes) => nodes.map((n) => n.getAttribute("href"))),
    ["#/downtime", "#/ask", "#/incidents", "#/reports", "#/metrics", "#/lessons", "#/studies"]);
  await page.goto(`${url}/#/work`);
  await page.waitForURL(/#\/inbox$/);
  await nav.locator('a[href="#/exceptions"][aria-current="page"]').waitFor();
  const work = page.getByRole("navigation", { name: "Work pages", exact: true });
  assert.equal(await work.getByRole("link", { name: "My work", exact: true }).getAttribute("aria-current"), "page");
  assert.deepEqual(await work.locator("a").evaluateAll((nodes) => nodes.map((n) => n.getAttribute("href"))),
    ["#/exceptions", "#/inbox", "#/plan", "#/masterdata"]);
  // The pages that left Work's nav still open, under the entry that owns them now (#554).
  for (const [route, owner, area] of [["#/handover", "My work", "Work pages"], ["#/review", "Master data", "Work pages"],
    ["#/processes", "Processes", "Settings pages"]]) {
    await page.goto(`${url}/${route}`);
    await page.getByRole("navigation", { name: area, exact: true }).locator(`a[aria-current="page"]`, { hasText: owner }).waitFor();
  }
  await nav.getByRole("link", { name: "Explore", exact: true }).click();
  await page.getByRole("navigation", { name: "Explore pages", exact: true }).getByRole("link", { name: "Ontology", exact: true }).waitFor();
  await nav.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("navigation", { name: "Settings pages", exact: true }).getByRole("link", { name: "Docs", exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open menu", exact: true }).click();
  await nav.getByRole("link", { name: "Sources", exact: true }).click();
  await page.waitForURL(/#\/sources$/);
  const menu = page.getByRole("button", { name: "Open menu", exact: true });
  await menu.waitFor();
  await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Open menu");
  await page.waitForFunction(() =>
    document.querySelector('#sidebar nav[aria-label="Main"] a[href="#/sources"][aria-current="page"]'));
  await page.getByRole("heading", { name: "Sources", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test("primary destination navigation keeps page headings and active context aligned", async () => {
  await page.route("**/demo/investigation", (route) => route.fulfill({ json: investigation }));
  await page.goto(url);
  const nav = page.getByRole("navigation", { name: "Main", exact: true });
  await nav.locator("a").first().waitFor();
  const destinations = [
    { label: "Home", href: "#/", heading: "Home" },
    { label: "Demo", href: "#/demo", heading: "Demo" },
    { label: "Explore", href: "#/browse", heading: "Browse", area: "Explore pages", current: "Browse" },
    { label: "Investigate", href: "#/downtime", heading: "Downtime", area: "Investigate pages", current: "Downtime" },
    { label: "Work", href: "#/exceptions", heading: "All work", area: "Work pages", current: "All work" },
    { label: "Sources", href: "#/sources", heading: "Sources" },
    { label: "Jobs", href: "#/jobs", heading: "Jobs" },
    { label: "Settings", href: "#/settings", heading: "Settings", area: "Settings pages", current: "Application settings" },
  ];
  await nav.getByRole("link", { name: "Explore", exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("heading", { level: 1, name: "Browse", exact: true }).waitFor();
  await page.waitForFunction(() =>
    document.querySelector('#sidebar nav[aria-label="Main"] a[aria-current="page"]')?.getAttribute("href") === "#/browse");
  assert.equal(await page.evaluate(() => document.activeElement?.tagName), "H1");
  assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), "Browse");
  for (const destination of destinations) {
    await page.goto(`${url}/${destination.href}`);
    await page.waitForFunction((href) =>
      document.querySelector('#sidebar nav[aria-label="Main"] a[aria-current="page"]')?.getAttribute("href") === href,
    destination.href);
    await page.getByRole("heading", { level: 1, name: destination.heading, exact: true }).waitFor();
    assert.equal(await page.getByRole("heading", { level: 1 }).count(), 1);
    if (destination.area) {
      const area = page.getByRole("navigation", { name: destination.area, exact: true });
      await area.getByRole("link", { name: destination.current, exact: true }).waitFor();
      assert.equal(await area.locator('a[aria-current="page"]').count(), 1);
      assert.equal(await area.getByRole("link", { name: destination.current, exact: true }).getAttribute("aria-current"), "page");
    }
  }
});

test("fresh Microsoft visitor gets a responsive sign-in entry without preloading protected APIs", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true, user: null } }));
  const protectedRequests = [];
  page.on("request", (request) => {
    if (["/stats", "/jobs", "/assistant/ask", "/demo/investigation"].includes(new URL(request.url()).pathname)) {
      protectedRequests.push(request.url());
    }
  });
  await page.goto(url);
  await page.getByRole("heading", { name: "Investigate with evidence, not guesswork" }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Continue with Microsoft" }).getAttribute("href"), "/auth/login");
  await page.getByText("Personal and work or school Microsoft accounts work.", { exact: false }).waitFor();
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
  await page.route("**/demo/investigation", (route) => route.fulfill({ json: investigation }));
  const administrative = [];
  page.on("request", (request) => {
    if (/^\/(jobs|sources|settings)(\/|$)/.test(new URL(request.url()).pathname)) administrative.push(request.url());
  });
  await page.goto(url);
  await page.getByRole("navigation", { name: "Main", exact: true }).getByRole("link", { name: "Demo", exact: true }).click();
  await page.getByRole("link", { name: "Next: Trace an issue", exact: true }).click();
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
  await page.getByRole("heading", { name: "Set up the first owner" }).waitFor();
  await page.getByLabel("Owner setup code").fill("test-setup-code-at-least-thirty-two-characters");
  await page.getByRole("button", { name: "Set up owner access" }).click();
  await page.getByRole("link", { name: "Manage users" }).click();
  await page.getByRole("heading", { name: "Users", exact: true }).waitFor();
  await page.getByRole("link", { name: "Help" }).waitFor();  // how Users works is in the docs
  await page.getByRole("button", { name: "Application role definitions" }).click();
  await page.getByRole("heading", { name: "Add an application role", exact: true }).waitFor();
});

test("each sign-in state names what happened and its next action at desktop and mobile without protected reads", async () => {
  const pending = { ...signedInUser, enabled: false, roles: [], permissions: [] };
  const states = [
    { name: "fresh", me: { configured: true, user: null }, heading: "Investigate with evidence, not guesswork", action: ["link", "Continue with Microsoft"] },
    { name: "unconfigured", me: { configured: false, user: null }, heading: "Sign-in is not available yet", action: ["button", "Check again"] },
    { name: "sign-in-failed", me: { configured: true, user: null }, hash: "#/?signin=failed", heading: "Sign-in did not complete", action: ["link", "Continue with Microsoft"] },
    { name: "sign-in-unreachable", me: { configured: true, user: null }, hash: "#/?signin=unavailable", heading: "Microsoft sign-in is unreachable", action: ["link", "Continue with Microsoft"] },
    { name: "check-failed", status: 503, heading: "Sign-in could not be checked", action: ["button", "Reload sign-in"] },
    { name: "pending", me: { configured: true, user: pending }, heading: "Your account is ready for approval", action: ["button", "Check approval"] },
    { name: "first-owner", me: { configured: true, needs_owner: true, user: pending }, heading: "Set up the first owner", action: ["button", "Set up owner access"] },
    { name: "no-read", me: { configured: true, user: { ...pending, enabled: true } }, heading: "Your account needs application access", action: ["button", "Check access"] },
  ];
  let current;
  await page.route("**/auth/me", (route) => current.status
    ? route.fulfill({ status: current.status, json: { detail: "Service unavailable" } })
    : route.fulfill({ json: { mode: "microsoft", ...current.me } }));
  const protectedRequests = [];
  page.on("request", (request) => {
    if (["/stats", "/work/inbox", "/auth/access", "/jobs"].includes(new URL(request.url()).pathname)) protectedRequests.push(request.url());
  });
  for (const state of states) {
    current = state;
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${url}/${state.hash ?? ""}`);
      await page.reload();  // a hash-only goto keeps the page, and its last session check
      await page.getByRole("heading", { level: 1, name: state.heading, exact: true }).waitFor();
      await page.getByRole(state.action[0], { name: state.action[1], exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${state.name} overflows at ${width}`);
      await waveScreenshot(`sign-in-${state.name}-${width}`);
    }
  }
  assert.deepEqual(protectedRequests, []);
});

test("sign-in recovery works from the keyboard, shows its check and moves focus to the new state", async () => {
  let configured = false;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route("**/auth/me", async (route) => {
    if (configured) await held;  // the second check waits until the test has seen it in progress
    await route.fulfill({ json: { mode: "microsoft", configured, user: null } });
  });
  await page.goto(url);
  await page.getByRole("heading", { level: 1, name: "Sign-in is not available yet", exact: true }).waitFor();
  configured = true;
  const check = page.getByRole("button", { name: "Check again", exact: true });
  for (let i = 0; i < 10 && !(await check.evaluate((el) => el === document.activeElement)); i++) await page.keyboard.press("Tab");
  assert.equal(await check.evaluate((el) => el === document.activeElement), true, "Check again is reachable with Tab");
  await page.keyboard.press("Enter");
  const busy = page.getByRole("button", { name: "Checking...", exact: true });
  await busy.waitFor();
  assert.equal(await busy.isDisabled(), true);
  release();
  const heading = page.getByRole("heading", { level: 1, name: "Investigate with evidence, not guesswork", exact: true });
  await heading.waitFor();
  await page.waitForFunction(() => document.activeElement?.tagName === "H1");
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement?.textContent), "Continue with Microsoft");
});

test("the first owner can submit the setup code from the keyboard", async () => {
  let owner = false;
  await page.route("**/auth/me", (route) => route.fulfill({ json: {
    mode: "microsoft", configured: true, needs_owner: !owner, user: { ...signedInUser, enabled: owner, owner },
  } }));
  await page.route("**/auth/bootstrap", async (route) => { owner = true; await route.fulfill({ json: { ok: true } }); });
  await page.route("**/stats", (route) => route.fulfill({ json: {
    facility: "OKAN", pack: "okan", records: 100, entities: { tag: 1 }, edges: 1, open_reviews: {}, rebuild: {},
  } }));
  await page.goto(url);
  await page.getByRole("heading", { level: 1, name: "Set up the first owner", exact: true }).waitFor();
  await page.getByText("nobody can approve accounts", { exact: false }).waitFor();
  await page.getByLabel("Owner setup code").focus();
  await page.keyboard.type("test-setup-code-at-least-thirty-two-characters");
  await page.keyboard.press("Enter");
  await page.getByRole("navigation", { name: "Main", exact: true }).waitFor();
});

test("expired server session exits protected pages and preserves a sign-in recovery path", async () => {
  let expired = false;
  await page.route("**/auth/me", (route) => route.fulfill({ json: {
    mode: "microsoft", configured: true, user: expired ? null : signedInUser,
  } }));
  await page.route("**/demo/investigation", (route) => {
    expired = true;
    return route.fulfill({ status: 401, json: { detail: "Sign in with Microsoft to continue" } });
  });
  await page.goto(`${url}/#/investigate/okan`);
  await page.getByRole("alert").filter({ hasText: "Your session has ended" }).waitFor();
  await page.getByRole("heading", { level: 1, name: "Your session has ended", exact: true }).waitFor();
  await page.waitForFunction(() => document.activeElement?.tagName === "H1");  // focus follows the ended session
  await page.getByRole("button", { name: "Reload sign-in" }).click();
  await page.getByRole("link", { name: "Continue with Microsoft" }).waitFor();
});

test("Okan journey connects Demo, asset, source evidence and a cited finding without writes or AI", async () => {
  const methods = [];
  await page.route("**/stats", (route) => route.fulfill({ json: {
    facility: "OKAN", pack: "okan", records: 100, entities: { tag: 1 }, edges: 1, open_reviews: {}, rebuild: {},
  } }));
  await page.route("**/demo/investigation", (route) => {
    methods.push(route.request().method());
    return route.fulfill({ json: investigation });
  });
  const paid = [];
  page.on("request", (request) => { if (request.url().includes("/assistant/") && request.method() !== "GET") paid.push(request.url()); });
  await page.goto(url);
  assert.equal(await page.getByRole("link", { name: "Start guided investigation", exact: true }).count(), 0);
  await page.getByRole("navigation", { name: "Main", exact: true }).getByRole("link", { name: "Demo", exact: true }).click();
  await page.getByRole("link", { name: "Next: Trace an issue", exact: true }).click();
  await page.getByRole("link", { name: "Start guided investigation", exact: true }).click();
  await page.getByRole("heading", { name: "Understand the issue", exact: true }).waitFor();
  await page.getByText("Synthetic demo data", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Next: Meet the asset", exact: true }).click();
  await page.getByRole("heading", { name: "Meet the asset", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.id), "investigation-step");
  await page.getByText("Train B second-stage compressor", { exact: false }).waitFor();
  await page.getByRole("link", { name: "Next: Inspect the evidence", exact: true }).click();
  await page.getByText("423 Sm³ recorded lost oil", { exact: true }).waitFor();
  await page.getByText("Last observed during ingestion:", { exact: false }).waitFor();
  await page.getByRole("link", { name: "Next: Read the finding", exact: true }).click();
  await page.getByText("What the loaded records establish", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Evidence 1", exact: true }).click();
  assert.equal(await page.locator("article.investigation-evidence").getAttribute("aria-current"), "true");
  await page.reload();
  await page.getByText("423 Sm³ recorded lost oil", { exact: true }).waitFor();
  await page.getByRole("link", { name: "Next: Read the finding", exact: true }).click();
  await page.getByRole("link", { name: "Finish investigation", exact: true }).click();
  await page.getByRole("heading", { name: "Meet the agents", exact: true }).waitFor();
  assert.ok(methods.length > 0 && methods.every((method) => method === "GET"));
  assert.deepEqual(paid, []);
});

test("Okan deep links support mobile, keyboard, dark theme and history without overflow", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.route("**/demo/investigation", (route) => route.fulfill({ json: investigation }));
  await page.goto(`${url}/#/investigate/okan?step=evidence&evidence=fz-downtime`);
  // The linked evidence is shown, not folded, and its heading takes focus.
  await page.locator("article.investigation-evidence h3").waitFor();
  await page.waitForFunction(() => document.activeElement?.closest("article.investigation-evidence") && document.activeElement.tagName === "H3");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "dark");
  await page.getByRole("link", { name: "Next: Read the finding", exact: true }).click();
  await page.getByRole("heading", { name: "Read the finding", exact: true }).waitFor();
  await page.goBack();
  await page.getByRole("heading", { name: "Inspect the evidence", exact: true }).waitFor();
});

test("Okan unavailable records and network failures have retry and exit paths", async () => {
  let ready = false;
  await page.route("**/demo/investigation", (route) => ready
    ? route.fulfill({ json: investigation })
    : route.fulfill({ status: 404, json: { detail: "The March Okan records are not loaded." } }));
  await page.goto(`${url}/#/investigate/okan`);
  await page.getByRole("alert").filter({ hasText: "not loaded" }).waitFor();
  await page.getByRole("link", { name: "Return home", exact: true }).waitFor();
  ready = true;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await page.getByRole("heading", { name: "Understand the issue", exact: true }).waitFor();
});

test("Demo visibility fails explicitly, recovers and follows global refresh without changing tools", async () => {
  let failed = true, enabled = true, reads = 0, investigations = 0;
  await page.route("**/demo/config", route => {
    reads++;
    return route.fulfill(failed ? { status: 429, json: { detail: "Server busy" } } : { json: { enabled } });
  });
  await page.route("**/demo/investigation", route => {
    investigations++;
    return route.fulfill({ json: investigation });
  });
  await page.goto(`${url}/#/demo`);
  await page.getByRole("alert").filter({ hasText: "Server busy" }).waitFor();
  assert.equal(reads, 1, "No automatic replay of rejected reads");
  failed = false;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await page.getByRole("heading", { name: "Get oriented", exact: true }).waitFor();
  enabled = false;
  await page.evaluate(() => window.dispatchEvent(new Event("fuzer:changed")));
  await page.getByText("Demo is disabled", { exact: true }).waitFor();
  const nav = page.getByRole("navigation", { name: "Main", exact: true });
  assert.equal(await nav.getByRole("link", { name: "Demo", exact: true }).count(), 0);
  assert.equal(await nav.getByRole("link", { name: "Investigate", exact: true }).count(), 1);
  await page.goto(`${url}/#/investigate/okan?step=finding`);
  await page.getByText("Demo is disabled", { exact: true }).waitFor();
  assert.equal(investigations, 0);
  enabled = true;
  await page.getByRole("button", { name: "Check again", exact: true }).click();
  await page.getByRole("heading", { name: "Read the finding", exact: true }).waitFor();
});

test("Demo prepares a registered specialist question and confirms paid execution exactly once", async () => {
  await page.route("**/assistant/agents", route => route.fulfill({ json: {
    agents: [{ id: "registered-specialist", label: "Registered specialist", description: "Recorded backlog evidence",
      skills: ["Work readiness"], personas: ["Planner"], limits: ["No stock reservation"], human_decisions: ["People approve schedules"] }],
    unavailable: [{ agent: "registered-specialist", capability: "Transport", needed: "No transport records loaded" }],
  } }));
  const writes = [];
  await page.route("**/assistant/ask", route => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({ status: 429, json: { detail: "Server busy; inspect recorded jobs" } });
  });
  await page.goto(`${url}/#/demo?step=agents`);
  await page.getByText("No transport records loaded", { exact: false }).waitFor();
  await page.getByRole("button", { name: /Registered specialist/ }).click();
  await page.getByRole("link", { name: "Prepare a question for Registered specialist", exact: true }).click();
  await page.getByRole("heading", { name: "Ask", exact: true }).waitFor();
  assert.match(await page.getByRole("textbox", { name: "Question for the operations assistant" }).inputValue(), /Registered specialist/);
  assert.deepEqual(writes, []);
  await page.getByRole("textbox", { name: "Question for the operations assistant" }).press("Enter");
  assert.deepEqual(writes, [], "Enter cannot bypass Demo paid confirmation");
  await page.getByRole("button", { name: "paid question", exact: true }).click();
  await page.getByRole("group", { name: "Confirm paid question", exact: true }).waitFor();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.deepEqual(writes, []);
  await page.getByRole("button", { name: "paid question", exact: true }).click();
  await page.getByRole("button", { name: "Confirm paid question", exact: true }).click();
  await page.getByText("Server busy; inspect recorded jobs", { exact: false }).waitFor();
  assert.equal(writes.length, 1);
  await page.getByRole("link", { name: "Return to Demo", exact: true }).click();
  await page.getByRole("heading", { name: "Meet the agents", exact: true }).waitFor();
});

const demoTask = {
  task: { task_id: 42, element: "review", name: "Review recorded evidence", kind: "user", state: "waiting",
    role: "engineer", assignee: null, requires_human: true, process_id: 9, item_id: 7, process_key: "upset",
    version: 1, definition_id: 1, attempts: 0 },
  item: { id: 7, title: "Loaded shared case", type: "upset", state: "open", subjects: [], created_by: "agent:sre" },
  data: {}, form: null, bulk: [], evidence: { agents: [], messages: [], comments: [], subjects: [] },
};

test("Demo workflow uses loaded shared tasks, confirms changes and exposes ambiguous failure recovery", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ana"));
  await page.route("**/work/items?*", route => route.fulfill({ json: {
    items: [{ ...demoTask.item, created_at: "2024-03-14T12:00:00Z", process_key: "upset" }], total: 1, overdue: 0, due_soon: 0,
  } }));
  await page.route("**/work/inbox?*", route => route.fulfill({ json: {
    person: "ana", roles: ["engineer"], mine: [], queue: [{ ...demoTask.task, title: demoTask.item.title }],
    counts: { mine: { total: 0 }, queue: { total: 1 } },
  } }));
  await page.route("**/work/tasks/42", route => route.fulfill({ json: demoTask }));
  await page.route("**/work/items/7", route => route.fulfill({ json: { ...demoTask.item, processes: [], events: [] } }));
  const writes = [];
  await page.route("**/work/tasks/42/*", route => {
    writes.push({ path: new URL(route.request().url()).pathname, body: route.request().postDataJSON() });
    return route.fulfill({ status: 503, json: { detail: "Task response uncertain" } });
  });
  await page.goto(`${url}/#/demo?step=workflow`);
  await page.getByRole("link", { name: "Loaded shared case", exact: true }).click();
  await page.getByRole("heading", { name: "Loaded shared case", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Reassign", exact: true }).count(), 0);
  await page.getByRole("link", { name: "Return to Demo", exact: true }).click();
  await page.getByRole("link", { name: "Review recorded evidence", exact: true }).click();
  await page.getByRole("heading", { name: "Review recorded evidence", exact: true }).waitFor();
  assert.deepEqual(writes, []);
  await page.getByRole("button", { name: "claim task", exact: true }).click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.deepEqual(writes, []);
  await page.getByRole("button", { name: "Complete", exact: true }).click();
  await page.getByRole("heading", { name: "Review task response", exact: true }).waitFor();
  assert.deepEqual(writes, []);
  await page.getByRole("button", { name: "task completion", exact: true }).click();
  await page.getByRole("button", { name: "Confirm task completion", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Task response uncertain" }).waitFor();
  await page.getByText("Check what was saved before trying again", { exact: false }).waitFor();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].path, "/work/tasks/42/complete");
  await page.getByRole("link", { name: "Return to Demo", exact: true }).click();
  await page.getByRole("heading", { name: "Follow the work", exact: true }).waitFor();
});

test("Demo reader can inspect capabilities and task evidence without paid or workflow controls", async () => {
  await page.route("**/auth/me", route => route.fulfill({ json: { mode: "microsoft", configured: true, user: signedInUser } }));
  await page.route("**/work/tasks/42", route => route.fulfill({ json: demoTask }));
  await page.route("**/work/items/7", route => route.fulfill({ json: { ...demoTask.item, processes: [], events: [] } }));
  const writes = [];
  page.on("request", request => { if (request.method() !== "GET") writes.push(request.url()); });
  await page.goto(`${url}/#/demo?step=agents`);
  await page.getByText("Paid questions require AI access", { exact: false }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Prepare a cross-domain question", exact: true }).count(), 0);
  await page.goto(`${url}/#/inbox/task/42?demo=workflow`);
  await page.getByText("Read-only task inspection.", { exact: false }).waitFor();
  assert.equal(await page.getByRole("button", { name: /claim task|Complete|task handoff/ }).count(), 0);
  assert.deepEqual(writes, []);
});

test("Demo paid success retains registered agent activity, citations and reported usage in Ask", async () => {
  await page.route("**/assistant/agents", route => route.fulfill({ json: {
    agents: [{ id: "maintenance-integrity", label: "Maintenance & Integrity", description: "Recorded reliability",
      personas: ["Engineer"], skills: [], limits: [], human_decisions: [] }], unavailable: [],
  } }));
  await page.route("**/assistant/ask", route => route.fulfill({ json: { job_id: "demo-job", conversation_id: "demo-conversation" } }));
  await page.route("**/assistant/jobs/demo-job", route => route.fulfill({ json: {
    id: "demo-job", status: "done", conversation_id: "demo-conversation", question: "Review historical evidence",
    answer: "Historical evidence is available; review the cited records.\n\nEvidence: fz-downtime-0123456789ab",
    seconds: 1, specialists: ["maintenance-integrity"],
    steps: [{ agent: "maintenance-integrity", tool: "maintenance_history", args: { ref: "20-K-2200B" } }],
    model_calls: 1, usage: { model_calls: 1, input: 100, output: 20, cache_read: 0, cache_write: 0, cost_usd: 0.01, models: {} },
  } }));
  await page.goto(`${url}/#/ask?demo=agents&q=Review%20historical%20evidence`);
  await page.getByRole("button", { name: "paid question", exact: true }).click();
  await page.getByRole("button", { name: "Confirm paid question", exact: true }).click();
  await page.getByText("Historical evidence is available; review the cited records.", { exact: true }).waitFor();
  await page.getByText("1 tool calls · 1 s · $0.01", { exact: true }).waitFor();
  await page.getByText("Specialists: Maintenance & Integrity", { exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "fz-downtime-0123456789ab", exact: true }).getAttribute("href"), "#/e/fz-downtime-0123456789ab");
  assert.equal(await page.getByRole("button", { name: "Yes & share learning", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "Share in facility memory…", exact: true }).count(), 0);
  await page.getByRole("link", { name: "Return to Demo", exact: true }).waitFor();
});

test("Demo confirmed task completion refreshes recorded task state and prevents replay", async () => {
  let state = "waiting", writes = 0;
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ana"));
  await page.route("**/work/tasks/42", route => route.fulfill({ json: {
    ...demoTask, task: { ...demoTask.task, state, finished_by: state === "done" ? "ana" : null },
  } }));
  await page.route("**/work/items/7", route => route.fulfill({ json: { ...demoTask.item, processes: [], events: [] } }));
  await page.route("**/work/tasks/42/complete", route => {
    writes++; state = "done";
    return route.fulfill({ json: { process_state: "running" } });
  });
  await page.goto(`${url}/#/inbox/task/42?demo=workflow`);
  await page.getByRole("button", { name: "Complete", exact: true }).click();
  await page.getByRole("button", { name: "task completion", exact: true }).click();
  await page.getByRole("button", { name: "Confirm task completion", exact: true }).click();
  await page.getByText("This step is done (by ana).", { exact: true }).waitFor();
  await page.getByText("Task response saved.", { exact: false }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Complete", exact: true }).count(), 0);
  assert.equal(writes, 1);
});

test("Demo mobile route steps retain focus, history and non-overflowing layout", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${url}/#/demo?step=evidence`);
  await page.getByRole("heading", { name: "Trace an issue", exact: true }).waitFor();
  await page.getByRole("link", { name: "Next: Meet the agents", exact: true }).click();
  await page.waitForFunction(() => document.activeElement?.id === "demo-step");
  await page.reload();
  await page.getByRole("heading", { name: "Meet the agents", exact: true }).waitFor();
  await page.goBack();
  await page.getByRole("heading", { name: "Trace an issue", exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test("Demo page mounts and global refresh stay below default authenticated admission limits", async () => {
  const responses = {
    "/stats": { facility: "OKAN", pack: "okan", records: 100, entities: {}, edges: 0, open_reviews: {}, rebuild: {} },
    "/demo/config": { enabled: true },
    "/ontology/declared": { groups: [], views: [], kinds: [], relations: [] },
    "/jobs/health": { available: false, level: "unknown", summary: "" },
    "/work/items": { items: [], total: 0, overdue: 0, due_soon: 0 },
    "/work/inbox": { person: "ana", roles: [], mine: [], queue: [], counts: { mine: { total: 0 }, queue: { total: 0 } } },
  };
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ana"));
  let active = 0, maximum = 0, reads = 0;
  await page.route("**/*", async route => {
    const path = new URL(route.request().url()).pathname;
    if (!(path in responses)) return route.fallback();
    active++; maximum = Math.max(maximum, active); reads++;
    await new Promise(resolve => setTimeout(resolve, 25));
    active--;
    return route.fulfill({ json: responses[path] });
  });
  await page.goto(`${url}/#/demo?step=workflow`);
  await page.getByText("No workflow cases loaded", { exact: true }).waitFor();
  await page.getByText("No tasks are assigned or available in your queues.", { exact: false }).waitFor();
  const initial = reads;
  await page.evaluate(() => window.dispatchEvent(new Event("fuzer:changed")));
  await page.waitForFunction(() => !document.querySelector('[role="status"]'));
  await page.getByText("No workflow cases loaded", { exact: true }).waitFor();
  assert.ok(reads > initial, "Global refresh re-reads the active resources");
  assert.ok(maximum <= 2 && maximum <= 4, `Browser queue maximum ${maximum}; server default is four`);
});

test("Okan loading and transport errors keep recovery and navigation usable", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route("**/demo/investigation", async (route) => {
    await held;
    await route.abort("failed");
  });
  await page.goto(`${url}/#/investigate/okan`);
  await page.getByRole("status").filter({ hasText: "Loading" }).waitFor();
  await page.getByRole("heading", { name: "Guided investigation", exact: true }).waitFor();
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

test("Inbox says which clock due dates use when the loaded data ends well before today (#389)", async () => {
  await context.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ana"));
  await page.route("**/work/inbox?*", route => route.fulfill({ json: {
    person: "ana", roles: ["engineer"], mine: [], queue: [], counts: { mine: { total: 0 }, queue: { total: 0 } } } }));
  await page.route("**/stats", route => route.fulfill({ json: {
    facility: "OKAN", pack: "okan", records: 1, entities: {}, edges: 0, open_reviews: {}, rebuild: {}, data_through: "2025-12-30" } }));
  await page.goto(`${url}/#/inbox`);
  const note = page.getByRole("note").filter({ hasText: "The loaded data runs to" });
  await note.waitFor();
  assert.match(await note.innerText(), /due dates, SLAs and opened times are on today's clock/);
  await page.route("**/stats", route => route.fulfill({ json: {
    facility: "OKAN", pack: "okan", records: 1, entities: {}, edges: 0, open_reviews: {}, rebuild: {},
    data_through: new Date().toISOString().slice(0, 10) } }));
  await page.reload();
  await page.getByText("unclaimed in your roles", { exact: false }).first().waitFor();
  assert.equal(await page.getByRole("note").filter({ hasText: "The loaded data runs to" }).count(), 0);
});

test("the page heading takes focus on load without drawing a focus ring (#391)", async () => {
  await page.goto(`${url}/#/`);
  await page.locator("#main-content h1").first().waitFor();
  await page.evaluate(() => { location.hash = "#/exceptions"; });  // as a pasted link or Back does
  const heading = page.getByRole("heading", { level: 1, name: "All work" });
  await heading.waitFor();
  await page.waitForFunction(() => document.activeElement?.tagName === "H1");
  assert.equal(await heading.evaluate((h) => getComputedStyle(h).outlineStyle), "none");
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

const draftFixture = { id: "d_abc123", title: "Review OK-J07-03 deferment", role: "production_engineer", summary: "Rate fell after the valve failure.",
  evidence: ["fz-well-0123456789ab"], subjects: [], item_id: null, item_state: null, confirmed_by: null, confirmed_at: null };
const savedDraftTurn = { id: "turn", conversation_id: "c", question: "Route this to the production engineer", answer: "Here is a draft.\n\n[[work-draft:d_abc123]]",
  error: null, seconds: 1, specialists: [], tools: [], created_at: "2026-11-06T12:00:00Z", usage: { model_calls: 1, input: 1, output: 1, cache_read: 0, cache_write: 0, cost_usd: 0, models: {} } };

async function askForDraft(routes = {}) {
  await page.route("**/assistant/ask", (route) => route.fulfill({ json: { job_id: "job", conversation_id: "c" } }));
  await page.route("**/assistant/jobs/job", (route) => route.fulfill({ json: { id: "job", conversation_id: "c", question: "q", status: "done",
    answer: savedDraftTurn.answer, error: null, seconds: 1, specialists: [], usage: savedDraftTurn.usage, steps: [], model_calls: 1 } }));
  let state = draftFixture, confirms = 0;
  await page.route("**/work/drafts/d_abc123", (route) => route.fulfill({ json: state }));
  await page.route("**/work/drafts/d_abc123/confirm", (route) => {
    confirms++;
    if (routes.fail) return route.fulfill({ status: 500, json: { detail: "Connection lost" } });
    state = { ...draftFixture, item_id: 41, item_state: "open", confirmed_by: "ana" };
    return route.fulfill({ json: { item_id: 41, created: true } });
  });
  await page.goto(`${url}/#/ask`);
  await page.getByRole("textbox").first().fill("Route this to the production engineer");
  return { count: () => confirms };
}

test("An assistant draft creates nothing until the person confirms, and then links to its case", async () => {
  const writes = [];
  page.on("request", (request) => { if (request.method() === "POST" && request.url().includes("/work/")) writes.push(request.url()); });
  const calls = await askForDraft();
  await page.getByRole("button", { name: /^Ask/ }).first().click();
  const confirmAsk = page.getByRole("button", { name: /Confirm|Send/ }).first();
  if (await confirmAsk.isVisible().catch(() => false)) await confirmAsk.click();
  const card = page.getByRole("region", { name: "Draft work item" });
  await card.getByText("not created yet", { exact: false }).waitFor();
  await card.getByText("production engineer queue", { exact: false }).first().waitFor();
  assert.deepEqual(writes, [], "Showing a draft writes nothing");
  await card.getByRole("button", { name: "Create work item" }).click();
  await card.getByText("This adds shared work to the production engineer queue", { exact: false }).waitFor();
  assert.equal(calls.count(), 0, "Opening the confirmation creates nothing");
  await card.getByRole("group", { name: "Confirm Create work item" }).getByRole("button", { name: "Create work item" }).click();
  await card.getByText("Created by ana.", { exact: false }).waitFor();
  assert.equal(await card.getByRole("link", { name: "Open the case" }).getAttribute("href"), "#/cases/41");
  assert.equal(await card.getByRole("button", { name: "Create work item" }).count(), 0);
  assert.equal(calls.count(), 1);
});

test("A failed draft confirmation is not replayed and shows the recorded state", async () => {
  const calls = await askForDraft({ fail: true });
  await page.getByRole("button", { name: /^Ask/ }).first().click();
  const confirmAsk = page.getByRole("button", { name: /Confirm|Send/ }).first();
  if (await confirmAsk.isVisible().catch(() => false)) await confirmAsk.click();
  const card = page.getByRole("region", { name: "Draft work item" });
  await card.getByRole("button", { name: "Create work item" }).click();
  await card.getByRole("group", { name: "Confirm Create work item" }).getByRole("button", { name: "Create work item" }).click();
  await card.getByRole("alert").filter({ hasText: "Connection lost" }).waitFor();
  await card.getByText("Check what was saved before trying again", { exact: false }).waitFor();
  assert.equal(calls.count(), 1, "A failure is never retried automatically");
});

test("Cases creates a plain work item for a queue after confirmation", async () => {
  await page.route("**/work/roles", (route) => route.fulfill({ json: { person: ["engineer", "production_engineer"], agent: ["sre"] } }));
  let body;
  await page.route("**/work/items", (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ json: { items: [], total: 0, overdue: 0, due_soon: 0 } });
    body = route.request().postDataJSON();
    return route.fulfill({ json: { id: 7 } });
  });
  await page.goto(`${url}/#/cases`);
  await page.getByText("New work item", { exact: true }).click();
  const create = page.getByRole("button", { name: "Create work item" });
  assert.equal(await create.isDisabled(), true, "A title and queue come first");
  await page.getByLabel("Work item title").fill("Check OK-J07-03 gas lift");
  await page.getByLabel("Queue").selectOption("production_engineer");
  await create.click();
  assert.equal(body, undefined);
  await page.getByRole("group", { name: "Confirm Create work item" }).getByRole("button", { name: "Create work item" }).click();
  await page.getByRole("link", { name: "Open the case" }).waitFor();
  assert.deepEqual({ title: body.title, role: body.role, type: body.type }, { title: "Check OK-J07-03 gas lift", role: "production_engineer", type: "task" });
});

async function askFor(answer) {
  await page.route("**/assistant/ask", (route) => route.fulfill({ json: { job_id: "job", conversation_id: "c" } }));
  await page.route("**/assistant/jobs/job", (route) => route.fulfill({ json: { id: "job", conversation_id: "c", question: "q", status: "done",
    answer, error: null, seconds: 1, specialists: [], usage: savedDraftTurn.usage, steps: [], model_calls: 1 } }));
  await page.goto(`${url}/#/ask`);
  await page.getByRole("textbox").first().fill("Show the flow");
  await page.getByRole("button", { name: /^Ask/ }).first().click();
  const confirmAsk = page.getByRole("button", { name: /Confirm|Send/ }).first();
  if (await confirmAsk.isVisible().catch(() => false)) await confirmAsk.click();
}

test("Ask draws Mermaid diagrams and shows fenced code as code", async () => {
  await askFor([
    "The route:", "", "```mermaid", "flowchart TD", '  W["OK-J09-04<br/>Oil well"] -->|current| HP["10-V-2001"]', "  HP --> KA & KB", "```", "",
    "After the diagram.", "```python", "print(\"**not bold** | fz-tag-0123456789ab\")", "```", "Done.",
  ].join("\n"));
  const diagram = page.locator(".md-diagram");
  await diagram.locator("svg").waitFor();
  assert.ok(await diagram.locator("svg").getByText("OK-J09-04").count() > 0, "Node labels are drawn");
  assert.equal(await page.getByText("```", { exact: false }).count(), 0, "No fence is left in the text");
  assert.equal(await page.locator(".md pre code").filter({ hasText: "print(" }).locator("strong, a").count(), 0, "Code stays literal");
  await page.locator(".md p", { hasText: "After the diagram." }).waitFor();
  await page.locator(".md p", { hasText: "Done." }).waitFor();
  assert.equal(await diagram.locator("pre").count(), 0, "A drawn diagram doesn't repeat its source");
});

test("A diagram Mermaid can't draw shows its source, and diagram text can't run script", async () => {
  await askFor(["```mermaid", "this is not a diagram", "```", "", "```mermaid", "flowchart TD", '  A["<img src=x onerror=window.pwned=1>"] -->|"<script>window.pwned=1</script>"| B', '  click A "javascript:window.pwned=1"', "```"].join("\n"));
  await page.getByText("This diagram could not be drawn", { exact: false }).waitFor();
  await page.locator(".md-diagram pre").getByText("this is not a diagram").first().waitFor();
  await page.locator(".md-diagram svg").waitFor();
  assert.equal(await page.evaluate(() => window.pwned), undefined);
  assert.equal(await page.locator('.md-diagram svg img[onerror], .md-diagram svg script, .md-diagram svg a[href^="javascript"]').count(), 0);
});

test("Citations read as linked names, and raw ids never reach the page", async () => {
  const well = "fz-well-2526585e8224", term = "fz-allocation_node-b708d0477249", day = "fz-daily_report-82c067e8e550", unknown = "fz-tag-000000000000";
  await page.route("**/entities/names*", (route) => route.fulfill({ json: {
    [well]: { label: "OK-J09-04", description: "Gas-lifted oil well" },
    [term]: { label: "ESCRAVOS-TERM", description: "Escravos crude terminal" },
    [day]: { label: "Daily report 2025-12-30", description: null },
  } }));
  await askFor([`I picked **OK-J09-04** [${well}], and oil goes to the **Escravos crude terminal** [${term}].`, "", `- Output was 7,619 Sm3 [${day}]`, "", `| Well |`, `| --- |`, `| OK-J09-04 [${well}] |`, "", `A claim [${unknown}].`].join("\n"));
  const answer = page.locator(".md");
  const named = answer.locator("strong").getByRole("link", { name: "OK-J09-04", exact: true });
  await named.waitFor();
  assert.equal(await named.getAttribute("href"), `#/e/${well}`);
  assert.equal(await named.getAttribute("title"), well, "The id stays one hover away");
  await answer.locator("strong").getByRole("link", { name: "Escravos crude terminal", exact: true }).waitFor();
  await answer.getByRole("link", { name: "Daily report 2025-12-30", exact: true }).waitFor();
  await answer.getByRole("cell").getByRole("link", { name: "OK-J09-04", exact: true }).waitFor();
  const text = await answer.innerText();
  assert.equal(text.includes(well) || text.includes(term) || text.includes(day), false, `No known id in: ${text}`);
  assert.equal(text.includes("["), false, "No brackets remain");
  assert.ok(text.includes(unknown), "A citation of nothing stays visible");
});

test("If names can't load, citations fall back to their ids", async () => {
  const well = "fz-well-2526585e8224";
  await page.route("**/entities/names*", (route) => route.fulfill({ status: 500, json: { detail: "down" } }));
  await askFor(`See OK-J09-04 [${well}].`);
  await page.locator(".md").getByRole("link", { name: well, exact: true }).waitFor();
});

test("A diagram written without a fence still draws, keeping the sentence before it", async () => {
  await askFor(["Requested check: compare the link.", "", "Diagram (Mermaid; questioned link dashed):", "flowchart LR", '  IS["20-V-2001"] --> KA["20-K-2300A"] & KB["20-K-2300B"]', '  IS -.->|"EC link: verify?"| LGH["20-FT-3002"]', "", "A closing line about the graph TD layout."].join("\n"));
  await page.locator(".md-diagram svg").getByText("20-K-2300A").first().waitFor();
  await page.locator(".md p", { hasText: "Diagram (Mermaid; questioned link dashed):" }).waitFor();
  await page.locator(".md p", { hasText: "A closing line" }).waitFor();
  assert.equal(await page.locator(".md-diagram").count(), 1, "Prose naming a diagram stays prose");
});

test("A case names its subjects and evidence, with ids in the tooltip", async () => {
  const tag = "fz-tag-d60c41f05dfb", doc = "fz-document-dc4c6012c562";
  await page.route("**/entities/names*", (route) => route.fulfill({ json: { [tag]: { label: "20-V-2001", description: "GGCP Inlet Separator" }, [doc]: { label: "OKAN-20-PID-003", description: null } } }));
  await page.route("**/work/items/41", (route) => route.fulfill({ json: { id: 41, type: "task", title: "Verify EC lift-gas link", subjects: [tag], state: "open", outcome: null, priority: null, assignee: null, role: "production_engineer", due: null, source: "assistant",
    created_by: "ana", created_at: "2026-11-06T19:27:00Z", updated_at: "2026-11-06T19:27:00Z", closed_at: null, payload: { summary: "Compare the link with the P&ID.\n\nflowchart LR\n  A --> B", evidence: [tag, doc] }, processes: [], events: [] } }));
  await page.goto(`${url}/#/cases/41`);
  await page.getByRole("heading", { name: "Verify EC lift-gas link" }).waitFor();
  const about = page.getByRole("link", { name: "20-V-2001", exact: true }).first();
  await about.waitFor();
  assert.equal(await about.getAttribute("title"), tag);
  await page.getByRole("link", { name: "OKAN-20-PID-003", exact: true }).waitFor();
  await page.locator(".md-diagram svg").waitFor();
  assert.equal((await page.locator("main").innerText()).includes(doc), false, "Evidence shows names, not ids");
});

test("Mermaid loads only for an answer with a diagram", async () => {
  const chunks = [];
  page.on("request", (request) => { if (/mermaid/i.test(request.url())) chunks.push(request.url()); });
  await askFor("A plain answer with no diagram.");
  await page.locator(".md p", { hasText: "A plain answer" }).waitFor();
  assert.deepEqual(chunks, []);
});

test("Ask opens and can be left without DOM effect-cleanup crashes", async () => {
  await page.goto(`${url}/#/ask`);
  await page.getByRole("heading", { name: "Ask", exact: true }).waitFor();
  await page.getByRole("link", { name: "Home", exact: true }).first().click();
  await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
});

test("Ask names specialists from the catalog and lists what none of them can do", async () => {
  const agent = (id, label) => ({ id, label, description: `${label} evidence.`, personas: id === "orchestrator" ? [] : ["A persona"],
    skills: [], limits: [], human_decisions: ["Accountable people decide."] });
  await page.route("**/assistant/agents", (route) => route.fulfill({ json: {
    agents: [agent("orchestrator", "Orchestrator"), agent("wells-subsurface", "Wells & Subsurface"),
             agent("hse-incident-management", "HSE & Incident Management"),
             ...[1, 2, 3, 4, 5].map((n) => agent(`domain-${n}`, `Domain ${n}`))],
    unavailable: [{ agent: "hse-incident-management", capability: "Emergency response procedures", needed: "Approved plans." }],
  } }));
  await page.route("**/assistant/ask", (route) => route.fulfill({
    json: { job_id: "domain-job", conversation_id: "domain-conversation" },
  }));
  await page.route("**/assistant/jobs/domain-job", (route) => route.fulfill({ json: {
    id: "domain-job", conversation_id: "domain-conversation", question: "Summarize well evidence.",
    status: "done", answer: "Evidence summary.", error: null, seconds: 1,
    specialists: ["wells-subsurface", "troubleshooter"],
    usage: { model_calls: 1, input: 1, output: 1, cache_read: 0, cache_write: 0, cost_usd: 0, models: {} },
    steps: [{ agent: "wells-subsurface", tool: "well_performance", args: {} }], model_calls: 1,
  } }));
  await page.goto(`${url}/#/ask`);
  await page.getByText("Who answers: 7 specialists, and what they can't do").click();
  await page.getByText("HSE & Incident Management", { exact: true }).waitFor();
  await page.getByText("Emergency response procedures").waitFor();
  await page.getByLabel("Question for the operations assistant").fill("Summarize well evidence.");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await page.getByText("Specialists: Wells & Subsurface, Troubleshooter", { exact: true }).waitFor();
});

test("Ask still works when the specialist catalog can't load, and offers a retry", async () => {
  let attempts = 0;
  await page.route("**/assistant/agents", (route) => (++attempts === 1
    ? route.fulfill({ status: 500, json: { detail: "down" } })
    : route.fulfill({ json: { agents: [{ id: "wells-subsurface", label: "Wells & Subsurface", description: "Wells.",
                                         personas: ["A persona"], skills: [], limits: [], human_decisions: [] }],
                              unavailable: [] } })));
  await page.goto(`${url}/#/ask`);
  await page.getByText("The list of specialists could not be loaded. You can still ask a question.").waitFor();
  await page.getByLabel("Question for the operations assistant").waitFor();
  await page.getByRole("button", { name: "Try again" }).click();
  await page.getByText("Who answers: 1 specialist, and what they can't do").waitFor();
});

const homeStats = { facility: "Test facility", pack: "okan", records: 10, entities: {}, edges: 0, open_reviews: {}, rebuild: {} };
const panel = (kind, title, summary, data) => ({ kind, title, sources: [], spec: { data, layout: { height: 340, meta: kind === "flow-capacity" ? { horizontal_below: 560 } : undefined } },
  summary: { context: "Plant · 2026-10-14 · Fictional, historical data, not a live operating update.", headline: null, stats: [], notes: [], warnings: [], tables: [], source: "Daily reports", ...summary } });
const efficiency = panel("production-efficiency", "Production efficiency", {
  stats: [{ label: "Weighted efficiency", value: "80%" }, { label: "Allocated · paired days", value: "160", unit: "Sm3" }],
  notes: ["Unknown values are gaps, never zero."], warnings: ["Coverage is not verified."],
  tables: [{ title: "Daily data", columns: ["Day", "Binding constraints"], rows: [["2026-10-04", "Compressor [equipment] + Export [export] (tied)"]] }],
}, [{ type: "scatter", name: "Dynamic maximum", x: ["2026-10-04", "2026-10-05"], y: [100, null] }]);
const capacity = panel("flow-capacity", "System capacity and bottleneck", {
  headline: "Bottleneck not established: no stage has a declared capacity, so available capacity is unknown.",
  tables: [{ title: "Metadata needed to establish the bottleneck · 3 of 3 stages incomplete", note: "For each stage: dated capacity.",
    columns: ["Stage", "Entities"], rows: [["Crude terminal", "ESCRAVOS-TERM"]] }],
}, [{ type: "bar", name: "Approved", x: ["Wells", "Separators"], y: [130, 110], offsetgroup: "stage" }, { type: "bar", name: "System limit", x: ["Wells", "Separators"], y: [100, 100], offsetgroup: "system" }]);

test("Home shows each server-built figure in its own card, with its tables on request", async () => {
  await page.route("**/stats", (route) => route.fulfill({ json: homeStats }));
  const calls = { efficiency: 0, capacity: 0 };
  await page.route("**/analytics/figures/production-efficiency*", (route) => { calls.efficiency++; return route.fulfill({ json: efficiency }); });
  await page.route("**/analytics/figures/flow-capacity*", (route) => { calls.capacity++; return route.fulfill({ json: capacity }); });
  await page.goto(`${url}/#/`);
  const charts = page.locator(".home-charts");
  const first = charts.locator(".home-card").filter({ has: page.getByRole("heading", { name: "Production efficiency", exact: true }) });
  await page.waitForFunction(() => document.querySelector(".home-charts .js-plotly-plot")?._fullLayout?.xaxis);
  await first.getByText("Coverage is not verified.").waitFor();
  await first.getByText("Unknown values are gaps, never zero.").waitFor();
  // No carousel: no slide controls, and both charts are on the page together.
  assert.equal(await page.getByRole("button", { name: /^(Previous|Next) chart$/ }).count(), 0);
  await first.getByRole("button", { name: "Daily data" }).click();
  await page.getByRole("cell", { name: "Compressor [equipment] + Export [export] (tied)", exact: true }).waitFor();

  const second = charts.locator(".home-card").filter({ has: page.getByRole("heading", { name: "System capacity and bottleneck", exact: true }) });
  await second.getByText("Bottleneck not established", { exact: false }).waitFor();
  await page.waitForFunction(() => document.querySelectorAll(".home-charts .js-plotly-plot")[1]?._fullData?.length === 2);
  const colors = await second.locator(".js-plotly-plot").evaluate((node) => node._fullData.map((t) => t.marker.color));
  const theme = await page.evaluate(() => [1, 2].map((i) => getComputedStyle(document.documentElement).getPropertyValue(`--chart-${i}`).trim()));
  assert.deepEqual(colors, theme, "Series take the theme colorway by position");
  await second.getByRole("button", { name: "Metadata needed to establish the bottleneck · 3 of 3 stages incomplete" }).click();
  await second.getByRole("cell", { name: "Crude terminal", exact: true }).waitFor();
  assert.equal(calls.capacity, 1);
  assert.equal(calls.efficiency, 1);
  assert.deepEqual(errors, []);
});

test("A failed home figure recovers by explicit retry and a figure with no plot still shows", async () => {
  await page.route("**/stats", (route) => route.fulfill({ json: homeStats }));
  await page.route("**/analytics/figures/production-efficiency*", (route) => route.fulfill({ json: efficiency }));
  let fail = true;
  await page.route("**/analytics/figures/flow-capacity*", (route) => fail
    ? route.fulfill({ status: 500, json: { detail: "Capacity unavailable" } })
    : route.fulfill({ json: panel("flow-capacity", "System capacity and bottleneck", { headline: "No flow network reaches this facility on its latest report day." }, []) }));
  await page.goto(`${url}/#/`);
  const card = page.locator(".home-charts .home-card").nth(1);
  await card.getByText("Capacity unavailable").waitFor();
  fail = false;
  await card.getByRole("button", { name: "Try again" }).click();
  await card.getByText("No flow network reaches this facility on its latest report day.").waitFor();
  assert.equal(await card.locator(".js-plotly-plot").count(), 0);
});

test("A malformed figure is contained to its card", async () => {
  await page.route("**/stats", (route) => route.fulfill({ json: homeStats }));
  await page.route("**/analytics/figures/production-efficiency*", (route) => route.fulfill({ json: { kind: "x", summary: {} } }));
  await page.route("**/analytics/figures/flow-capacity*", (route) => route.fulfill({ json: capacity }));
  await page.goto(`${url}/#/`);
  await page.locator(".home-charts .home-card").first().getByText("This chart could not be drawn from the data returned.", { exact: false }).waitFor();
  await page.getByRole("heading", { name: "Latest daily report", exact: true }).waitFor();
  await page.locator(".home-charts .home-card").nth(1).getByText("Bottleneck not established", { exact: false }).waitFor();
});

test("Home mounts both charts and refreshes below default admission limits", async () => {
  const responses = { "/stats": homeStats, "/analytics/figures/production-efficiency": efficiency, "/analytics/figures/flow-capacity": capacity };
  let active = 0, maximum = 0;
  await page.route("**/*", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/assets/") || path === "/" || path.endsWith(".svg") || path.endsWith(".png")) return route.fallback();
    active++; maximum = Math.max(maximum, active);
    await new Promise((resolve) => setTimeout(resolve, 25));
    active--;
    return path in responses ? route.fulfill({ json: responses[path] }) : route.fallback();
  });
  await page.goto(`${url}/#/`);
  await page.getByText("Bottleneck not established", { exact: false }).waitFor();
  await page.evaluate(() => window.dispatchEvent(new Event("fuzer:changed")));
  await page.waitForFunction(() => !document.querySelector('[role="status"][aria-live="polite"].status'));
  assert.ok(maximum <= 4, `Browser queue maximum ${maximum}; server default is four`);
});

test("Empty facilities show no charts, and old Analytics links land on Home", async () => {
  await page.goto(`${url}/#/analytics?ref=plant&end=2026-10-10`);
  await page.waitForURL(`${url}/#/`);
  await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
  assert.equal(await page.locator(".home-charts").count(), 0);
});

const homeRow = (i) => ({ task_id: i, item_id: 100 + i, element: "e", name: null, kind: "task", role: "operator", assignee: null,
  requires_human: true, due_at: null, escalated_at: null, error: null, created_at: "2026-10-01T00:00:00Z", overdue: false,
  title: `Check separator level ${i}`, type: "data_review" });
const homePopulated = {
  "/stats": { ...homeStats, open_reviews: { entity_match: 12, schema_mapping_field: 3, log_link: 0 } },
  "/work/inbox": { person: "Ada", roles: ["operator"], mine: [homeRow(1)], queue: [homeRow(2)], counts: { mine: { total: 1 }, queue: { total: 4 } } },
  "/board": { cards: [{ key: "k1", rank: 1, kind: "upset", title: "Train B vibration", text: null, unit_id: null, unit: null, area_id: null,
    area: null, owner: "ops", records: [], value_usd: 1000, effective_usd: 1000, item_id: 7 }], total: 1, page: 1, per_page: 50 },
  "/incidents": { items: [{ id: "fz-incident-1", description: "20-K-2200B tripped", unit: "B", start: "2025-03-11", end: "2025-03-11",
    signals: 1, events: 1, downtime_records: 1, notifications: 1, lost_oil_sm3: 1450.4 }], total: 1 },
  "/upsets": [
    { id: 1, kind: "process", status: "detected", start: "2025-12-21T03:00:00Z", end: null, detections: 4, work_item_id: 31,
      priority_usd: null, first_unit: "train:B", first_detector: "limits", first_key: "20-PT-3003", first_point: null },
    { id: 2, kind: "process", status: "detected", start: "2025-12-22T03:00", end: null, detections: 8, work_item_id: null,
      priority_usd: null, first_unit: null, first_detector: "limits", first_key: null, first_point: "OKAN.20PT3003.PV" },
  ],
  "/reports/daily": [],
  "/sources/health": [],
  "/analytics/figures/production-efficiency": efficiency, "/analytics/figures/flow-capacity": capacity,
};
async function homeFixture(fixtures, { fail = false } = {}) {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "Ada"));
  await page.route("**/*", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path in fixtures) return route.fulfill({ json: fixtures[path] });
    if (fail && ["/reports/daily", "/work/inbox", "/board", "/incidents", "/upsets", "/sources/health", "/jobs"].includes(path)) {
      return route.fulfill({ status: 503, json: { detail: "Service unavailable" } });
    }
    return route.fallback();
  });
}

test("Home leads with the facility search, labels times and links each item to its owner", async () => {
  await homeFixture(homePopulated);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${url}/#/`);
    await page.getByRole("search").getByRole("textbox", { name: "Search the facility" }).waitFor();
    // Home is not a second menu: destinations live in the navigation, not a grid of link cards (docs/ui-style.md).
    assert.equal(await page.locator(".home-guide-links").count(), 0);
    // My work is counted, each count linking to the page that shows it; Home copies no work rows (#556).
    assert.equal(await page.getByRole("link", { name: "1 assigned to you", exact: true }).getAttribute("href"), "#/inbox");
    assert.equal(await page.getByRole("link", { name: "Check separator level 1", exact: true }).getAttribute("href"), "#/inbox/task/1");
    assert.equal(await page.getByRole("button", { name: "Claim", exact: true }).count(), 0);
    await page.getByText("Started 2025-03-11 · 1,450 Sm³ lost oil", { exact: true }).waitFor();
    assert.equal(await page.getByRole("link", { name: "20-PT-3003", exact: true }).getAttribute("href"), "#/cases/31");
    assert.equal(await page.getByRole("link", { name: "OKAN.20PT3003.PV", exact: true }).getAttribute("href"), "#/exceptions?list=ranked&view=kind");
    await page.getByText("Detected from 2025-12-21 03:00 UTC", { exact: false }).waitFor();
    await page.getByText("Detected from 2025-12-22T03:00 (timezone not supplied)", { exact: false }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await waveScreenshot(`home-populated-${width}`);
  }
});

test("Home counts the ops lead's and steward's work, each number matching the view it opens (#556)", async () => {
  const exception = (id, extra = {}) => ({ id, type: "upset", key: `k${id}`, title: `Exception ${id}`, source: "sre", state: "open",
    priority: null, value_usd: null, subjects: [], unmatched: [], item_id: null, case_state: null, assignee: null, role: null,
    raised_count: 1, first_seen: "2026-11-01T00:00:00Z", last_seen: "2026-11-01T00:00:00Z", ...extra });
  const open = [exception(1), exception(2), exception(3, { item_id: 30, role: "operator" }),
    exception(4, { item_id: 40, assignee: "ann", evidence: { field: [{ at: "2026-11-02T00:00:00Z", by: "bo", status: "done", note: null }] } })];
  await homeFixture({ ...homePopulated,
    "/work/inbox": { ...homePopulated["/work/inbox"], roles: ["ops_lead", "data_steward"] },
    "/exceptions": open, "/work/people": [], "/stewardship/unmatched": [{ ref: "P-9", exceptions: 1, open: [], sources: ["sap"] }] });
  await page.route("**/exceptions?*", (route) => route.fulfill({ json: open }));
  await page.goto(`${url}/#/`);
  for (const [name, to] of [["2 to dispatch", "#/exceptions"], ["1 with the field", "#/exceptions?list=field"],
    ["1 back from the field", "#/exceptions?list=back"], ["4 open", "#/exceptions?list=open"], ["15 records to review", "#/masterdata?tab=questions"],
    ["1 ref on open work matched nothing", "#/masterdata"]]) {
    assert.equal(await page.getByRole("link", { name, exact: true }).getAttribute("href"), to);
  }
  await page.getByRole("heading", { name: "Highest value", exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Train B vibration", exact: true }).getAttribute("href"), "#/cases/7");
  await page.getByRole("link", { name: "2 to dispatch", exact: true }).click();
  await page.getByRole("navigation", { name: "All work views" }).getByRole("link", { name: /To dispatch\s*2/ }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${url}/#/`);
  const target = await page.getByRole("link", { name: "2 to dispatch", exact: true }).boundingBox();
  assert.ok(target.height >= 44, `a count is a 44 px target on a phone, got ${target.height}`);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test("Home with no records keeps only work and health, and readers see no administration", async () => {
  await homeFixture({ ...homePopulated, "/stats": { ...homeStats, records: 0, open_reviews: {} } });
  await page.goto(`${url}/#/`);
  await page.getByRole("heading", { name: "Connect data before exploring", exact: true }).waitFor();
  await page.getByRole("heading", { name: "My work", exact: true }).waitFor();
  await page.getByRole("heading", { name: "Data and scheduler health", exact: true }).waitFor();
  for (const absent of ["Latest daily report", "Incidents and upsets"]) {
    assert.equal(await page.getByRole("heading", { name: absent, exact: true }).count(), 0, `${absent} hidden without records`);
  }
  await waveScreenshot("home-empty-1440");

  const reader = await browser.newContext();
  const readerPage = await reader.newPage();
  const fixtures = { ...homePopulated, "/auth/me": { mode: "microsoft", configured: true, user: signedInUser }, "/demo/config": { enabled: true } };
  const administrative = [];
  await readerPage.route("**/*", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (["/sources/health", "/jobs"].includes(path)) administrative.push(path);
    if (path.startsWith("/assets/") || path === "/" || path.endsWith(".svg") || path.endsWith(".png")) return route.continue();
    return route.fulfill({ json: fixtures[path] ?? [] });
  });
  await readerPage.goto(`${url}/#/`);
  await readerPage.getByRole("heading", { name: "Incidents and upsets", exact: true }).waitFor();
  assert.equal(await readerPage.getByRole("heading", { name: "Data and scheduler health", exact: true }).count(), 0);
  assert.equal(await readerPage.getByRole("link", { name: /Source readiness|Application settings/ }).count(), 0);
  assert.deepEqual(administrative, []);
  await reader.close();
});

test("Home shows a failed read with its own retry instead of an empty card", async () => {
  await homeFixture({ "/stats": homeStats }, { fail: true });
  await page.goto(`${url}/#/`);
  for (const card of ["Latest daily report", "My work", "Incidents and upsets", "Data and scheduler health"]) {
    const section = page.locator("section.home-card").filter({ has: page.getByRole("heading", { name: card, exact: true }) });
    await section.getByRole("alert").first().waitFor();
    assert.ok(await section.getByRole("button", { name: "Try again", exact: true }).count() >= 1, `${card} offers a retry`);
  }
  for (const zero of ["No incidents recorded.", "Nothing waiting for you.", "No daily report recorded."]) {
    assert.equal(await page.getByText(zero, { exact: false }).count(), 0, `a failure is not shown as "${zero}"`);
  }
});

test("Home charts stack on a phone and redraw for the wider screen on rotation", async () => {
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const mobile = await phone.newPage();
  for (const [path, json] of [["**/auth/me", { mode: "local", configured: true, user: null }], ["**/stats", homeStats], ["**/demo/config", { enabled: true }],
    ["**/analytics/figures/production-efficiency*", efficiency], ["**/analytics/figures/flow-capacity*", capacity]]) {
    await mobile.route(path, (route) => route.fulfill({ json }));
  }
  await mobile.goto(`${url}/#/`);
  await mobile.getByRole("heading", { name: "System capacity and bottleneck", exact: true }).waitFor();
  await mobile.waitForFunction(() => document.querySelectorAll(".home-charts .js-plotly-plot")[1]?._fullData?.[0]?.orientation === "h");
  assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0);
  await mobile.setViewportSize({ width: 800, height: 844 });
  await mobile.waitForFunction(() => document.querySelectorAll(".home-charts .js-plotly-plot")[1]?._fullData?.[0]?.orientation === "v");
  assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0);
  await phone.close();
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

test("a field operator sees a dispatched job on a phone and reports back; the IOC dispatches from Exceptions", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "fred"));
  const job = { id: 7, type: "leak", key: "walk-9", title: "Leak at P-101", source: "olga", state: "open", priority: null,
    value_usd: 1200, subjects: ["fz-equipment-0123456789ab"], unmatched: ["tag:SPARE-9"], item_id: 31, case_state: "open",
    assignee: "fred", role: null, raised_count: 2, first_seen: "2026-11-08T06:00:00Z", last_seen: "2026-11-08T07:00:00Z",
    label: "Leak", due: null, task_id: null, task_name: null,
    assets: [{ fuzer_id: "fz-equipment-0123456789ab", label: "P-101 export pump", kind: "equipment", lat: 52.1, lon: 4.1 }],
    finding: { detector: "test", origin: "external", origin_label: "Reported by", method: null, standard: null, start: null, end: null, confidence: null, items: [{ label: "Drips per min", value: 12, kind: "number" }], checks: [], subjects: [], value_usd: null }, history: [{ id: 3, title: "P-101 vibration high", state: "cleared", first_seen: "2026-11-01T06:00:00Z" }],
    field: [], queued: false, sent: { by: "ann", at: "2026-11-08T07:05:00Z", reason: "isolate and check the gland" } };
  const waiting = { ...job, id: 8, key: "scada-1", title: "Compressor trip on high discharge pressure", item_id: 32, assignee: null,
    role: "operator", queued: true, sent: null, history: [] };
  const reports = [];
  // Relative to now: the Coming up card offers Done for anything starting within a day, so a fixed date
  // stops being "later" once the clock reaches it.
  const soon = { id: 12, kind: "maintenance", title: "Replace P-101 seal", source: "sap", source_key: "4000001",
    subjects: ["fz-equipment-0123456789ab"], area_id: null, area_label: null, start_at: new Date(Date.now() + 3 * 86_400_000).toISOString(),
    end_at: new Date(Date.now() + 3 * 86_400_000 + 8 * 3_600_000).toISOString(), status: "scheduled", crew: "fred", crew_hours: null, needs_shutdown: false,
    deferment_usd: null, exception_id: null, notes: "bring the seal kit", clashes: [],
    assets: [{ fuzer_id: "fz-equipment-0123456789ab", label: "P-101 export pump" }] };
  const today = { ...soon, id: 13, title: "Operator rounds", crew: "role:operator", notes: null,
    start_at: new Date().toISOString(), end_at: new Date(Date.now() + 3_600_000).toISOString() };
  const changed = [];
  await page.route("**/plan/13", (route) => { changed.push(route.request().postDataJSON()); return route.fulfill({ json: today }); });
  await page.route("**/exceptions/field?*", (route) => route.fulfill({ json: { person: "fred", jobs: [job, waiting], coming_up: [today, soon] } }));
  const taken = [];
  await page.route("**/exceptions/8/dispatch", (route) => { taken.push(route.request().postDataJSON()); return route.fulfill({ json: waiting }); });
  await page.route("**/exceptions/7/report", (route) => { reports.push(route.request().postDataJSON()); return route.fulfill({ json: job }); });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto(`${url}/#/field`);  // the old My jobs link lands on My work (#503)
  await page.waitForURL(/#\/inbox/);
  await page.getByRole("heading", { name: /Leak at P-101/ }).waitFor();
  await page.getByRole("link", { name: "directions" }).first().waitFor();
  await page.getByText("Drips per min").first().waitFor();
  await page.getByText("isolate and check the gland").waitFor();  // what the IOC asked for, on the job
  const queued = page.locator(".field-job", { hasText: "Compressor trip" });
  assert.equal(await queued.getByRole("button", { name: "Done" }).count(), 0);  // not theirs until taken
  await queued.getByRole("button", { name: "Take it" }).click();
  await expectReports(taken, 1);
  assert.deepEqual([taken[0].by, taken[0].to], ["fred", "fred"]);
  const mine = page.locator(".field-job", { hasText: "Leak at P-101" });
  await page.getByRole("heading", { name: "Coming up" }).waitFor();
  await page.getByText("bring the seal kit").waitFor();
  await page.getByText("for operator").waitFor();
  const coming = page.locator(".field-coming");
  assert.equal(await coming.getByRole("button", { name: "Done" }).count(), 1);  // only today's job can be closed
  await coming.getByRole("button", { name: "Done" }).click();
  await expectReports(changed, 1);
  assert.deepEqual(changed[0], { by: "fred", status: "done" });
  assert.equal(await mine.getByRole("button", { name: "Could not do" }).isDisabled(), true);
  await mine.getByRole("button", { name: "On site" }).click();
  await mine.getByLabel(/Note for the IOC/).fill("valve seized");
  await mine.getByRole("button", { name: "Could not do" }).click();
  await expectReports(reports, 2);
  assert.deepEqual(reports.map((r) => r.status), ["on_site", "could_not_do"]);
  assert.equal(reports[1].note, "valve seized");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await waveScreenshot("field-jobs");

  const dispatched = [];
  await page.route("**/exceptions?*", (route) => route.fulfill({ json: [{ ...job, item_id: null, assignee: null }] }));
  await page.route("**/work/people", (route) => route.fulfill({ json: [{ name: "fred", roles: ["operator"] }] }));
  await page.route("**/work/roles/catalog", (route) => route.fulfill({ json: [{ name: "operator", kind: "person", members: [] },
    { name: "engineer", kind: "person", members: [] }, { name: "maintenance-integrity", kind: "agent", members: [] }] }));
  await page.route("**/exceptions/refs", (route) => route.fulfill({ json: [{ form: "tag:<key>", example: "tag:20-K-2100B", entities: 812 }] }));
  await page.route("**/exceptions/7/dispatch", (route) => { dispatched.push(route.request().postDataJSON()); return route.fulfill({ json: job }); });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${url}/#/exceptions`);
  const views = page.getByRole("navigation", { name: "All work views", exact: true });
  await views.getByRole("link", { name: "To dispatch 1", exact: true }).waitFor();  // the count shows before the view opens
  assert.equal(await views.getByRole("link", { name: "To dispatch 1", exact: true }).getAttribute("aria-current"), "page");
  // Exception types are configuration, so they live on Settings > Routing, not under All work.
  await page.goto(`${url}/#/routing`);
  await page.getByText("tag:20-K-2100B").waitFor();  // what another system's refs should look like
  const queue = page.getByLabel("Queue");  // a new type routes to a known queue, picked, not typed
  await queue.getByRole("option", { name: "engineer queue" }).waitFor({ state: "attached" });
  assert.deepEqual(await queue.locator("option").allTextContents(), ["waits for the IOC to dispatch", "operator queue", "engineer queue"]);
  await page.goto(`${url}/#/exceptions`);
  await page.getByLabel("Dispatch to").getByRole("option", { name: "fred (operator)" }).waitFor({ state: "attached" });
  await page.getByLabel("Dispatch to").selectOption("fred");
  await page.getByLabel("What to do").fill("isolate and check the gland");
  await page.getByRole("button", { name: "Dispatch" }).click();
  await expectReports(dispatched, 1);
  assert.equal(dispatched[0].to, "fred");
  assert.equal(dispatched[0].reason, "isolate and check the gland");
});

test("the field's report sits above the IOC's decision, and operators are only offered their own step (#452)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "erin"));
  const comment = (id, text, actor, at) => ({ id, kind: "commented", actor, element: null, data: { text }, at });
  await page.route("**/work/tasks/42", (route) => route.fulfill({ json: { ...demoTask,
    task: { ...demoTask.task, element: "triage", name: "Triage", requires_human: false, assignee: "erin" },
    data: { field_ask: "read the wellhead gauge", field_result: "done" },
    evidence: { ...demoTask.evidence, comments: [comment(5, "Field: Done: gauge reads 13 barg", "otto", "2026-11-09T09:00:00Z"),
      comment(4, "looks like a sticky valve", "erin", "2026-11-09T08:00:00Z")] } } }));
  await page.route("**/work/items/7", (route) => route.fulfill({ json: { ...demoTask.item, processes: [], events: [] } }));
  await page.goto(`${url}/#/inbox/task/42`);
  const box = page.locator(".from-field");
  await box.getByRole("heading", { name: "From the field" }).waitFor();
  await box.getByText("You asked: read the wellhead gauge").waitFor();
  await box.getByText("Done: gauge reads 13 barg").waitFor();
  assert.equal(await box.getByText("sticky valve").count(), 0);  // the IOC's own notes stay in the history

  const base = { type: "upset", source: "sre", state: "open", priority: null, value_usd: null, subjects: [], unmatched: [],
    case_state: "open", role: null, raised_count: 1, first_seen: "2026-11-08T06:00:00Z", last_seen: "2026-11-08T07:00:00Z",
    label: "Upset", due: null, assets: [], finding: { detector: "test", origin: "external", origin_label: "Reported by", method: null, standard: null, start: null, end: null, confidence: null, items: [], checks: [], subjects: [], value_usd: null }, history: [], field: [], queued: false, evidence: {} };
  const theirs = { ...base, id: 1, key: "a", title: "Low pressure across 3 assets", item_id: 31, assignee: null, task_id: 51,
    task_name: "Field check", own_step: true, sent: { by: "erin", at: "2026-11-09T07:00:00Z", reason: "read the gauge" } };
  const handed = { ...base, id: 2, key: "b", title: "Gauge reads odd", item_id: 32, assignee: null, task_id: 52,
    task_name: "Triage", own_step: false, sent: { by: null, at: null, reason: "look at it" } };
  await page.route("**/exceptions/field?*", (route) => route.fulfill({ json: { person: "otto", jobs: [theirs, handed], coming_up: [] } }));
  await page.goto(`${url}/#/inbox`);
  await page.locator(".field-job", { hasText: "Low pressure" }).getByRole("link", { name: "Open step: Field check" }).waitFor();
  const other = page.locator(".field-job", { hasText: "Gauge reads odd" });
  await other.getByText("look at it").waitFor();
  assert.equal(await other.getByRole("link", { name: /Open step/ }).count(), 0);  // an engineer's form isn't theirs

  const back = { ...base, id: 3, key: "c", title: "Seal weeping on P-101", item_id: 33, assignee: "erin",
    evidence: { field: [{ at: "2026-11-09T09:00:00Z", by: "otto", status: "done", note: "seal replaced" }] } };
  const fresh = { ...base, id: 4, key: "d", title: "Compressor trip", item_id: null, assignee: null };
  await page.route("**/exceptions?*", (route) => route.fulfill({ json: [back, fresh] }));
  await page.route("**/exceptions/types", (route) => route.fulfill({ json: [] }));
  await page.route("**/work/people", (route) => route.fulfill({ json: [] }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${url}/#/exceptions`);
  await page.getByRole("navigation", { name: "All work views", exact: true }).getByRole("link", { name: "Back from the field 1" }).click();
  await page.getByText("Seal weeping on P-101").waitFor();
  assert.equal(await page.getByText("Compressor trip").count(), 0);
});

test("All work holds the Work board and Cases as views, and their old links land on the same view (#502)", async () => {
  const exc = (id, extra) => ({ id, type: "upset", key: `k${id}`, source: "sre", state: "open", priority: null, value_usd: null,
    subjects: [], unmatched: [], case_state: "open", role: null, raised_count: 1, first_seen: "2026-11-08T06:00:00Z",
    last_seen: "2026-11-08T07:00:00Z", item_id: null, assignee: null, evidence: {}, ...extra });
  await page.route("**/exceptions?*", (route) => route.fulfill({ json: [
    exc(1, { title: "Compressor trip" }),
    exc(2, { title: "Packing leak", item_id: 21, role: "operator" }),
    exc(3, { title: "Seal weeping", item_id: 22, assignee: "erin",
      evidence: { field: [{ at: "2026-11-09T09:00:00Z", by: "otto", status: "done", note: "seal replaced" }] } }),
  ] }));
  await page.route("**/work/people", (route) => route.fulfill({ json: [{ name: "otto", roles: ["operator"] }] }));
  await page.route("**/work/roles/catalog", (route) => route.fulfill({ json: [] }));
  await page.route("**/exceptions/types", (route) => route.fulfill({ json: [] }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${url}/#/exceptions`);
  const views = page.getByRole("navigation", { name: "All work views", exact: true });
  await views.getByRole("link", { name: "All open 3", exact: true }).waitFor();
  await page.getByText("Compressor trip").waitFor();
  assert.deepEqual((await views.locator("a").allTextContents()).map((t) => t.trim()),
    ["To dispatch 1", "With the field 1", "Back from the field 1", "All open 3", "In progress", "Ranked by value", "Closed"]);
  await page.getByText("Compressor trip").waitFor();
  assert.equal(await page.getByText("Packing leak").count(), 0);
  await views.getByRole("link", { name: "With the field 1" }).click();
  await page.getByText("Packing leak").waitFor();
  assert.equal(await page.getByText("Compressor trip").count(), 0);

  // One filter bar for every view, kept in the URL across views (#502 part 2).
  await page.goto(`${url}/#/exceptions?list=open`);
  await page.getByText("Seal weeping").waitFor();
  await page.getByRole("searchbox", { name: "Search titles" }).fill("seal");
  await page.waitForURL(/list=open&q=seal$/);
  await page.getByText("Seal weeping").waitFor();
  assert.equal(await page.getByText("Compressor trip").count(), 0);
  await page.getByRole("combobox", { name: "Held by" }).selectOption("otto");
  await page.waitForURL(/who=otto/);
  await page.getByText("Nothing here.").waitFor();  // erin holds the seal case, not otto
  assert.match(await views.getByRole("link", { name: /In progress/ }).getAttribute("href"), /list=progress&who=otto&q=seal/);

  await page.goto(`${url}/#/board?view=kind`);
  await page.waitForURL(/#\/exceptions\?list=ranked&view=kind$/);
  assert.equal(await views.getByRole("link", { name: "Ranked by value" }).getAttribute("aria-current"), "page");
  await page.getByRole("heading", { level: 1, name: "All work", exact: true }).waitFor();
  assert.equal(await page.getByRole("heading", { level: 1 }).count(), 1);
  await page.goto(`${url}/#/cases?state=closed&type=upset`);
  await page.waitForURL(/#\/exceptions\?list=closed&type=upset$/);
  await page.goto(`${url}/#/cases`);
  await page.waitForURL(/#\/exceptions\?list=progress$/);
  await page.getByText("No cases match.").waitFor();
  const work = page.getByRole("navigation", { name: "Work pages", exact: true });
  assert.equal(await work.getByRole("link", { name: "All work", exact: true }).getAttribute("aria-current"), "page");
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test("a fresh demo offers a sample IOC team, an agent step with no worker can be taken over, evidence reads as words", async () => {
  const added = [];
  let roster = [];
  await page.route("**/work/people", (route) => route.fulfill({ json: roster }));
  await page.route("**/work/people/*", (route) => {
    added.push({ name: decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop()), ...route.request().postDataJSON() });
    roster = [...roster, { name: added.at(-1).name, roles: added.at(-1).roles }];
    return route.fulfill({ json: { name: added.at(-1).name, roles: added.at(-1).roles } });
  });
  await page.route("**/exceptions/field?*", (route) => route.fulfill({ json: { person: "riley", jobs: [], coming_up: [] } }));
  await page.goto(`${url}/#/inbox`);
  await page.getByText("Nobody is on the team yet.").waitFor();
  await page.getByRole("button", { name: "Add the sample team" }).click();
  await page.getByRole("button", { name: "Confirm Add the sample team" }).click();
  await expectReports(added, 5);
  assert.deepEqual(added.map((p) => [p.name, p.roles]), [["riley", ["ops_lead", "engineer"]], ["sam", ["operator"]],
    ["pat", ["maintenance_planner"]], ["jordan", ["production_engineer"]], ["casey", ["data_steward"]]]);
  await page.getByText("Nobody is on the team yet.").waitFor({ state: "detached" });

  // a demo that added the earlier three-person team gets the production engineer and data steward, and only them
  added.length = 0;
  roster = roster.slice(0, 3);
  await page.reload();
  await page.getByText("Some queues have nobody in them.").waitFor();
  await page.getByRole("button", { name: "Add the rest of the sample team" }).click();
  await page.getByRole("button", { name: "Confirm Add the rest of the sample team" }).click();
  await expectReports(added, 2);
  assert.deepEqual(added.map((p) => p.name), ["jordan", "casey"]);
  await page.getByText("Some queues have nobody in them.").waitFor({ state: "detached" });

  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "riley"));
  const handed = [];
  const step = { id: 77, process_id: 9, element: "diagnose", name: "Diagnose", kind: "agent", state: "waiting", flow: null,
    role: "field-operations-facilities", assignee: null, requires_human: false, due_at: null, escalated_at: null, attempts: 0,
    error: null, created_at: new Date().toISOString(), finished_at: null, finished_by: null, unclaimed: true,
    fallback_at: new Date(Date.now() + 30 * 60_000).toISOString() };
  await page.route("**/work/items/7", (route) => route.fulfill({ json: { id: 7, title: "Upset case", type: "upset", state: "open",
    subjects: [], created_by: "agent:sre", created_at: "2026-11-09T09:00:00Z", events: [], agents_configured: false,
    payload: { start: "2025-12-30T00:00", first_detection: "excursions:alarm:lo" },
    processes: [{ id: 9, process_key: "upset", version: 1, definition_id: 1, parent_token: null, state: "running", outcome: null,
      data: {}, started_by: "agent:sre", started_at: "2026-11-09T09:00:00Z", path: [], open: [step] }] } }));
  await page.route("**/work/tasks/77/hand-off", (route) => { handed.push(route.request().postDataJSON()); return route.fulfill({ json: {} }); });
  await page.goto(`${url}/#/cases/7`);
  await page.getByText("No AI provider is configured for agents").waitFor();  // straight away, not after 10 minutes
  // and it says when the step goes to a person if nobody picks it up (P0.3)
  await page.getByRole("note").filter({ hasText: "It goes to a person queue at" }).waitFor();
  await page.getByRole("button", { name: "Take it over" }).click();
  await expectReports(handed, 1);
  assert.equal(handed[0].to, "riley");
  await page.getByText("Details").click();
  await page.getByText("2025-12-30 00:00 (data time)").waitFor();
  await page.getByText("alarm low excursions").waitFor();
});

async function expectReports(list, n) {
  for (let i = 0; i < 50 && list.length < n; i++) await new Promise((r) => setTimeout(r, 100));
  assert.equal(list.length, n);
}

test("the integrated plan shows clashes and bundling by area and reschedules an item", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ann"));
  const at = (d) => new Date(Date.now() + d * 86_400_000).toISOString();
  const base = { source: "sap", subjects: ["fz-equipment-0123456789ab"], area_id: "fz-area-0123456789ab", area_label: "North",
    crew: null, crew_hours: null, deferment_usd: null, exception_id: null, notes: null,
    assets: [{ fuzer_id: "fz-equipment-0123456789ab", label: "P-101" }] };
  const items = [
    { ...base, id: 1, kind: "maintenance", title: "Replace P-101 seal", source_key: "4000001", start_at: at(2), end_at: at(3),
      status: "scheduled", needs_shutdown: false,
      clashes: [{ kind: "bundle", with: 2, title: "North train overhaul", say: "could be done during “North train overhaul”" },
        { kind: "same_asset", with: 3, title: "Lube P-101", say: "at the same time as “Lube P-101” on the same asset" }],
      parts_short: [{ id: "fz-material-0123456789ab", label: "10001 seal kit", description: "Mechanical seal", mpn: null, bin: null,
        for: "fz-equipment-0123456789ab" }] },
    { ...base, id: 2, kind: "turnaround", title: "North train overhaul", source_key: "4000002", start_at: at(10), end_at: at(15),
      status: "proposed", needs_shutdown: true, clashes: [] },
  ];
  const changes = [];
  await page.route("**/plan?*", (route) => route.fulfill({ json: items }));
  await page.route("**/plan/1", (route) => { changes.push(route.request().postDataJSON()); return route.fulfill({ json: items[0] }); });
  await page.goto(`${url}/#/plan`);
  await page.getByRole("heading", { name: "North", exact: true }).waitFor();
  await page.getByText("1 with a clash").waitFor();
  await page.getByText("1 could ride along with a shutdown").waitFor();
  await page.getByRole("button", { name: /Replace P-101 seal/ }).click();
  await page.getByText(/Clash: at the same time as “Lube P-101”/).waitFor();
  await page.getByText(/Bundle: could be done during/).waitFor();
  await page.getByText("1 waiting on parts").waitFor();
  await page.getByText(/no stock on record.*10001 seal kit \(Mechanical seal\)/).waitFor();
  await page.getByLabel("Crew").fill("fred");
  await page.getByRole("button", { name: "Reschedule" }).click();
  await expectReports(changes, 1);
  assert.equal(changes[0].crew, "fred");
  await page.setViewportSize({ width: 390, height: 900 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await waveScreenshot("plan");
});

test("an asset's page shows the open exceptions about it and the work planned on it", async () => {
  const id = "fz-equipment-0123456789ab";
  await page.route(`**/entities/${id}/map`, (route) => route.fulfill({ json: { features: [], focus: null } }));
  await page.route(`**/entities/${id}`, (route) => route.fulfill({ json: { fuzer_id: id, kind: "facility", label: "P-101 export pump",
    golden: {}, values: {}, records: [], keys: [], edges: [], edge_counts: {} } }));
  await page.route("**/exceptions?*", (route) => route.fulfill({ json: [{ id: 7, type: "leak", key: "w", title: "Leak at P-101",
    source: "olga", state: "open", priority: null, value_usd: null, subjects: [id], unmatched: [], item_id: null, case_state: null,
    assignee: null, role: null, raised_count: 1, first_seen: "2026-11-08T06:00:00Z", last_seen: "2026-11-08T07:00:00Z" }] }));
  await page.route("**/plan?*", (route) => route.fulfill({ json: [{ id: 12, kind: "maintenance", title: "Replace P-101 seal",
    source: "sap", source_key: "4000001", subjects: [id], area_id: null, area_label: null, start_at: "2026-11-12T08:00:00Z",
    end_at: "2026-11-13T16:00:00Z", status: "scheduled", crew: "fred", crew_hours: null, needs_shutdown: false, deferment_usd: null,
    exception_id: null, notes: null, assets: [], clashes: [] }] }));
  await page.goto(`${url}/#/e/${id}`);
  await page.getByRole("heading", { name: "Work here" }).waitFor();
  await page.getByRole("link", { name: "Leak at P-101" }).waitFor();
  await page.getByText("not dispatched").waitFor();
  await page.getByRole("link", { name: "Replace P-101 seal" }).waitFor();
});

test("an entity page reads labels, PI AF paths and long relations as a person would (#392, #400, #401)", async () => {
  const id = "fz-well-0123456789ab";
  const edge = (i) => ({ direction: "in", rel: "tested", other: `fz-well_test-${String(i).padStart(12, "0")}`, other_kind: "well_test",
    other_label: `WT-${i}`, other_description: null, valid_from: null, valid_to: null, sources: ["ec"], confidence: 1, props: null });
  await page.route(`**/entities/${id}/map`, (route) => route.fulfill({ json: { features: [], focus: null } }));
  await page.route(`**/entities/${id}`, (route) => route.fulfill({ json: { fuzer_id: id, kind: "well", label: "OK-J09-04",
    golden: { isa95_level: { value: "unit", source: "pi_af", rule: "priority" }, latitude: { value: 4.1, source: "gis", rule: "priority" },
      longitude: { value: 5.2, source: "gis", rule: "priority" }, geometry: { value: { type: "Point" }, source: "gis", rule: "priority" } },
    values: {}, keys: [], edge_counts: {}, edges: Array.from({ length: 24 }, (_, i) => edge(i + 1)),
    records: [{ source: "pi_af", record_type: "element", source_key: "OKAN\\20 OK_GGCP\\20-K-2300A", method: "singleton", confidence: 1 }] } }));
  await page.goto(`${url}/#/e/${id}`);
  await page.getByRole("rowheader", { name: "ISA-95 level", exact: true }).or(page.getByText("ISA-95 level", { exact: true })).first().waitFor();
  assert.equal(await page.getByText("geometry", { exact: false }).count(), 0);
  await page.getByText("OKAN › 20 OK_GGCP › 20-K-2300A", { exact: true }).waitFor();
  assert.equal(await page.getByRole("link", { name: /^WT-/ }).count(), 5);
  await page.getByRole("button", { name: "Show all 24", exact: true }).click();
  assert.equal(await page.getByRole("link", { name: /^WT-/ }).count(), 24);
});

test("work board cards are dense rows and don't repeat 'unassigned' (#408)", async () => {
  const card = (rank, owner) => ({ key: `k${rank}`, kind: "sap_backlog", title: `Replace seal ${rank}`, text: null, unit_id: null,
    unit: null, area_id: null, area: "North", owner, records: [`r${rank}`], value_usd: 1000, effective_usd: 1000, reason: null,
    breakdown: {}, rank, prev_rank: rank, moved: null, pinned: false, pin_reason: null, pinned_by: null, item_id: null,
    first_seen: "2026-11-01T00:00:00Z" });
  await page.route("**/board?*", route => route.fulfill({ json: { total: 2, page: 1, per_page: 50,
    cards: [card(1, "unassigned"), card(2, "ana")], facets: { kind: {}, owner: {}, area: [] } } }));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`${url}/#/board`);
  await page.getByText("Replace seal 1", { exact: true }).waitFor();
  const box = await page.locator(".board-card").first().boundingBox();
  assert.ok(box.height <= 60, `a card should be a dense row, got ${box.height}px`);
  assert.equal(await page.locator(".board-card").getByText("unassigned", { exact: true }).count(), 0);
  await page.locator(".board-card").getByText("ana", { exact: true }).waitFor();
});

test("a turnaround shows its scope on the plan and brings planned work into it", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ann"));
  const item = (id, title, extra = {}) => ({ id, kind: "inspection", title, source: "sap_plan", source_key: null, subjects: [],
    area_id: "fz-area-0123456789ab", area_label: "North", start_at: "2027-03-01T00:00:00Z", end_at: "2027-03-02T00:00:00Z",
    status: "proposed", crew: null, crew_hours: null, needs_shutdown: true, deferment_usd: null, exception_id: null, notes: null,
    assets: [], clashes: [], ...extra });
  const scope = { revision: "TA26", revisions: ["TA26"], as_of: "2026-11-09", planned_start: "2026-11-20",
    scoped_work: [{ id: "fz-wo-1", order: "4000002", tag: "P-101", tag_id: "fz-equipment-0123456789ab", priority: "2",
      text: "North train overhaul", planned_start: "2026-11-20", status: "CRTD" }],
    shutdown_plans_not_in_scope: [], bad_actors_not_in_scope: [{ id: "fz-equipment-1", tag: "K-201", failures: 4, lost_oil_sm3: 900 }],
    spares_out_of_stock: [{ for: "P-101", material: "Mech seal", id: "fz-material-1", description: "seal kit" }],
    window: { start: "2026-11-20T00:00:00Z", end: "2026-11-25T00:00:00Z" }, on_plan: [],
    ride_along: [item(5, "Repack P-102", { needs_shutdown: false, kind: "maintenance", start_at: "2026-11-10T00:00:00Z" })],
    shutdown_outside: [item(6, "PSV-101 statutory test")] };
  const brought = [];
  await page.route("**/turnarounds?*", (route) => route.fulfill({ json: scope }));
  await page.route("**/turnarounds", (route) => route.fulfill({ json: scope }));
  await page.route("**/turnarounds/bring-in", (route) => { brought.push(route.request().postDataJSON()); return route.fulfill({ json: scope.shutdown_outside[0] }); });
  await page.goto(`${url}/#/turnaround`);
  // Turnaround is a window on the Plan now (#554): the old link opens Plan with the revision beside the timeline.
  await page.waitForURL(/#\/plan\?revision=$/);
  await page.getByRole("heading", { level: 1, name: "Integrated plan" }).waitFor();
  await page.getByRole("heading", { name: "Turnaround TA26" }).waitFor();
  assert.equal(await page.getByRole("navigation", { name: "Work pages", exact: true })
    .getByRole("link", { name: "Plan", exact: true }).getAttribute("aria-current"), "page");
  await page.getByText("North train overhaul").waitFor();
  await page.getByText("1 spares out of stock").waitFor();
  await page.getByRole("heading", { name: "Needs the unit down, planned outside it" }).waitFor();
  await page.getByRole("button", { name: "Bring into TA26" }).first().click();
  await expectReports(brought, 1);
  assert.deepEqual(brought[0], { by: "ann", item_id: 6, revision: "TA26" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.getByRole("link", { name: "Close the turnaround" }).click();
  await page.waitForURL(/#\/plan$/);
  await page.getByRole("heading", { name: "Turnaround TA26" }).waitFor({ state: "detached" });
});

test("the incoming lead finds the shift handover from My work, not the nav (#504)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ann"));
  const inbox = { person: "ann", roles: ["ops_lead"], mine: [], queue: [], counts: { mine: { total: 0 }, queue: { total: 0 } } };
  await page.route("**/work/inbox?*", (route) => route.fulfill({ json: inbox }));
  await page.route("**/work/inbox", (route) => route.fulfill({ json: inbox }));
  let acked = false;
  const row = () => ({ id: 7, shift_start: "2026-11-11T18:00:00Z", shift_end: "2026-11-12T06:00:00Z",
    state: acked ? "acknowledged" : "handed_over", notes_by: "bo", acknowledged_by: acked ? "ann" : null,
    acknowledged_at: acked ? "2026-11-12T06:10:00Z" : null, item_id: 40, lines: 6 });
  await page.route("**/handovers", (route) => route.fulfill({ json: [row()] }));
  await page.route("**/handovers/7", (route) => route.fulfill({ json: { ...row(), facts: { sections: { "Open work": [] } },
    drafted_at: "2026-11-12T06:00:00Z", notes: "Watch K-201", notes_at: "2026-11-12T05:50:00Z", acknowledged_note: null } }));
  await page.goto(`${url}/#/inbox`);
  await page.getByText("Shift handover ready", { exact: false }).waitFor();
  await page.getByRole("link", { name: "Read and acknowledge" }).click();
  await page.waitForURL(/#\/handover\?id=7$/);
  await page.getByRole("button", { name: "I have read it: acknowledge" }).waitFor();
  acked = true;
  await page.goto(`${url}/#/inbox`);
  await page.getByRole("link", { name: "Shift handover", exact: true }).waitFor();
  await page.getByText("Shift handover ready", { exact: false }).waitFor({ state: "detached" });
});

test("a dispatched operator sees what they were sent on My work and lands on the job from it (#604)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "olav"));
  const inbox = { person: "olav", roles: ["operator"], mine: [], queue: [], counts: { mine: { total: 0 }, queue: { total: 0 } } };
  await page.route("**/work/inbox?*", (route) => route.fulfill({ json: inbox }));
  // the case the notification opens: without it the case page renders an empty list as a case and fails
  await page.route("**/work/items/41", (route) => route.fulfill({ json: { ...demoTask.item, id: 41, processes: [], events: [] } }));
  const read = [];
  let notifications = [
    { id: 3, kind: "dispatched", item_id: 41, token_id: null, text: "ines sent you 'Check the pump seal'. seal weeping",
      link: "#/cases/41", channel: "in_app", delivery: null, created_at: "2026-11-12T06:05:00Z", read_at: null },
    { id: 2, kind: "due_soon", item_id: 40, token_id: 9, text: "'Swap the filter' is due in 20 min.",
      link: "#/cases/40", channel: "in_app", delivery: null, created_at: "2026-11-12T06:00:00Z", read_at: null },
  ];
  await page.route("**/work/notifications?*", (route) => route.fulfill({ json: { notifications,
    unread: notifications.filter((n) => !n.read_at).length } }));
  await page.route("**/work/notifications/read", (route) => {
    const body = route.request().postDataJSON();
    read.push(body);
    notifications = notifications.map((n) => (!body.ids || body.ids.includes(n.id) ? { ...n, read_at: "now" } : n));
    return route.fulfill({ json: { read: 1 } });
  });
  await page.goto(`${url}/#/inbox`);
  await page.getByRole("heading", { name: "New for you (2)" }).waitFor();
  await page.getByText("Due soon", { exact: true }).waitFor();
  await page.getByRole("link", { name: "ines sent you 'Check the pump seal'. seal weeping" }).click();
  await page.waitForURL(/#\/cases\/41$/);
  await expectReports(read, 1);
  assert.deepEqual(read[0], { person: "olav", ids: [3] });
  await page.goto(`${url}/#/inbox`);
  await page.getByRole("heading", { name: "New for you (1)" }).waitFor();
  await page.getByRole("button", { name: "Mark all read" }).click();
  await page.getByRole("heading", { name: /New for you/ }).waitFor({ state: "detached" });
  assert.deepEqual(read[1], { person: "olav", ids: null });
});

test("a person chooses Slack with quiet hours on My work (#606)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "olav"));
  const inbox = { person: "olav", roles: ["operator"], mine: [], queue: [], counts: { mine: { total: 0 }, queue: { total: 0 } } };
  await page.route("**/work/inbox?*", (route) => route.fulfill({ json: inbox }));
  const saved = [];
  await page.route("**/work/people/olav/notify", (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON();
      if (!body.address) return route.fulfill({ status: 400, json: { detail: "slack needs env:NAME, the environment variable holding the webhook URL" } });
      saved.push(body);
      return route.fulfill({ json: body });
    }
    return route.fulfill({ json: { channel: "in_app", address: null, quiet_from: null, quiet_to: null } });
  });
  await page.goto(`${url}/#/inbox`);
  await page.getByRole("heading", { name: "How you are told" }).waitFor();
  await page.getByLabel("Also send to").selectOption("slack");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByRole("alert").filter({ hasText: "env:NAME" }).waitFor();
  await page.getByLabel("Webhook kept in").fill("env:OLAV_SLACK");
  await page.getByLabel("Quiet from").fill("22:00");
  await page.getByLabel("to", { exact: true }).fill("06:00");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByRole("status").filter({ hasText: "Saved." }).waitFor();
  assert.deepEqual(saved[0], { channel: "slack", address: "env:OLAV_SLACK", quiet_from: "22:00", quiet_to: "06:00", by: "olav" });
});

test("Ontology header buttons stay on one line and All views explains its hidden link names (#399)", async () => {
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true,
    user: { ...signedInUser, roles: ["administrator"], permissions: ["read", "configure"] } } }));
  await page.route("**/ontology", (route) => route.fulfill({ json: {
    kinds: [{ kind: "tag", label: "Tag", count: 40 }, { kind: "well", label: "Well", count: 12 }],
    relations: [{ src_kind: "tag", rel: "measures", dst_kind: "well", n: 30 }],
    groups: [], views: [], model: { kinds: [], relations: [] },
    needs_definition: { kinds: [], relations: [], not_allowed: [] },
  } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${url}/#/ontology`);
  const add = page.getByRole("button", { name: "Add link type", exact: true });
  await add.waitFor();
  const lineHeight = await add.evaluate((b) => parseFloat(getComputedStyle(b).lineHeight) || 20);
  const box = await add.boundingBox();
  assert.ok(box.height < lineHeight * 2, `Add link type wraps (${box.height}px)`);
  await page.getByText("Link names show when you point at a link or a kind", { exact: false }).waitFor();
});

test("sidebar and Jobs agree when the scheduler isn't running, and Jobs says how to start it (#325)", async () => {
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true,
    user: { ...signedInUser, roles: ["administrator"], permissions: ["read", "configure"] } } }));
  await page.goto(`${url}/#/jobs`);
  await page.locator("#sidebar .health").getByText("Scheduler history unavailable", { exact: true }).waitFor();
  await page.getByText("Scheduler history is unavailable in this deployment", { exact: false }).waitFor();
  await page.getByText("fuzer jobs", { exact: true }).waitFor();
});

test("a big flow network opens on its largest manifold, and can show the whole network (#300)", async () => {
  const nodes = [], edges = [];
  const add = (id, kind) => nodes.push({ id, kind, label: id });
  const link = (src, dst) => edges.push({ id: edges.length + 1, src, rel: "flows_to", dst, valid_from: null, valid_to: null, sources: [] });
  add("SEP-1", "separator"); add("SALES", "meter"); link("SEP-1", "SALES");
  for (const [m, n] of [["MAN-A", 40], ["MAN-B", 25]]) {
    add(m, "manifold"); link(m, "SEP-1");
    for (let i = 0; i < n; i++) { add(`${m}-W${i}`, "well"); link(`${m}-W${i}`, m); }
  }
  await page.route("**/network*", (route) => route.fulfill({ json: { nodes, edges, truncated: false } }));
  await page.goto(`${url}/#/network`);
  const show = page.getByRole("combobox", { name: "Show part of the network" });
  await show.waitFor();
  assert.equal(await show.inputValue(), "MAN-A");
  await show.selectOption("all");
  assert.equal(await show.inputValue(), "all");
  await show.selectOption("MAN-B");
  assert.equal(await show.inputValue(), "MAN-B");
});

test("on a phone the ontology graph takes the full width, its panel sits below, and the model reads as cards", async () => {
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "microsoft", configured: true,
    user: { ...signedInUser, roles: ["administrator"], permissions: ["read", "configure"] } } }));
  const kinds = ["tag", "equipment", "well", "manifold"].map((kind, i) => ({ kind, label: kind, count: 10 + i, sources: [], fields: [] }));
  await page.route("**/ontology", (route) => route.fulfill({ json: {
    kinds, relations: [{ src_kind: "tag", rel: "measures", dst_kind: "well", n: 30, sources: [] }],
    groups: [], views: [],
    model: { kinds: kinds.map((k) => ({ name: k.kind, label: k.label, description: "A long description of what one of these is", status: "core", count: k.count })),
      relations: [{ name: "measures", label: "measures", from_kinds: ["tag"], to_kinds: ["well"], status: "core", count: 30 }] },
    needs_definition: { kinds: [], relations: [], not_allowed: [] },
  } }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${url}/#/ontology`);
  const canvas = page.locator(".graph-canvas");
  await canvas.waitFor();
  const graph = await canvas.boundingBox(), panel = await page.locator(".graph-panel").boundingBox();
  assert.ok(graph.width > 300, `graph is squeezed to ${graph.width}px`);
  assert.ok(graph.height < 844 * 0.7, `graph (${graph.height}px) leaves no room to scroll the page past it`);
  assert.ok(panel.y >= graph.y + graph.height, "panel sits below the graph");
  await page.getByText("Kinds & link types", { exact: true }).click();
  const row = page.locator(".model-table tr").filter({ hasText: "A long description" }).first();
  const rowBox = await row.boundingBox();
  assert.ok(rowBox.width <= 390, `kind row is ${rowBox.width}px wide`);
  const scroll = await page.evaluate(() => document.documentElement.scrollWidth);
  assert.ok(scroll <= 390, `page scrolls sideways (${scroll}px)`);
});

test("one network draws several link types together, filtered by link type and kind of thing", async () => {
  const nodes = [
    { id: "W1", kind: "well", label: "W1" }, { id: "MAN", kind: "allocation_node", label: "MAN" },
    { id: "V-1", kind: "tag", label: "V-1" }, { id: "LG", kind: "allocation_node", label: "LG" },
  ];
  const edge = (id, src, rel, dst) => ({ id, src, rel, dst, valid_from: null, valid_to: null, sources: [] });
  const asked = [];
  await page.route("**/ontology/declared", (route) => route.fulfill({ json: { groups: [], kinds: [],
    views: [{ id: "flow", label: "Flow / allocation" }, { id: "physical", label: "Physical" }],
    relations: [
      { name: "flows_to", label: "flows to", description: "Production and allocation flow.", view: "flow" },
      { name: "lift_gas_to", label: "lift gas to", description: "Gas lift.", view: "flow" },
      { name: "part_of", label: "part of", description: "", view: "physical" },
    ] } }));
  await page.route("**/network*", (route) => {
    const rels = new URL(route.request().url()).searchParams.get("rels");
    asked.push(rels);
    const on = rels ? rels.split(",") : ["flows_to", "lift_gas_to"];
    const edges = [edge(1, "W1", "flows_to", "MAN"), edge(2, "MAN", "flows_to", "V-1"), edge(3, "LG", "lift_gas_to", "W1"),
      edge(4, "V-1", "part_of", "MAN")].filter((e) => on.includes(e.rel));
    return route.fulfill({ json: { rels: on, nodes, edges, truncated: false } });
  });
  await page.goto(`${url}/#/network`);
  const links = page.getByRole("group", { name: "Link types" });
  const flows = links.getByRole("button", { name: /flows to/ });
  const lift = links.getByRole("button", { name: /lift gas to/ });
  // The buttons draw once the ontology loads and show as pressed once the network arrives: wait for that.
  await links.locator('[aria-pressed="true"]', { hasText: /flows to/ }).waitFor();
  // No tabs: the default draws the ontology's whole flow view at once.
  assert.equal(asked[0], null);
  assert.equal(await flows.getAttribute("aria-pressed"), "true");
  assert.equal(await lift.getAttribute("aria-pressed"), "true");
  assert.equal(await page.getByRole("button", { name: "Allocation (EC)" }).count(), 0);

  await lift.click();
  await page.waitForFunction(() => location.hash.includes("rels=flows_to"));
  assert.equal(asked.at(-1), "flows_to");
  assert.equal(await lift.getAttribute("aria-pressed"), "false");

  // Any relation in the ontology can join the network.
  await page.getByText("More link types").click();
  await links.getByRole("button", { name: /part of/ }).click();
  await page.waitForFunction(() => location.hash.includes("rels=flows_to%2Cpart_of"));
  assert.equal(asked.at(-1), "flows_to,part_of");

  const kinds = page.getByRole("group", { name: "Kinds of thing" });
  const tags = kinds.getByRole("button", { name: /tag/ });
  await tags.click();
  await page.waitForFunction(() => location.hash.includes("hide=tag"));
  assert.equal(await tags.getAttribute("aria-pressed"), "false");

  for (const r of ["flows to", "part of"]) await links.getByRole("button", { name: new RegExp(r) }).click();
  await page.getByText("Pick at least one link type to draw.").waitFor();
});

test("a P&ID drawing in another project loads its image from that project", async () => {
  const id = "fz-document-0123456789ab";
  await page.addInitScript(() => localStorage.setItem("fuzer.project", "kestrel"));
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "local", configured: true, user: null,
    project: "okan", projects: ["okan", "kestrel"] } }));
  let imageQuery = null;
  await page.route(`**/documents/${id}/image*`, (route) => {
    imageQuery = new URL(route.request().url()).searchParams.get("fuzer_project");
    return route.fulfill({ status: 200, contentType: "image/png", body: Buffer.alloc(0) });
  });
  await page.route(`**/documents/${id}/drawing`, (route) => route.fulfill({ json: { sheet: "KRGP-E1-PID-001",
    format: "smart", title: "TRAIN E1", read_by: "smart file", image: `/documents/${id}/image`, width: 2200, height: 1500,
    marks: [], connections: [], findings: [] } }));
  await page.route(`**/entities/${id}/map`, (route) => route.fulfill({ json: { features: [], focus: null } }));
  await page.route(`**/entities/${id}`, (route) => route.fulfill({ json: { fuzer_id: id, kind: "document",
    label: "KRGP-E1-PID-001", golden: {}, values: {}, keys: [], edges: [], edge_counts: {},
    records: [{ source: "pid", record_type: "sheet", source_key: "KRGP-E1-PID-001", method: "singleton", confidence: 1 }] } }));
  await page.goto(`${url}/#/e/${id}`);
  await page.getByAltText("P&ID KRGP-E1-PID-001").waitFor();
  await page.waitForFunction(() => document.querySelector("img[alt^='P&ID']")?.complete);
  assert.equal(imageQuery, "kestrel");  // an <img> can't send the project header, so the URL names it
});

test("an overarching person's Inbox lists their work in other fields and opens a field by switching to it (#497)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "vera"));
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "local", configured: true, user: null,
    project: "okan", projects: ["okan", "kestrel"] } }));
  await page.route("**/work/inbox?*", (route) => route.fulfill({ json: { person: "vera", roles: ["engineer"], mine: [], queue: [],
    counts: { mine: { total: 0 }, queue: { total: 0 } } } }));
  const row = { task_id: 9, item_id: 3, title: "Sales gas compressor vibration", name: "Triage", type: "upset", assignee: null,
    role: "engineer", kind: "user", due_at: null, priority: null, bulk: [] };
  await page.route("**/work/inbox/fields?*", (route) => route.fulfill({ json: [
    { project: "okan", roles: ["engineer"], mine: [], queue: [] },
    { project: "kestrel", roles: ["engineer"], mine: [], queue: [row] },
  ] }));
  await page.goto(`${url}/#/inbox`);
  await page.getByText("In your other fields", { exact: false }).waitFor();
  await page.getByText("Sales gas compressor vibration", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Work in okan" }).count(), 0);  // the field this tab is in
  await page.getByRole("button", { name: "Work in kestrel" }).click();
  assert.equal(await page.evaluate(() => localStorage.getItem("fuzer.project")), "kestrel");
});

test("an upset's triage shows how it spread and merges a knock-on into one case", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "erin"));
  await page.route("**/work/tasks/42", (route) => route.fulfill({ json: { ...demoTask,
    task: { ...demoTask.task, element: "triage", name: "Triage", requires_human: false, assignee: "erin" },
    data: { upset_id: 13 } } }));
  await page.route("**/work/items/7", (route) => route.fulfill({ json: { ...demoTask.item, processes: [], events: [] } }));
  const related = (over) => ({ id: 14, signature: "a step change on OK-J12-01", start: "2024-11-05T12:00:00", end: null,
    lag_minutes: 20, hops: 5, seen_together: 2, role: "likely_knock_on", why: "", score: 0.4, status: "detected",
    detections: 4, units: 4, work_item_id: 8, case_title: "Step change on OK-J12-01", case_state: "open",
    merged_into: null, same_case: false, ...over });
  let merged = false;
  await page.route("**/upsets/13/view", (route) => route.fulfill({ json: {
    upset: { id: 13, start: "2024-11-05T11:40:00", detections: 5 }, signature: "an excursion on 67-FT-3001",
    timeline: [{ id: 1, start: "2024-11-05T11:40:00", end: null, unit: "67-FT-3001", point: "67-FT-3001.PV",
      detector: "excursions", key: "alarm:hi", magnitude: 56, finding: "High alarm" }],
    detections_shown: 1, by_unit: {}, spread: { spec: null, summary: null, title: null, note: "It reached one unit." },
    related: { related: merged ? [related({ same_case: true, work_item_id: 7 })] : [related(),
      related({ id: 20, signature: "a stop on 10-P-1001C", role: "coincident", hops: null, work_item_id: null })],
      considered: 2, window_hours: 12, basis: "Time and links are leads, not causes." },
    merged: merged ? [{ id: 14, signature: "a step change on OK-J12-01", start: "2024-11-05T12:00:00" }] : [] } }));
  const posts = [];
  await page.route("**/upsets/14/merge", (route) => { posts.push(route.request().postDataJSON()); merged = true;
    return route.fulfill({ json: { upset: 14, into: 13, item_id: 7, cancelled_case: 8, reason: "" } }); });
  await page.goto(`${url}/#/inbox/task/42`);
  const panel = page.locator(".upset-panel");
  await panel.getByText("It reached one unit.").waitFor();
  await panel.getByText("High alarm").waitFor();
  const row = panel.locator("li", { hasText: "a step change on OK-J12-01" });
  await row.getByText("Likely knock-on").waitFor();
  await row.getByText("20 min later", { exact: false }).waitFor();
  await row.getByText("seen together 2× in 90 days", { exact: false }).waitFor();
  assert.equal(await panel.getByText("a stop on 10-P-1001C").count(), 0);  // coincident and caseless: not worth a look
  await row.getByRole("button", { name: "Bring into this case" }).click();
  await row.getByRole("button", { name: "Confirm Bring into this case" }).click();
  await panel.getByText("Merged: case #8 is now part of this one.").waitFor();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].into, 13);
  await panel.getByText("Merged into this case: a step change on OK-J12-01.").waitFor();
  await panel.getByText("Already on this case.").waitFor();
});

test("Routing lists each type of work, flags an empty lane and dry-runs a dispatch (#507)", async () => {
  const route = { type: "short_gas_day", label: "Short gas day", description: "Flow under 23 hours.", declared_in: "source pack",
    source_system: "measurement", joins: true, join_hours: 12, starts: "queue", process: "queue_item", role: "production_engineer",
    case_type: "short_gas_day", audience: ["ioc"], close_case_on_clear: true, due: null };
  const sap = { ...route, type: "sap_notification", label: "SAP notification", description: "", declared_in: "code",
    source_system: null, process: null, role: null, starts: "dispatch", case_type: "sap_notification",
    due: { by: "priority", hours: { "1": 24, "2": 72 }, setting: "intake.sap_notifications.due_hours", from: "raised", applies: "when dispatched" } };
  const step = { element: "work", step: "Work the case", kind: "person", role: "production_engineer", sla: "PT8H", sla_from: "role",
    escalation: ["ops_lead"], escalation_from: "role", assignment: null, form: null, requires_human: false, fallback_role: null, holders: [] };
  const asked = [];
  await page.route("**/routing", (r) => r.fulfill({ json: [sap, route] }));
  await page.route("**/routing/short_gas_day/explain*", (r) => {
    const q = new URL(r.request().url()).searchParams;
    asked.push({ refs: q.getAll("refs"), to: q.get("to") });
    return r.fulfill({ json: { type: "short_gas_day", route, outcome: q.get("to") ? "dispatched" : "queue",
      summary: q.get("to") ? "Opens a short gas day case in the production_engineer lane and hands it to riley." : "Opens a short gas day case in the production_engineer queue, which nobody holds.",
      matched: q.getAll("refs").length ? [{ id: "fz-meter-1", label: "Meter 7", kind: "meter" }] : [], unmatched: [], unmatched_note: null,
      updates: null, joins: null, due: null, queue: { role: "production_engineer", assignee: null, holders: [] },
      process: { key: "queue_item", published: false, steps: [step] } } });
  });
  await page.goto(`${url}/#/routing`);
  await page.getByRole("heading", { name: "Routing", exact: true }).waitFor();
  assert.equal(await page.getByRole("navigation", { name: "Settings pages" }).getByRole("link", { name: "Routing" }).getAttribute("aria-current"), "page");
  await page.getByText("1: 24 h, 2: 72 h").waitFor();  // SAP's due policy, from its setting
  await page.getByText("waits for the IOC to dispatch").waitFor();
  await page.getByRole("link", { name: "Short gas day" }).click();
  await page.waitForURL(/type=short_gas_day/);
  await page.getByText(/which nobody holds/).waitFor();
  await page.getByRole("alert").getByText(/Nobody holds production_engineer/).waitFor();
  await page.getByLabel("Refs").fill("tag:FT-7");
  await page.getByLabel("Dispatch to").fill("riley");
  await page.getByRole("button", { name: "Try it" }).click();
  await page.getByText(/hands it to riley/).waitFor();
  await page.getByText("Matches Meter 7.").waitFor();
  assert.deepEqual(asked.at(-1), { refs: ["tag:FT-7"], to: "riley" });
});

// ---- P1.1: one finding contract, one evidence component --------------------------------------------------------

function finding(origin, method, items, more = {}) {
  return { detector: "test", origin, origin_label: "", method, standard: null, start: null, end: null, confidence: null,
    items, checks: [], subjects: [], value_usd: null, ...more };
}
const text = (label, value) => ({ label, value, kind: "text" });
const number = (label, value, unit) => ({ label, value, kind: "number", ...(unit ? { unit } : {}) });
const named = (label, text) => ({ label, value: "fz-equipment-0123456789ab", kind: "asset", text });

// One finding per work type, in the shape the API returns (core.findings.shown).
const FINDINGS = {
  upset: finding("deterministic", "Detections grouped into one upset by time and plant topology", [
    named("First seen on", "30-PT-2002 inlet pressure"), { label: "Started", value: "2025-12-24T00:00", kind: "time" },
    { label: "Still going", value: true, kind: "flag" }, number("Peak", 90, "barg"), number("Assets affected", 3),
    number("Detections", 6)], { start: "2025-12-24T00:00", value_usd: 42000 }),
  iow_breach: finding("deterministic", "Time outside an integrity operating window, past its allowed time", [
    text("Window", "IOW-7"), named("Measured on", "30-PT-2002 inlet pressure"), text("Level", "critical"),
    number("Limit", 85, "barg"), number("Peak", 91.2, "barg"), number("Time outside", 3.5, "h")],
    { standard: "API RP 584", start: "2025-12-30T04:00", end: "2025-12-30T07:30" }),
  machine_trip: finding("deterministic", "The first point to move before the trip names the cause family", [
    { label: "Tripped", value: "2024-03-14T03:20", kind: "time" }, named("Likely source", "20-K-2200B 2nd stage compressor"),
    text("Likely cause", "high vibration"), number("First out led the trip by", 42, "min")],
    { standard: "ISO 13379-1 symptom-to-cause", checks: ["Check the bearing temperatures", "Confirm lube oil pressure"] }),
  machine_health: finding("deterministic", "Rotating equipment fleet screen", [
    text("Status", "needs action"), text("Flagged for", "vibration in zone D"),
    text("Recommended first", "Plan a bearing inspection before the next start")],
    { standard: "ISO 20816-3 vibration zones; API 670 protection channels; ISO 14224 rotating classes" }),
  records_gap: finding("deterministic", "Downtime records compared with the operator log and notifications", [
    { label: "When", value: "2025-12-27T02:40", kind: "time" }, text("Missing", "log entry, notification"),
    number("Lost oil", 120, "Sm³")]),
  sap_notification: finding("external", "An open notification with no work order", [
    text("Notification", "10004711"), text("Notification type", "malfunction report (M2)"),
    text("Status", "outstanding (OSNO)"), text("Failure mode", "Vibration (VIB)")]),
  source_issue: finding("deterministic", "Source health checks", [
    text("Dataset", "orders"), text("System", "sap"), text("Problem", "failed to load")]),
  scada_alarm: finding("external", null, [text("Alarm priority", "high"), number("Drips per min", 12)]),
  agent_watch: finding("agentic", "Agentic watch", [text("Seen", "Suction pressure sagging while speed holds")],
    { confidence: 0.62 }),
};

for (const [type, found] of Object.entries(FINDINGS)) {
  test(`a ${type.replace(/_/g, " ")} finding reads the same on the phone, the task and the case, with no raw ids or keys (P1.1)`, async () => {
    await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "fred"));
    const source = { id: 7, type, key: "k-1", title: `A ${type} title`, source: "test", state: "open", value_usd: found.value_usd,
      first_seen: "2026-11-08T06:00:00Z", last_seen: "2026-11-08T07:00:00Z", joined_to: null, subjects: [], label: type, finding: found };
    const job = { ...source, priority: null, unmatched: [], item_id: 7, case_state: "open", assignee: "fred", role: null,
      raised_count: 1, due: null, task_id: null, task_name: null, assets: [], checklist: found.checks, sources: [],
      history: [], field: [], queued: false, sent: null };
    await page.route("**/exceptions/field?*", (route) => route.fulfill({ json: { person: "fred", jobs: [job], coming_up: [] } }));
    await page.route("**/work/tasks/42", (route) => route.fulfill({ json: { ...demoTask, sources: [source] } }));
    await page.route("**/work/items/7", (route) => route.fulfill({ json: { ...demoTask.item, processes: [], events: [], sources: [source] } }));
    const read = async (path, width) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`${url}/#${path}`);
      const box = page.locator(".finding dl").first();
      await box.waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${path} fits ${width}px`);
      return (await box.innerText()).replace(/\s+/g, " ").trim();
    };
    const phone = await read("/inbox", 390);
    const task = await read("/inbox/task/42", 1280);
    const taskOnPhone = await read("/inbox/task/42", 390);
    const onCase = await read("/cases/7", 1280);
    assert.equal(task, phone);
    assert.equal(taskOnPhone, phone);
    assert.equal(onCase, phone);
    assert.match(phone, new RegExp(`^Found by ${{ deterministic: "Rule", agentic: "Agent", external: "Another system" }[found.origin]}`));
    for (const item of found.items) assert.ok(phone.includes(item.label), `${item.label} is shown`);
    assert.doesNotMatch(phone, /fz-[a-z_]+-[0-9a-f]{6,}|\b[a-z]+_[a-z_]+\b|\d{4}-\d{2}-\d{2}T\d{2}/);  // no ids, keys or raw timestamps
    if (found.standard) assert.ok(phone.includes(found.standard));
    if (found.checks.length) await page.getByText(found.checks[0]).waitFor();  // the checks before the work, on the case
  });
}

// The jobs the loop eval (P1.7) handed a field operator on the Okan bundle, one per type the field sees, written by
// `FUZER_WRITE_FIXTURES=1 uv run pytest tests/evals/test_loop.py -k okan` and checked against the API's shape there.
const LOOP_JOBS = JSON.parse(readFileSync(new URL("./fixtures/loop-field-jobs.json", import.meta.url), "utf8"));

for (const [type, job] of Object.entries(LOOP_JOBS)) {
  test(`the loop's ${type.replace(/_/g, " ")} field job is usable on a 390 px phone (P1.7)`, async () => {
    await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "loop-field"));
    await page.route("**/exceptions/field?*", (route) => route.fulfill({ json: { person: "loop-field", jobs: [job], coming_up: [] } }));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${url}/#/inbox`);
    const card = page.locator(".field-job").first();
    await card.getByRole("heading", { name: job.title }).waitFor();
    await card.getByText(job.sent.reason).waitFor();  // what the IOC asked
    for (const a of job.assets) await card.getByText(a.label ?? a.name, { exact: false }).first().waitFor();
    assert.doesNotMatch(await card.innerText(), /\bfz-[\w-]+/, "no raw ids on the job");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "fits 390 px");
    for (const name of ["On site", "Done"]) {
      const box = await card.getByRole("button", { name, exact: true }).boundingBox();
      assert.ok(box && box.height >= 44 && box.width >= 44, `${name} is a 44 px tap target, got ${box?.width}x${box?.height}`);
    }
  });
}

// P0.2c (#578): every page has its empty, loading and failed states. A page never goes blank or crashes: with empty data
// it shows its heading, while its reads are held it says it is loading, and when they fail it says so with a retry.
const EVERY_PAGE = ["#/", "#/browse", "#/network", "#/ontology", "#/downtime", "#/ask", "#/incidents", "#/reports",
  "#/lessons", "#/studies", "#/exceptions", "#/exceptions?list=ranked", "#/exceptions?list=progress", "#/inbox", "#/plan",
  "#/masterdata", "#/review", "#/handover", "#/teams", "#/processes", "#/sources", "#/sources?view=systems", "#/jobs",
  "#/settings", "#/users", "#/routing", "#/demo"];
const SHELL = new Set(["/auth/me"]);

for (const state of ["empty", "loading", "failed"]) {
  test(`every page shows its ${state} state (#578)`, async () => {
    await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ann"));
    if (state === "empty") {
      // what the server answers for a facility with nothing in it, where that is not a bare list
      const empties = {
        "/ontology": { kinds: [], relations: [], groups: [], views: [], model: { kinds: [], relations: [] },
          needs_definition: { kinds: [], relations: [], not_allowed: [] } },
        "/network": { nodes: [], edges: [], truncated: false, rels: [] },
        "/feedback/briefs": { incidents: 0, rated: 0, ratings: 0, useful_share: null, items: [] },
        "/work/workload": { people: [], roles: {}, agents: [], agent_failures: [] },
      };
      await page.route("**/*", (route) => {
        const path = new URL(route.request().url()).pathname;
        return path in empties ? route.fulfill({ json: empties[path] }) : route.fallback();
      });
    } else {
      await page.route("**/*", async (route) => {
        const path = new URL(route.request().url()).pathname;
        if (path.startsWith("/assets/") || path === "/" || /\.(svg|png|woff2?)$/.test(path) || SHELL.has(path)) return route.fallback();
        if (state === "failed") return route.fulfill({ status: 503, json: { detail: "Service unavailable" } });
        // loading: hold the read; the page must say it is loading meanwhile
      });
    }
    const crashed = [];
    for (const to of EVERY_PAGE) {
      await page.goto(`${url}/${to}`);
      await page.reload();
      const main = page.locator("#main-content");
      if (state === "loading") {
        await main.getByText(/Loading|Checking/).first().waitFor({ timeout: 5000 }).catch(() => crashed.push(`${to}: no loading status`));
      } else if (state === "failed") {
        await main.getByRole("alert").first().waitFor({ timeout: 5000 }).catch(() => crashed.push(`${to}: no error shown`));
      } else {
        await page.getByRole("heading", { level: 1 }).first().waitFor({ timeout: 5000 }).catch(() => crashed.push(`${to}: no heading`));
      }
      if (await page.getByText("This page could not be displayed").count()) {
        crashed.push(`${to}: crashed: ${await page.locator("#main-content .error, main .error").first().textContent().catch(() => "")}`);
      }
    }
    errors.length = 0;
    assert.deepEqual(crashed, []);
  });
}

// P5.3a (#641): the field's pages on a 390 px phone are rows and single-column forms, with every control a 44 px
// tap target and nothing wider than the screen: My work, the job, the task and the case.
test("My work, the job, the task and the case fit a 390 px phone with 44 px targets (#641)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "fred"));
  const source = { id: 7, type: "sap_notification", key: "k-1", title: "Pump P-101 vibration", source: "sap", state: "open",
    first_seen: "2026-11-08T06:00:00Z", last_seen: "2026-11-08T07:00:00Z", joined_to: null, subjects: [], label: "SAP notification",
    finding: FINDINGS.sap_notification };
  const job = { ...source, priority: null, unmatched: [], item_id: 7, case_state: "open", assignee: "fred", role: null,
    raised_count: 1, due: "2026-11-12T12:00:00Z", task_id: null, task_name: null,
    assets: [{ fuzer_id: "fz-p-101", label: "P-101 export pump", kind: "pump", lat: 1, lon: 2 }], checklist: [], sources: [],
    history: [], field: [{ at: "2026-11-12T06:00:00Z", by: "fred", status: "on_site", note: null }], queued: false,
    sent: { reason: "Check the drive-end bearing", by: "ines", at: "2026-11-12T05:50:00Z" } };
  await page.route("**/exceptions/field?*", (route) => route.fulfill({ json: { person: "fred", jobs: [job], coming_up: [] } }));
  const row = { ...demoTask.task, type: "upset", title: demoTask.item.title, subjects: [], bulk: [], priority: 2, escalated_at: null,
    error: null, created_at: "2026-11-12T05:00:00Z", overdue: false, claimed_by: null };
  await page.route("**/work/inbox?*", (route) => route.fulfill({ json: { person: "fred", roles: ["engineer"],
    mine: [{ ...row, item_id: 8, assignee: "fred", due_at: "2026-11-12T12:00:00Z" }],
    queue: [{ ...row, item_id: 9, task_id: 43, title: "Another case in the queue", due_at: null }],
    counts: { mine: { total: 1 }, queue: { total: 1 } } } }));
  await page.route("**/work/tasks/42", (route) => route.fulfill({ json: { ...demoTask, sources: [source] } }));
  await page.route("**/work/items/7", (route) => route.fulfill({ json: { ...demoTask.item, processes: [], events: [],
    sources: [source], due: null, closed_at: null, created_at: "2026-11-12T05:00:00Z" } }));
  await page.setViewportSize({ width: 390, height: 844 });
  const small = async (path, ready) => {
    await page.goto(`${url}/#${path}`);
    await page.getByRole("heading", { name: ready }).first().waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${path} fits 390 px`);
    return page.evaluate(() => [...document.querySelectorAll("#main-content button, #main-content input:not([type=hidden]), "
      + "#main-content select, #main-content textarea, #main-content a.ui-button")]
      .filter((el) => el.offsetParent !== null)
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter(({ r }) => r.height < 44 || r.width < 44)
      .map(({ el, r }) => `${el.tagName.toLowerCase()} "${(el.getAttribute("aria-label") || el.textContent || el.getAttribute("placeholder") || "").trim().slice(0, 30)}" ${Math.round(r.width)}x${Math.round(r.height)}`));
  };
  const found = {
    "My work": await small("/inbox", "Pump P-101 vibration"),
    task: await small("/inbox/task/42", "Review recorded evidence"),
    case: await small("/cases/7", "Loaded shared case"),
  };
  assert.deepEqual(found, { "My work": [], task: [], case: [] });
});

// P5.3b (#642): Done with a reading and a photo from the phone; the IOC's "From the field" box shows both.
test("a reading and a photo taken on the phone reach the IOC's From the field box (#642)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "fred"));
  const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
  const job = { id: 7, type: "leak", key: "k", title: "Leak at P-101", source: "walk", state: "open", first_seen: "2026-11-12T05:00:00Z",
    last_seen: "2026-11-12T05:00:00Z", joined_to: null, subjects: [], label: "Leak", finding: FINDINGS.scada_alarm, priority: null,
    unmatched: [], item_id: 7, case_state: "open", assignee: "fred", role: null, raised_count: 1, due: null, task_id: null,
    task_name: null, assets: [], checklist: [], sources: [], history: [], field: [], queued: false,
    sent: { reason: "Look at the gland", by: "ines", at: "2026-11-12T05:10:00Z" } };
  const sent = [];
  await page.route("**/exceptions/field?*", (route) => route.fulfill({ json: { person: "fred", jobs: [job], coming_up: [] } }));
  await page.route("**/exceptions/7/report", (route) => { sent.push(route.request().postDataJSON()); return route.fulfill({ json: job }); });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${url}/#/inbox`);
  const card = page.locator(".field-job").first();
  await card.getByLabel("What was read").fill("Gland temperature");
  await card.getByLabel("Reading", { exact: true }).fill("72.5");
  await card.getByLabel("Unit").fill("°C");
  await card.locator('input[type="file"]').setInputFiles({ name: "gland.png", mimeType: "image/png", buffer: PNG });
  await card.getByRole("img", { name: "Photo 1 to send" }).waitFor();
  await card.getByRole("button", { name: "Done", exact: true }).click();
  for (let i = 0; i < 50 && !sent.length; i++) await page.waitForTimeout(50);
  assert.equal(sent[0].status, "done");
  assert.deepEqual(sent[0].reading, { value: 72.5, unit: "°C", what: "Gland temperature" });
  assert.equal(sent[0].photos.length, 1);
  assert.match(sent[0].photos[0], /^data:image\/jpeg;base64,/);  // shrunk on the phone

  const said = { id: 99, kind: "commented", actor: "fred", element: null, at: "2026-11-12T06:00:00Z",
    data: { text: "Field: Done: gland repacked (Gland temperature 72.5 °C; 1 photo)",
      attachments: [{ kind: "photo", exception_id: 7, photo_id: 3 }] } };
  await page.route("**/work/tasks/42", (route) => route.fulfill({ json: { ...demoTask,
    evidence: { ...demoTask.evidence, comments: [said] } } }));
  await page.route("**/work/items/7", (route) => route.fulfill({ json: { ...demoTask.item, processes: [], events: [], sources: [] } }));
  await page.route("**/exceptions/7/photos/3", (route) => route.fulfill({ body: PNG, contentType: "image/png" }));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`${url}/#/inbox/task/42`);
  const box = page.locator(".from-field");
  await box.getByText("Done: gland repacked (Gland temperature 72.5 °C; 1 photo)").waitFor();
  await box.getByRole("img", { name: "Photo from the field" }).waitFor();
});

// P5.3c (#643): without signal the jobs still read from the phone, a report waits in the outbox, and it is sent
// once, with the time it was made, when the signal returns.
test("a report made without signal waits on the phone and is sent once with its time when signal returns (#643)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "fred"));
  let online = true;
  const posts = [];
  const job = { id: 7, type: "leak", key: "k", title: "Leak at P-101", source: "walk", state: "open", first_seen: "2026-11-12T05:00:00Z",
    last_seen: "2026-11-12T05:00:00Z", joined_to: null, subjects: [], label: "Leak", finding: FINDINGS.scada_alarm, priority: null,
    unmatched: [], item_id: 7, case_state: "open", assignee: "fred", role: null, raised_count: 1, due: null, task_id: null,
    task_name: null, assets: [{ fuzer_id: "fz-p-101", label: "P-101 export pump", kind: "pump" }], checklist: [], sources: [],
    history: [], field: [], queued: false, sent: { reason: "Look at the gland", by: "ines", at: "2026-11-12T05:10:00Z" } };
  const live = (json) => (route) => (online ? route.fulfill({ json }) : route.abort("internetdisconnected"));
  await page.route("**/exceptions/field?*", live({ person: "fred", jobs: [job], coming_up: [] }));
  await page.route("**/work/inbox?*", live({ person: "fred", roles: ["operator"], mine: [], queue: [],
    counts: { mine: { total: 0 }, queue: { total: 0 } } }));
  await page.route("**/exceptions/7/report", (route) => {
    if (!online) return route.abort("internetdisconnected");
    posts.push({ ...route.request().postDataJSON(), received: Date.now() });
    return route.fulfill({ json: job });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${url}/#/inbox`);
  await page.locator(".field-job").getByText("Look at the gland").waitFor();  // seen once with signal: kept

  online = false;
  await page.reload();
  await page.getByText("No signal: these are your jobs as kept on this phone", { exact: false }).waitFor();
  const card = page.locator(".field-job").first();
  await card.getByText("Look at the gland").waitFor();
  await card.getByText("P-101 export pump").waitFor();
  const before = Date.now();
  await card.getByLabel(/Note for the IOC/).fill("Gland weeping, 12 drips a minute");
  await card.getByRole("button", { name: "Add note", exact: true }).click();
  await card.getByText("No signal: kept on this phone", { exact: false }).waitFor();
  await page.getByRole("heading", { name: "1 report waiting to send" }).waitFor();
  assert.equal(posts.length, 0);
  await page.waitForTimeout(1500);  // still no signal

  online = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await page.getByRole("heading", { name: "1 report waiting to send" }).waitFor({ state: "detached" });
  await page.evaluate(() => window.dispatchEvent(new Event("online")));  // a second nudge sends nothing more
  await page.waitForTimeout(200);
  assert.equal(posts.length, 1);
  assert.equal(posts[0].note, "Gland weeping, 12 drips a minute");
  assert.ok(posts[0].client_id, "carries its id, so a resend is applied once");
  const made = Date.parse(posts[0].at);
  assert.ok(made >= before - 1000 && made <= posts[0].received - 1000, "keeps the time it was made, not when it was sent");
});

// P5.1a (#649): an ops lead covering two fields sees one ranked list on Home, overdue first whichever field it is in,
// with what changed since they last looked, and reaches an item in another field in one click from Home.
test("Home ranks an ops lead's work across both fields and opens one in the other field in one click (#649)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ines"));
  await page.route("**/auth/me", (route) => route.fulfill({ json: { mode: "local", configured: true, user: null,
    project: "okan", projects: ["okan", "kestrel"] } }));
  const row = (over) => ({ task_id: 9, item_id: 3, element: "triage", title: "Okan separator level", name: "Triage", type: "upset",
    assignee: null, role: "ops_lead", kind: "user", requires_human: true, due_at: "2099-01-01T00:00:00Z", escalated_at: null,
    error: null, created_at: "2026-11-12T05:00:00Z", overdue: false, claimed_by: null, priority: null, subjects: [],
    process_id: 1, process_key: "upset", bulk: [], ...over });
  const okan = row({});
  const kestrel = row({ task_id: 77, item_id: 21, title: "Kestrel compressor trip", overdue: true, due_at: "2026-11-11T00:00:00Z" });
  await page.route("**/work/inbox?*", (route) => route.fulfill({ json: { person: "ines", roles: ["ops_lead"], mine: [], queue: [okan],
    counts: { mine: { total: 0 }, queue: { total: 1 } } } }));
  await page.route("**/work/inbox/fields?*", (route) => route.fulfill({ json: [
    { project: "okan", roles: ["ops_lead"], mine: [], queue: [okan] },
    { project: "kestrel", roles: ["ops_lead"], mine: [], queue: [kestrel] },
  ] }));
  let marked = null;
  await page.route("**/work/home?*", (route) => {
    marked = new URL(route.request().url()).searchParams.get("mark");
    return route.fulfill({ json: { person: "ines", seen_at: "2026-11-12T04:00:00Z", changes: [
      { id: 5, item_id: 3, kind: "commented", actor: "fred", at: "2026-11-12T05:30:00Z", data: { text: "gland weeping" },
        title: "Okan separator level", type: "upset", state: "open" }] } });
  });
  await page.route("**/work/tasks/77", (route) => route.fulfill({ json: demoTask }));
  await page.goto(`${url}/#/`);
  const shift = page.locator("section.home-card").filter({ has: page.getByRole("heading", { name: "My work", exact: true }) });
  await shift.getByText("1 more in your other field, ranked below").waitFor();
  const titles = await shift.locator(".home-shift > li .work-title, .home-shift > li button").allInnerTexts();
  assert.deepEqual(titles, ["Kestrel compressor trip", "Okan separator level"]);  // overdue first, whichever field
  await shift.getByText("gland weeping", { exact: false }).waitFor();
  assert.equal(marked, "true");  // this look is the new last look
  await shift.getByRole("button", { name: "Kestrel compressor trip" }).click();
  await page.waitForURL(/#\/inbox\/task\/77$/);
  assert.equal(await page.evaluate(() => localStorage.getItem("fuzer.project")), "kestrel");
});

// P5.1b (#650): a person watches a case from its page, and changes on what they watch show on Home under Watching,
// apart from their own work and never assigned to them.
test("watching a case shows its changes on Home under Watching, not as assigned work (#650)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "fred"));
  await page.route("**/work/items/7", (route) => route.fulfill({ json: { ...demoTask.item, processes: [], events: [] } }));
  let watched = [];
  const posts = [];
  await page.route("**/work/watch*", (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push(body);
      watched = body.on ? [...watched, body.ref] : watched.filter((r) => r !== body.ref);
      return route.fulfill({ json: { person: "fred", ref: body.ref, watching: body.on } });
    }
    return route.fulfill({ json: watched });
  });
  await page.goto(`${url}/#/cases/7`);
  await page.getByRole("button", { name: "Watch", exact: true }).click();
  await page.getByRole("button", { name: "Watching", exact: true }).waitFor();
  assert.deepEqual(posts, [{ person: "fred", ref: "case:7", on: true }]);
  assert.equal(await page.getByRole("button", { name: "Watching", exact: true }).getAttribute("aria-pressed"), "true");

  await page.route("**/work/home?*", (route) => route.fulfill({ json: { person: "fred", seen_at: "2026-11-12T04:00:00Z",
    watching: ["case:7"], changes: [
      { id: 6, item_id: 7, kind: "commented", actor: "pat", at: "2026-11-12T05:30:00Z", data: { text: "parts ordered" },
        title: "Train B vibration", type: "upset", state: "open", watching: true }] } }));
  await page.goto(`${url}/#/`);
  const shift = page.locator("section.home-card").filter({ has: page.getByRole("heading", { name: "My work", exact: true }) });
  await shift.getByRole("heading", { name: /^Watching/ }).waitFor();
  await shift.getByText("parts ordered", { exact: false }).waitFor();
  await shift.getByText("Nothing new on your work.").waitFor();  // watching is not her own work
});

// P5.1c (#651): an ops lead saves All work's filters as a view for their role, and another ops lead opens it from
// the picker with the same view and filters.
test("a view saved on All work for a role opens for everyone in that role with its filters (#651)", async () => {
  await page.addInitScript(() => { if (!localStorage.getItem("fuzer.reviewer")) localStorage.setItem("fuzer.reviewer", "ines"); });
  await page.route("**/exceptions?*", (route) => route.fulfill({ json: [] }));
  await page.route("**/work/people", (route) => route.fulfill({ json: [{ name: "ines", roles: ["ops_lead"] },
    { name: "olu", roles: ["ops_lead"] }] }));
  const saved = [];
  const asked = [];
  await page.route("**/work/views*", (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      const v = { id: saved.length + 1, role: body.role, name: body.name, list: body.list, filters: body.filters,
        created_by: body.person, created_at: "2026-11-12T06:00:00Z" };
      saved.push(v);
      return route.fulfill({ json: v });
    }
    asked.push(new URL(route.request().url()).searchParams.get("person"));
    return route.fulfill({ json: saved });
  });
  await page.goto(`${url}/#/exceptions?list=back&type=upset`);
  await page.getByRole("button", { name: "Save this view for a role", exact: true }).click();
  await page.getByLabel("View name").fill("Upsets back from the field");
  await page.getByRole("button", { name: "Save for every ops lead", exact: true }).click();
  const picker = page.getByLabel("Saved views");
  await picker.getByRole("option", { name: "Upsets back from the field (ops lead)" }).waitFor({ state: "attached" });
  assert.deepEqual(saved.map((v) => [v.role, v.list, v.filters, v.created_by]),
    [["ops_lead", "back", { type: "upset" }, "ines"]]);

  await page.evaluate(() => localStorage.setItem("fuzer.reviewer", "olu"));
  await page.goto(`${url}/#/exceptions`);
  await page.reload();  // a fresh load as the other ops lead
  await page.getByLabel("Saved views").selectOption({ label: "Upsets back from the field (ops lead)" });
  await page.waitForURL(/list=back/);
  assert.match(page.url(), /type=upset/);
  assert.equal(asked.at(-1), "olu");  // the other ops lead's own picker
});

test("an uncertain measurement role is confirmed or corrected from Review, and stays after rebuilds (P1.4b)", async () => {
  const item = (id, proposed, label) => ({ id, kind: "role", subject: `fz-ts-${id}`, status: "open", label: null, payload: {
    point_id: `fz-ts-${id}`, point: `KRGP:D1.TT400${id}.PV`, description: "Amine Reboiler Temperature", uom: "degC",
    letters: "TT", asset: "fz-tag-1", asset_label: "D1-E-4006", proposed, proposed_label: label, confidence: 0.7,
    basis: ["letters TT: temperature", "units degC: temperature", "on HE: usually outlet temperature"],
    choices: [{ key: "outlet_temperature", label: "Outlet temperature" }, { key: "temperature", label: "Process temperature" },
      { key: "pressure", label: "Process pressure" }] } });
  await page.route("**/review?*", (route) => route.fulfill({ json: [item(6, "outlet_temperature", "Outlet temperature"),
    item(7, "outlet_temperature", "Outlet temperature")] }));
  const sent = {};
  await page.route("**/review/*", (route) => {
    const id = route.request().url().split("/").pop();
    sent[id] = route.request().postDataJSON();
    return route.fulfill({ json: { decided: Number(id) } });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${url}/#/masterdata?tab=questions&kind=role`);
  const first = page.locator(".review", { hasText: "KRGP:D1.TT4006.PV" });
  await first.getByText("on HE: usually outlet temperature").waitFor();
  await first.getByText("70% sure").waitFor();
  assert.equal(await first.getByRole("link", { name: "D1-E-4006" }).count(), 1);
  assert.equal(await first.getByRole("button", { name: "Use this role" }).isDisabled(), true);  // nothing new picked
  await first.getByLabel("Role").selectOption("temperature");
  await first.getByRole("button", { name: "Use this role" }).click();
  await page.getByRole("button", { name: "Confirm decision", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "is process temperature from now on" }).waitFor();
  assert.deepEqual([sent["6"].decision, sent["6"].value], ["choose", "temperature"]);
  const second = page.locator(".review", { hasText: "KRGP:D1.TT4007.PV" });
  await second.getByRole("button", { name: "Confirm outlet temperature" }).click();
  await page.getByRole("button", { name: "Confirm decision", exact: true }).click();
  await page.getByRole("status").filter({ hasText: "is outlet temperature from now on" }).waitFor();
  assert.equal(sent["7"].decision, "accept");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

// P6.1a (#655): closing a case on a phone says what it was in the ISO 14224 terms, pre-filled from the case's words.
test("a case closes out on a phone with what it was, its failure mode, cause and action pre-filled (#655)", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "fred"));
  let item = { ...demoTask.item, state: "open", closed_at: null, processes: [], events: [] };
  await page.route("**/work/items/7", (route) => route.fulfill({ json: item }));
  const vocabulary = {
    dispositions: { real: "A real problem", false_alarm: "False alarm", duplicate: "Duplicate", data_problem: "Data problem" },
    outcomes: { fixed: "Fixed", monitoring: "Being monitored" },
    failure_modes: { VIB: "Vibration", ELP: "External leakage - process medium" },
    mechanisms: { "2.4": "Wear" }, causes: { "1": "Design-related causes", "3": "Failure related to operation / maintenance" },
    activities: { "1": "Replace", "2": "Repair" },
  };
  let record = null;
  const posts = [];
  await page.route("**/work/items/7/closeout", (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      posts.push(body);
      record = { item_id: 7, ...body, mechanism: null, duplicate_of: null, at: "2026-11-12T06:00:00Z" };
      item = { ...item, state: "done", closed_at: "2026-11-12T06:00:00Z" };
      return route.fulfill({ json: record });
    }
    return route.fulfill({ json: { item_id: 7, record, vocabulary, repeats: [],
      suggestion: { from: "words", disposition: "real", failure_mode: "VIB", activity: "1" } } });
  });
  await page.goto(`${url}/#/cases/7`);
  const started = Date.now();
  await page.getByRole("button", { name: "Close out", exact: true }).click();
  await page.getByText("Filled in from what the case says.", { exact: false }).waitFor();
  assert.equal(await page.getByLabel("Failure mode").inputValue(), "VIB");
  assert.equal(await page.getByLabel("Action taken").inputValue(), "1");
  assert.equal(await page.getByRole("button", { name: "Close the case" }).isDisabled(), true);  // the cause is still to say
  await page.getByLabel("Cause").selectOption("3");
  await page.getByLabel("Outcome").selectOption("fixed");
  const box = await page.getByLabel("Cause").boundingBox();
  assert.ok(box.height >= 44, `a 44 px target, got ${box.height}`);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.getByRole("button", { name: "Close the case" }).click();
  await page.getByText("A real problem: Vibration, cause failure related to operation / maintenance, replace; fixed").waitFor();
  assert.ok(Date.now() - started < 60_000);
  assert.equal(posts.length, 1);
  assert.deepEqual([posts[0].by, posts[0].disposition, posts[0].failure_mode, posts[0].cause, posts[0].activity, posts[0].outcome],
    ["fred", "real", "VIB", "3", "1", "fixed"]);
  assert.deepEqual(posts[0].suggested, { from: "words", disposition: "real", failure_mode: "VIB", activity: "1" });
  await page.setViewportSize({ width: 1280, height: 800 });
});

// P6.1b (#656): when the re-check is due, whoever closed the problem out answers "did the fix hold?" on its case.
test("whoever closed a problem out answers whether the fix held, and a no reopens the case (#656)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "fred"));
  let item = { ...demoTask.item, id: 7, type: "recheck", title: "Did the fix hold? P-301 leaking at the seal", state: "open",
    closed_at: null, processes: [], events: [], payload: { recheck_of: 3 } };
  await page.route("**/work/items/7", (route) => route.fulfill({ json: item }));
  const answers = [];
  await page.route("**/work/items/3/recheck", (route) => {
    answers.push(route.request().postDataJSON());
    item = { ...item, state: "done", closed_at: "2026-12-12T06:00:00Z" };
    return route.fulfill({ json: { item_id: 3, held: false } });
  });
  await page.goto(`${url}/#/cases/7`);
  const card = page.locator(".recheck");
  await card.getByRole("link", { name: "case 3" }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Close out", exact: true }).count(), 0);  // a re-check isn't closed out itself
  await card.getByLabel("Note").fill("weeping again");
  await card.getByRole("button", { name: "No, it came back" }).click();
  await card.getByRole("button", { name: "No, it came back" }).waitFor({ state: "detached" });
  assert.deepEqual(answers, [{ by: "fred", held: false, note: "weeping again" }]);
});

// P7.2b (#661): from All work, an ops lead sees per queue whether it keeps up this week against the last four, and
// opens the cases behind a number.
test("Work performance shows per queue whether it keeps up and opens the cases behind a number (#661)", async () => {
  const cell = (over = {}) => ({ arrivals: { n: 1, ids: [1] }, closed: { n: 1, ids: [1] }, backlog: { n: 0, ids: [] },
    sla_misses: { n: 0, ids: [] }, escalations: { n: 0, ids: [] }, hand_backs: { n: 0, ids: [] }, repeats: { n: 0, ids: [] },
    triage_h: 1.5, dispatch_h: 3, close_h: 48, ...over });
  await page.route("**/work/performance", (route) => route.fulfill({ json: {
    weeks: [0, 1, 2, 3, 4].map((k) => ({ start: `2026-10-${12 + 7 * k}T00:00:00Z`, end: "" })),
    queues: [
      { queue: "operator", keeping_up: "falling_behind", backlog_avg4: 0, arrivals_avg4: 1, closed_avg4: 0.8,
        weeks: [cell(), cell(), cell(), cell(), cell({ arrivals: { n: 3, ids: [11, 12, 13] }, closed: { n: 0, ids: [] },
          backlog: { n: 3, ids: [11, 12, 13] }, hand_backs: { n: 1, ids: [11] } })] },
      { queue: "maintenance_planner", keeping_up: "keeping_up", backlog_avg4: 0, arrivals_avg4: 1, closed_avg4: 1, weeks: [cell(), cell(), cell(), cell(), cell()] },
    ],
    types: [], compliance: { as_of: "2026-11-12", summary: { PM: { plans: 10, overdue: 2, on_time_pct: 80 } } },
  } }));
  await page.goto(`${url}/#/exceptions`);
  await page.getByRole("link", { name: "How the work is running" }).click();
  await page.getByRole("heading", { name: "Work performance" }).waitFor();
  const row = page.getByRole("row").filter({ hasText: "operator" });
  await row.getByText("falling behind").waitFor();
  await row.getByText("avg 0.8").waitFor();  // the server's four-week mean, not one worked out on the page
  await page.getByRole("row").filter({ hasText: "maintenance planner" }).getByText("keeping up").waitFor();
  await row.getByRole("button", { name: "3" }).first().click();
  const cases = page.locator(".perf-cases");
  await cases.getByText("operator: arrived this week").waitFor();
  assert.deepEqual(await cases.getByRole("link").allInnerTexts(), ["case 11", "case 12", "case 13"]);
  assert.equal(await cases.getByRole("link", { name: "case 11" }).getAttribute("href"), "#/cases/11");
  await page.getByText("80% on time", { exact: false }).waitFor();
  // All work stays the highlighted area while the view is open
  assert.equal(await page.getByRole("link", { name: "All work", exact: true }).first().getAttribute("aria-current"), "page");
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.setViewportSize({ width: 1280, height: 800 });
});

// P5.5 (#684): the dispatch card says whether the job is ready, names each blocker, and a blocked job goes only
// with a reason.
test("the dispatch card shows what blocks a job and sends a blocked one only with a reason (#684)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ines"));
  const job = { id: 7, type: "leak", key: "walk-9", title: "Compressor B1-K-2001 valve noise", state: "open", source: "olga",
    priority: 2, value_usd: null, subjects: ["fz-k2001"], unmatched: [], evidence: {}, item_id: null, assignee: null, role: null,
    raised_count: 1, last_seen: "2026-11-12T05:00:00Z", first_seen: "2026-11-12T05:00:00Z", joined: [], planned: null };
  await page.route("**/exceptions?*", (route) => route.fulfill({ json: [job] }));
  await page.route("**/work/people", (route) => route.fulfill({ json: [{ name: "fred", roles: ["operator"] }, { name: "ines", roles: ["ops_lead"] }] }));
  await page.route("**/exceptions/7/readiness", (route) => route.fulfill({ json: { ready: false,
    blockers: ["Spares: Compressor valve assembly: 2 in stock, 4 needed; on order (4500007101), due 2026-11-15",
      "Spares: Rod packing set: 0 in stock, 2 needed; nothing on order"],
    checks: [{ check: "spares", label: "Spares", ready: false, checked: true, note: null, blockers: [] }] } }));
  const sent = [];
  await page.route("**/exceptions/7/dispatch", (route) => { sent.push(route.request().postDataJSON()); return route.fulfill({ json: job }); });
  await page.goto(`${url}/#/exceptions`);
  await page.getByLabel("Dispatch to").selectOption("fred");
  await page.getByText("Rod packing set: 0 in stock, 2 needed; nothing on order", { exact: false }).waitFor();
  await page.getByText("not ready", { exact: true }).waitFor();
  await page.getByLabel("What to do").fill("check the discharge valves");
  const send = page.getByRole("button", { name: "Dispatch", exact: true });
  assert.equal(await send.isDisabled(), true);  // blocked: a reason first
  await page.getByLabel("Why send it anyway").fill("valves can be changed on the 15th, inspect now");
  await send.click();
  await expectReports(sent, 1);
  assert.deepEqual([sent[0].to, sent[0].override], ["fred", "valves can be changed on the 15th, inspect now"]);
});

// P5.5b, P5.5c (#682, #683): the dispatch card says how busy the job's crew is and which permit covers it, and a full
// crew is a blocker naming its jobs.
test("the dispatch card shows the crew's load, its permit, and a full crew blocks the job (#682, #683)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ines"));
  const job = (id, title) => ({ id, type: "leak", key: `k${id}`, title, state: "open", source: "olga", priority: 2,
    value_usd: null, subjects: ["fz-g7006"], unmatched: [], evidence: {}, item_id: null, assignee: null, role: null,
    raised_count: 1, last_seen: "2026-11-12T05:00:00Z", first_seen: "2026-11-12T05:00:00Z", joined: [], planned: null });
  await page.route("**/exceptions?*", (route) => route.fulfill({ json: [job(8, "Generator load test"), job(9, "Generator batteries")] }));
  await page.route("**/work/people", (route) => route.fulfill({ json: [{ name: "fred", roles: ["operator"] }] }));
  const elec = { id: "fz-elec", label: "ELEC", description: "Electrical technicians", crew: 1, busy: 0, full: false, jobs: [] };
  await page.route("**/exceptions/8/readiness", (route) => route.fulfill({ json: { ready: true, blockers: [],
    checks: [{ check: "spares", label: "Spares", ready: true, checked: false, note: "No BOM for this equipment, so no parts to check.", blockers: [] },
      { check: "crew", label: "Crew", ready: true, checked: true, note: null, blockers: [], centers: [elec] },
      { check: "permits", label: "Permit", ready: true, checked: true, note: null, blockers: [], needed: true,
        permits: [{ id: "fz-p", label: "PTW-24103", type: "electrical", state: "issued", valid_to: "2026-11-13 18:00",
          location_label: "G1-G-7006", valid: true, why_not: null }] }] } }));
  await page.route("**/exceptions/9/readiness", (route) => route.fulfill({ json: { ready: false,
    blockers: ["Crew: Electrical technicians (ELEC): all 1 busy on open jobs: #3 Look at the standby generator"],
    checks: [{ check: "crew", label: "Crew", ready: false, checked: true, note: null, blockers: [],
      centers: [{ ...elec, busy: 1, full: true, jobs: [{ id: 3, title: "Look at the standby generator", assignee: "fred" }] }] }] } }));
  await page.goto(`${url}/#/exceptions`);
  await page.getByText("Generator batteries").waitFor();
  for (const sel of await page.getByLabel("Dispatch to").all()) await sel.selectOption("fred");
  await page.getByText("Electrical technicians: 0 of 1 busy.", { exact: false }).waitFor();
  await page.getByText("Permit PTW-24103 (electrical) valid to 2026-11-13 18:00.", { exact: false }).waitFor();
  await page.getByText("all 1 busy on open jobs: #3 Look at the standby generator", { exact: false }).waitFor();
});

// P5.11a (#708): on the phone, a job whose work type needs a permit shows "blocked: no valid permit" with the permit
// and its isolations, and On site needs a reason before it starts anyway.
test("a job with no valid permit shows blocked on the phone and starts only with a reason (#708)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "fred"));
  const job = { id: 21, type: "upset", key: "k-2001", title: "Compressor B1-K-2001 discharge valve", source: "olga", state: "open",
    priority: null, value_usd: null, subjects: ["fz-k"], unmatched: [], item_id: 51, case_state: "open", assignee: "fred", role: null,
    raised_count: 1, first_seen: "2026-11-12T06:00:00Z", last_seen: "2026-11-12T06:00:00Z", label: "Process upset", due: null,
    task_id: null, task_name: null, assets: [{ fuzer_id: "fz-k", label: "B1-K-2001", kind: "equipment", lat: null, lon: null }],
    finding: { detector: "test", origin: "external", origin_label: "Reported by", method: null, standard: null, start: null, end: null,
      confidence: null, items: [], checks: [], subjects: [], value_usd: null }, history: [], field: [], queued: false,
    sent: { by: "ann", at: "2026-11-12T06:05:00Z", reason: "change the valve" }, sources: [],
    permit: { needed: true, blocked: true,
      blocker: "blocked: no valid permit (upset work on B1-K-2001): PTW-24101 isolations not in place: ISO-24101-3 B1-K-2001 cylinders (planned)",
      permit: { label: "PTW-24101", type: "cold work", state: "issued", valid_from: "2026-11-11 06:00", valid_to: "2026-11-14 18:00",
        work: "Change discharge valves on cylinder 3", location_label: "B1-K-2001", valid: false, why_not: "isolations not in place",
        isolations: [{ isolation: "ISO-24101-1", point: "B1-XV-2001", method: "closed and locked", state: "applied" },
          { isolation: "ISO-24101-3", point: "B1-K-2001 cylinders", method: "blown down and vented", state: "planned" }] } } };
  const reports = [];
  await page.route("**/exceptions/field?*", (route) => route.fulfill({ json: { person: "fred", jobs: [job], coming_up: [] } }));
  await page.route("**/exceptions/21/report", (route) => { reports.push(route.request().postDataJSON()); return route.fulfill({ json: job }); });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto(`${url}/#/inbox`);
  const card = page.locator(".field-job", { hasText: "B1-K-2001 discharge valve" });
  await card.getByText("blocked: no valid permit", { exact: true }).waitFor();
  await card.getByText("Change discharge valves on cylinder 3").waitFor();
  await card.getByText("planned", { exact: true }).waitFor();
  const start = card.getByRole("button", { name: "On site" });
  assert.equal(await start.isDisabled(), true);
  await card.getByLabel("Why start without a valid permit").fill("walk round only, nothing opened");
  await start.click();
  await expectReports(reports, 1);
  assert.deepEqual([reports[0].status, reports[0].override], ["on_site", "walk round only, nothing opened"]);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

// P5.11b (#709): two jobs in one area at once whose permits clash are flagged on Plan in plain words, and the same
// conflict blocks the dispatch card.
test("hot work beside a line break is flagged on Plan and at dispatch (#709)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "ann"));
  const at = (h) => new Date(Date.now() + h * 3_600_000).toISOString();
  const base = { source: "fuzer", source_key: null, area_id: "fz-area-c1", area_label: "TEG dehydration", crew: null, crew_hours: null,
    deferment_usd: null, exception_id: null, notes: null, status: "scheduled", needs_shutdown: false, kind: "maintenance", parts_short: [] };
  const say = "Hot work near a line break in TEG dehydration: hot work (PTW-24105) for this job and line break (PTW-24106) for “Open contactor manway”";
  const items = [
    { ...base, id: 1, title: "Weld reboiler fire tube support", subjects: ["fz-h"], assets: [{ fuzer_id: "fz-h", label: "C1-H-3008" }],
      start_at: at(-2), end_at: at(6), clashes: [{ kind: "simops", with: 2, title: "Open contactor manway", say }] },
    { ...base, id: 2, title: "Open contactor manway", subjects: ["fz-v"], assets: [{ fuzer_id: "fz-v", label: "C1-V-3001" }],
      start_at: at(-1), end_at: at(5), clashes: [{ kind: "simops", with: 1, title: "Weld reboiler fire tube support", say: "Hot work near a line break" }] },
  ];
  await page.route("**/plan?*", (route) => route.fulfill({ json: items }));
  await page.goto(`${url}/#/plan`);
  await page.getByText("2 with a clash").waitFor();
  await page.getByRole("button", { name: /Weld reboiler fire tube support/ }).click();
  await page.getByText("Simultaneous operations: Hot work near a line break in TEG dehydration: hot work (PTW-24105)", { exact: false }).waitFor();
});

// P5.11c (#710): at shift change the outgoing operator hands a job in progress over; the incoming one sees the note
// at the top and accepts it.
test("a job in progress is handed over at shift change and the incoming operator accepts it (#710)", async () => {
  const base = { id: 31, type: "leak", key: "k31", title: "Compressor B1-K-2001 valve change", source: "olga", state: "open",
    priority: null, value_usd: null, subjects: ["fz-k"], unmatched: [], item_id: 61, case_state: "open", assignee: "fred", role: null,
    raised_count: 1, first_seen: "2026-11-12T06:00:00Z", last_seen: "2026-11-12T06:00:00Z", label: "Leak", due: null,
    task_id: null, task_name: null, assets: [{ fuzer_id: "fz-k", label: "B1-K-2001", kind: "equipment", lat: null, lon: null }],
    finding: { detector: "test", origin: "external", origin_label: "Reported by", method: null, standard: null, start: null, end: null,
      confidence: null, items: [], checks: [], subjects: [], value_usd: null }, history: [], queued: false, sources: [],
    field: [{ at: "2026-11-12T15:00:00Z", by: "fred", status: "on_site", note: null }],
    sent: { by: "ann", at: "2026-11-12T14:00:00Z", reason: "change the valve" }, permit: null, handover: null };
  const sent = [];
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "fred"));
  await page.route("**/exceptions/field?*", (route) => route.fulfill({ json: { person: "fred", jobs: [base], coming_up: [] } }));
  await page.route("**/exceptions/31/handover", (route) => { sent.push(route.request().postDataJSON()); return route.fulfill({ json: base }); });
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto(`${url}/#/inbox`);
  const card = page.locator(".field-job", { hasText: "valve change" });
  await card.getByRole("button", { name: "Hand over at shift change" }).click();
  const go = card.getByRole("button", { name: "Hand over", exact: true });
  assert.equal(await go.isDisabled(), true);
  await card.getByLabel("Hand over to").fill("gina");
  await card.getByLabel("What was done").fill("isolated and blown down");
  await card.getByLabel("What is left").fill("fit the new discharge valve");
  await card.getByLabel("Unsafe to leave").fill("cylinder 3 cover is off");
  await go.click();
  await expectReports(sent, 1);
  assert.deepEqual(sent[0], { by: "fred", to: "gina", done: "isolated and blown down", left: "fit the new discharge valve",
    unsafe: "cylinder 3 cover is off" });

  const handed = { ...base, queued: true, handover: { from: "fred", to: "gina", at: "2026-11-12T17:55:00Z", done: "isolated and blown down",
    left: "fit the new discharge valve", unsafe: "cylinder 3 cover is off", permit: null, mine_to_accept: true, waiting_on: null } };
  const accepted = [];
  await page.evaluate(() => localStorage.setItem("fuzer.reviewer", "gina"));
  await page.unroute("**/exceptions/field?*");
  await page.route("**/exceptions/field?*", (route) => route.fulfill({ json: { person: "gina", jobs: [handed], coming_up: [] } }));
  await page.route("**/exceptions/31/handover/accept", (route) => { accepted.push(route.request().postDataJSON()); return route.fulfill({ json: base }); });
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "gina"));
  await page.reload();
  const theirs = page.locator(".field-job", { hasText: "valve change" });
  await theirs.getByText("Handed over to you by fred").waitFor();
  await theirs.getByText("Unsafe to leave: cylinder 3 cover is off").waitFor();
  assert.equal(await theirs.getByRole("button", { name: "Take it" }).count(), 0);
  await theirs.getByRole("button", { name: "Accept the handover" }).click();
  await expectReports(accepted, 1);
  assert.deepEqual(accepted[0], { by: "gina" });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

// P5.4a (#717): an operator's My work shows this shift's rounds, each with how far it has got, and opens one to its
// points in order with their limits.
test("an operator sees this shift's rounds and takes a round's readings and checks (#717, #718)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "olu"));
  await page.route("**/work/inbox?*", (route) => route.fulfill({ json: { person: "olu", roles: ["operator"], mine: [], queue: [],
    counts: { mine: { total: 0 }, queue: { total: 0 } } } }));
  const run = { id: 4, round: "Compression", area: "Compression", shift_start: "2026-11-12T06:00:00Z", shift_end: "2026-11-12T18:00:00Z",
    role: "operator", points: 2, taken: 1, found: 0, state: "open" };
  await page.route("**/rounds", (route) => route.fulfill({ json: [run] }));
  await page.route("**/rounds/4", (route) => route.fulfill({ json: { ...run, points: [
    { id: "fz-rp1", point_id: "RP-B1-01", asset: "fz-k", asset_label: "B1-K-2001", round: "Compression", area: "Compression",
      what: "Discharge pressure", kind: "reading", unit: "barg", low: 40, high: 62, taken: null },
    { id: "fz-rp2", point_id: "RP-B1-02", asset: "fz-k", asset_label: "B1-K-2001", round: "Compression", area: "Compression",
      what: "No oil on the skid", kind: "check", unit: null, low: null, high: null, taken: null }] } }));
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto(`${url}/#/inbox`);
  const rounds = page.locator(`section[aria-labelledby="rounds-this-shift"]`);
  await rounds.getByText("1 of 2 taken").waitFor();
  await rounds.getByRole("button", { name: "Compression (Compression)" }).click();
  await rounds.getByText("40 to 62 barg").waitFor();
  await rounds.getByText("No oil on the skid").waitFor();
  // P5.4b (#718): readings and checks are taken on the phone; a failed check needs what was found
  const sent = [];
  await page.route("**/rounds/4/readings", (route) => { sent.push(route.request().postDataJSON()); return route.fulfill({ json: {} }); });
  const save = rounds.getByRole("button", { name: "Save" });
  assert.equal(await save.isDisabled(), true);
  await rounds.getByLabel("Reading for Discharge pressure on B1-K-2001").fill("65.5");
  await save.click();
  await expectReports(sent, 1);
  assert.deepEqual(sent[0], { point: "fz-rp1", by: "olu", value: 65.5 });
  await rounds.getByRole("button", { name: "Found something" }).click();
  const go = rounds.getByRole("button", { name: "Send to the IOC" });
  assert.equal(await go.isDisabled(), true);
  await rounds.getByLabel("What you found at No oil on the skid on B1-K-2001").fill("oil weeping at the drain");
  await go.click();
  await expectReports(sent, 2);
  assert.deepEqual(sent[1], { point: "fz-rp2", by: "olu", passed: false, note: "oil weeping at the drain" });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

// P5.4c (#719): Work performance counts each shift's operator rounds: done, partly done, missed and the checks not
// taken.
test("Work performance counts rounds done, partly done and missed per shift (#719)", async () => {
  await page.route("**/work/performance", (route) => route.fulfill({ json: { weeks: [], queues: [], types: [], compliance: null,
    rounds: [{ shift_start: "2026-11-12T06:00:00Z", shift_end: "2026-11-12T18:00:00Z", rounds: 3, done: 0, partly_done: 0,
      missed: 0, open: 3, not_taken: 0, found: 0 },
    { shift_start: "2026-11-11T18:00:00Z", shift_end: "2026-11-12T06:00:00Z", rounds: 3, done: 1, partly_done: 1, missed: 1,
      open: 0, not_taken: 5, found: 1 }] } }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${url}/#/performance`);
  const card = page.locator(".ui-card", { hasText: "Operator rounds per shift" });
  await card.getByText("3 under way").waitFor();
  const last = card.getByRole("row").nth(2);
  await last.getByText("1 of 3").waitFor();
  assert.equal(await last.locator('[data-label="Checks not taken"]').innerText(), "5");
  assert.equal(await last.locator('[data-label="Missed"]').innerText(), "1");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.setViewportSize({ width: 1280, height: 800 });
});

// P5.6a (#317): the Processes catalog recovers from a failed read, says who can publish and where routing lives,
// labels and checks the new-process key, and an import that fails says why and can be tried again.
test("the Processes catalog recovers, explains who publishes, and validates new processes (#317)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "alice"));
  let failing = true;
  const imports = [];
  await page.route("**/work/definitions", (route) => route.request().method() === "POST"
    ? (imports.push(route.request().postDataJSON()), route.fulfill({ status: 400, json: { detail: "line 3: a person's task needs an SLA" } }))
    : route.fulfill(failing ? { status: 503, json: { detail: "Database restarting" } }
    : { json: [{ id: 1, process_key: "upset", version: 2, name: "Upset", state: "published", published_at: "2026-11-01T00:00:00Z", running: 3 },
      { id: 2, process_key: "upset", version: 3, name: "Upset", state: "draft", published_at: null, running: 0 }] }));
  await page.route("**/work/stats?*", (route) => route.fulfill({ json: [{ process_key: "upset", waiting: 2, overdue: 1, median_h: 5, p90_h: 30, sla_breach_pct: 12 }] }));
  await page.route("**/work/people", (route) => route.fulfill({ json: [{ name: "alice", roles: ["engineer"] }] }));
  await page.route("**/work/forms", (route) => route.fulfill({ json: [] }));
  await page.goto(`${url}/#/processes`);
  await page.getByRole("alert").filter({ hasText: "Database restarting" }).waitFor();
  failing = false;
  await page.getByRole("button", { name: "Try again" }).first().click();
  const row = page.getByRole("row").filter({ hasText: "Upset" });
  await row.getByText("v3").waitFor();
  await page.getByText("which you don't have", { exact: false }).waitFor();
  assert.equal(await page.locator("#who-edits").getByRole("link", { name: "Routing" }).getAttribute("href"), "#/routing");
  await page.getByText("No forms yet.", { exact: false }).waitFor();

  const key = page.getByLabel("Key");
  const create = page.getByRole("button", { name: "Create draft" });
  await key.fill("upset");
  await page.getByRole("alert").filter({ hasText: "That key is taken" }).waitFor();
  assert.equal(await create.isDisabled(), true);
  assert.equal(await key.getAttribute("aria-invalid"), "true");
  await key.fill("Lesson Review");
  await page.getByRole("alert").filter({ hasText: "lower case letters" }).waitFor();
  await key.fill("lesson_review");
  assert.equal(await create.isDisabled(), false);
  assert.equal(await page.getByRole("alert").filter({ hasText: "lower case letters" }).count(), 0);
  await page.getByLabel("BPMN file to import").setInputFiles({ name: "broken.bpmn", mimeType: "application/xml",
    buffer: Buffer.from("<definitions/>") });
  await page.getByRole("alert").filter({ hasText: "Couldn't import broken.bpmn: line 3: a person's task needs an SLA" }).waitFor();
  assert.deepEqual(imports, [{ xml: "<definitions/>", by: "alice" }]);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.setViewportSize({ width: 1280, height: 800 });
});

// P5.6b (#318): a process page says which version you are on and how it relates to what runs, its views are
// labelled tabs, a draft shows what draft, check and publish each do, and the monitor, running and routing views
// recover from a failed read.
test("a process page says where you are, what each step of a change does, and recovers each view (#318)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "alice"));
  const xml = readFileSync(new URL("../../src/fuzer/workflow/definitions/shift_handover.bpmn", import.meta.url), "utf8");
  await page.route("**/work/definitions?*", (route) => route.fulfill({ json: [
    { id: 1, process_key: "shift_handover", version: 2, name: "Shift handover", state: "published", published_at: "2026-11-01T00:00:00Z", running: 4,
      created_by: "olga", created_at: "2026-11-01T00:00:00Z", problems: [] },
    { id: 2, process_key: "shift_handover", version: 3, name: "Shift handover", state: "draft", published_at: null, running: 0,
      created_by: "alice", created_at: "2026-11-11T00:00:00Z", problems: [] }] }));
  await page.route("**/work/definitions/*/bpmn", (route) => route.fulfill({ body: xml, contentType: "application/xml" }));
  await page.route("**/work/people", (route) => route.fulfill({ json: [{ name: "alice", roles: ["engineer"] }] }));
  await page.route("**/work/roles", (route) => route.fulfill({ json: { person: ["ops_lead"], agent: [] } }));
  let failing = true;
  const flaky = (json) => (route) => route.fulfill(failing ? { status: 503, json: { detail: "Read timed out" } } : { json });
  await page.route("**/work/monitor/**", flaky({ steps: [], days: 30, version: null }));
  await page.route("**/work/instances*", flaky([]));
  await page.route("**/work/definitions/1/routing", flaky([]));
  await page.goto(`${url}/#/processes/shift_handover?tab=model`);
  await page.getByTestId("where-you-are").filter({ hasText: "You're looking at draft v3 (not checked yet); v2 is published, with 4 running." }).waitFor();
  const tabs = page.getByRole("navigation", { name: "Process views" });
  assert.equal(await tabs.getByRole("link", { name: "Model" }).getAttribute("aria-current"), "page");
  const steps = page.getByRole("list", { name: "How a change goes live" });
  assert.match(await steps.locator('[aria-current="step"]').innerText(), /^Draft/);
  await steps.getByText("Cases already running stay on their version.", { exact: false }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Publish", exact: true }).isDisabled(), true);

  for (const [tab, after] of [["Monitor", null], ["Running", "None."], ["SLAs & routing", null]]) {
    failing = true;
    await tabs.getByRole("link", { name: tab }).click();
    const alert = page.getByRole("alert").filter({ hasText: "Read timed out" });
    await alert.waitFor();
    failing = false;
    await alert.getByRole("button", { name: "Try again" }).click();
    await alert.waitFor({ state: "detached" });
    if (after) await page.getByText(after, { exact: true }).waitFor();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.setViewportSize({ width: 1280, height: 800 });
});

// P5.6c (#319): the task form editor opens a valid form without runtime errors, previews it as a person sees it,
// keeps changes after a failed save, checks the bulk actions and the new key, and turns a missing form into a way
// to create it.
test("the task form editor previews, survives a failed save, and recovers a missing form (#319)", async () => {
  await page.addInitScript(() => localStorage.setItem("fuzer.reviewer", "alice"));
  const schema = { type: "default", id: "lesson-review", schemaVersion: 16,
    components: [{ type: "textfield", key: "lesson", label: "What was learned", id: "f1" }] };
  await page.route("**/work/forms/lesson-review", (route) => route.fulfill({ json: { key: "lesson-review", version: 2, schema } }));
  await page.route("**/work/forms/gone", (route) => route.fulfill({ status: 404, json: { detail: "no form gone" } }));
  const saves = [];
  await page.route("**/work/forms?*", (route) => { saves.push(route.request().postDataJSON());
    return route.fulfill({ status: 503, json: { detail: "Database restarting" } }); });
  await page.goto(`${url}/#/processes/forms/lesson-review`);
  await page.getByText("v2. Saving makes a new version", { exact: false }).waitFor();
  await page.locator(".form-editor .fjs-editor-container").waitFor();
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  const preview = page.locator(".form-preview");
  await preview.getByLabel("What was learned").waitFor();
  await page.getByRole("button", { name: "Back to editing" }).click();

  await page.getByText("Bulk actions", { exact: false }).first().click();
  const bulk = page.getByLabel("Bulk actions (JSON)", { exact: false });
  await bulk.fill("{ not json");
  await page.getByRole("alert").filter({ hasText: "Bulk actions aren't valid JSON" }).waitFor();
  const save = page.getByRole("button", { name: "Save new version" });
  assert.equal(await save.isDisabled(), true);
  await bulk.fill('[{ "label": "Accept all", "data": { "accepted": true } }]');
  await save.click();
  await page.getByRole("alert").filter({ hasText: "Couldn't save: Database restarting. Your changes are still here" }).waitFor();
  assert.equal(saves.length, 1);
  assert.deepEqual(saves[0].fuzer.bulk, [{ label: "Accept all", data: { accepted: true } }]);
  await page.getByText("unsaved changes").waitFor();

  await page.goto(`${url}/#/processes/forms/gone`);
  const missing = page.getByRole("alert").filter({ hasText: "There is no form “gone”." });
  await missing.waitFor();
  assert.equal(await missing.getByRole("link", { name: "Create it" }).getAttribute("href"), "#/processes/forms/new?key=gone");
  await missing.getByRole("link", { name: "Create it" }).click();
  const key = page.getByLabel("Form key");
  assert.equal(await key.inputValue(), "gone");
  await key.fill("Bad Key");
  await page.getByRole("alert").filter({ hasText: "Use lower case letters" }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Create form" }).isDisabled(), true);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.setViewportSize({ width: 1280, height: 800 });
});

// P5.9a (#337): Plan shows production at risk per shutdown window, with the hours each job is down and any assumed
// duration said beside the number.
test("an asset shows how people worked its problems in Teams, with a link back (#745)", async () => {
  await page.route("**/entities/tag-k2001/map", (route) => route.fulfill({ json: { features: [], focus: null } }));
  await page.route("**/entities/tag-k2001", (route) => route.fulfill({ json: { fuzer_id: "tag-k2001", kind: "tag",
    label: "B1-K-2001", golden: { description: { value: "Sales gas compressor" } }, values: {}, records: [], keys: [], edges: [], edge_counts: {} } }));
  await page.route("**/entities/tag-k2001/conversations*", (route) => route.fulfill({ json: [
    { id: "c1", subject: "B1-K-2001 high vibration after restart", channel: "KRGP Operations", state: "resolved",
      started_at: "2026-10-31T06:40:00", last_post_at: "2026-10-31T10:45:00", participants: ["Field Operator", "Rotating Equipment Engineer"],
      problem: "2nd stage reading 6.8 mm/s on the cooler end.", tried: ["Alignment within spec, lube oil 3.1 barg."],
      fix: "Found a loose mounting bolt on the 2nd stage cooler fan. That did it.",
      web_url: "https://teams.microsoft.com/l/message/19:kr-ops@thread.tacv2/1731000000001" },
    { id: "c2", subject: "B1-K-2001 seal gas pressure low", channel: "KRGP Reliability", state: "open", started_at: "2026-11-11T21:15:00",
      last_post_at: null, participants: ["Field Operator"], problem: "Seal gas low.", tried: [], fix: null, web_url: null }] }));
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto(`${url}/#/e/tag-k2001`);
    const card = page.locator("section.conversations");
    await card.getByText("Fix: Found a loose mounting bolt on the 2nd stage cooler fan. That did it.").waitFor();
    await card.getByText("still open").waitFor();
    assert.equal(await card.getByRole("link", { name: "Open in Teams" }).getAttribute("href"),
      "https://teams.microsoft.com/l/message/19:kr-ops@thread.tacv2/1731000000001");
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
});

test("Plan shows production at risk per shutdown window with the durations used (#337)", async () => {
  await page.route("**/plan?*", (route) => route.fulfill({ json: [] }));
  await page.route("**/plan/at-risk?*", (route) => route.fulfill({ json: { start: "2026-11-12T00:00:00Z", end: "2027-02-10T00:00:00Z", days: 90,
    basis: "Wells behind each unit on the flow network, at their latest test rate, for the time the unit is down; a well is counted once however many overlapping jobs take it down.",
    windows: [{ start: "2026-11-22T00:00:00Z", end: "2026-11-25T00:00:00Z", untested: ["KR 14-01H"],
      assumptions: ["SAP maintenance plans give a due date, not an outage length: one day is assumed"],
      items: [{ id: 1, title: "Compressor valve change", kind: "shutdown", hours: 48, assumed: null, start: "2026-11-22T00:00:00Z",
        end: "2026-11-24T00:00:00Z", units: [{ id: "fz-k", label: "B1-K-2001" }], wells: 2 },
      { id: 2, title: "Compressor 4000-hour service", kind: "maintenance", hours: 24, assumed: "SAP maintenance plans give a due date, not an outage length: one day is assumed",
        start: "2026-11-24T00:00:00Z", end: "2026-11-25T00:00:00Z", units: [{ id: "fz-k", label: "B1-K-2001" }], wells: 2 }],
      wells: [{ id: "w1", label: "KR 12-03H", hours: 72, rates: { gas_rate: { value: 841, unit: "kSm3/d", at: "2026-11-01" } } },
        { id: "w2", label: "KR 14-01H", hours: 72, rates: {} }],
      at_risk: [{ quantity: "gas_rate", value: 2523, unit: "kSm3" }] }] } }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${url}/#/plan`);
  const card = page.locator(".plan-at-risk");
  await card.getByText("Gas 2,523 kSm3").waitFor();
  await card.getByText("counted once", { exact: false }).waitFor();
  await card.getByRole("button", { name: /Gas 2,523 kSm3/ }).click();
  await card.getByText("Compressor valve change: 48 h down (B1-K-2001)").waitFor();
  await card.getByText("Assumption: SAP maintenance plans give a due date, not an outage length: one day is assumed.").waitFor();
  await card.getByText("No test on record, so not counted: KR 14-01H.").waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.setViewportSize({ width: 1280, height: 800 });
});

test("a turnaround with a schedule shows its progress against the baseline and the slip (#360)", async () => {
  const scope = { revision: "TA26", revisions: ["TA26"], as_of: "2026-03-01", planned_start: null, scoped_work: [],
    shutdown_plans_not_in_scope: [], bad_actors_not_in_scope: [], spares_out_of_stock: [], window: null, on_plan: [],
    ride_along: [], shutdown_outside: [], schedule: { project: "TA26", as_of: "2026-03-01", late: [], slipped: ["A0100"] } };
  const asked = [];
  await page.route("**/plan?*", (route) => route.fulfill({ json: [] }));
  await page.route("**/turnarounds?*", (route) => route.fulfill({ json: scope }));
  await page.route("**/analytics/figures/turnaround-progress*", (route) => { asked.push(route.request().url()); return route.fulfill({ json: {
    kind: "turnaround-progress", title: "Turnaround TA26 progress", sources: [],
    spec: { data: [{ type: "scatter", mode: "lines", name: "Planned (baseline)", x: ["2026-02-01", "2026-03-01"], y: [0, 40] }], layout: {} },
    summary: { context: "Schedule as of 1 Mar 2026 · 8 activities counted", headline: "30% earned against 40% planned: 10 points behind the plan, forecast to finish 14 days after its baseline.",
      stats: [{ label: "Earned", value: "30", unit: "%" }], notes: [], source: "the planning system's schedule (activities)",
      warnings: ["Not counted: A0010, a percent complete of 112, outside 0 to 100."],
      tables: [{ title: "Slipped activities · 1", note: null, columns: ["Activity", "Days slipped"], rows: [["A0100", "14"]] }] } } }); });
  await page.goto(`${url}/#/plan?revision=TA26`);
  const card = page.locator("section", { has: page.getByRole("heading", { name: "Progress against the baseline" }) });
  await card.getByText("10 points behind the plan", { exact: false }).waitFor();
  await card.getByText("Not counted: A0010, a percent complete of 112, outside 0 to 100.").waitFor();
  await card.getByRole("button", { name: "Slipped activities · 1" }).waitFor();
  assert.ok(asked.some((u) => u.includes("revision=TA26")));
});
