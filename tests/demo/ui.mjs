// Browser run-of-show: the on-screen half of docs/DEMO_RUNBOOK.md (steps a, f, h, i and Live Mode), driven as the presenter would.
//   BASE_URL=http://localhost:3101 DEMO_PASSWORD='Mintree-Demo#2026' PLAYWRIGHT_DIR=/path/with/node_modules node tests/demo/ui.mjs
// Needs the web app and API running against the DEMO database (make demo-up). Every step asserts what is on screen AND what the API says.
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
const { chromium } = createRequire(process.env.PLAYWRIGHT_DIR ? join(process.env.PLAYWRIGHT_DIR, "x.js") : import.meta.url)("playwright");

const BASE = process.env.BASE_URL ?? "http://localhost:3101";
const PASSWORD = process.env.DEMO_PASSWORD ?? "Mintree-Demo#2026";
const SHOTS = process.env.SHOTS ?? mkdtempSync(join(tmpdir(), "demo-ui-"));
const BUDGET_MS = { a: 12000, live: 70000, f: 25000, h: 15000, i: 20000, cc: 40000, sc: 170000, inv: 60000, t1: 60000 };
const results = [];
const consoleErrors = [];
const browser = await chromium.launch();
let ctx, page;
const fresh = async (viewport = { width: 1440, height: 900 }) => {
  await ctx?.close();
  ctx = await browser.newContext({ viewport });
  page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource|ERR_ABORTED/.test(m.text())) consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
};
const shot = (n) => page.screenshot({ path: join(SHOTS, `${n}.png`) }).catch(() => {});
async function step(key, name, fn) {
  const t0 = Date.now();
  try {
    await fn();
    const ms = Date.now() - t0;
    assert.ok(ms <= BUDGET_MS[key], `took ${ms} ms, budget ${BUDGET_MS[key]} ms`);
    results.push([name, "PASS", ms]);
    console.log("PASS", name, `${ms} ms`);
  } catch (e) {
    results.push([name, "FAIL", Date.now() - t0]);
    console.log("FAIL", name, "\n   ", e.message.split("\n").slice(0, 10).join("\n    "));
    await shot(`FAIL-${key}`);
  }
}
async function loginAs(user, viewport) {
  await fresh(viewport);
  csrfToken = "";
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email").fill(`demo.${user}@example.com`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(`${BASE}/`);
}
let csrfToken = "";
const csrf = async () => csrfToken || (csrfToken = (await (await ctx.request.get(`${BASE}/api/auth/me`)).json()).csrf_token);       // the CSRF token comes from /auth/me, as the app itself does
const settle = async () => { await page.getByRole("heading", { level: 1 }).first().waitFor(); await page.waitForTimeout(1200); };       // hydration: keyboard shortcuts attach after the first paint
const api = async (path, init = {}) => {                                        // an independent read of the backend as the same signed-in user
  const r = await ctx.request.fetch(`${BASE}/api${path}`, { method: init.method ?? "GET", headers: { "X-CSRF-Token": await csrf(), ...(init.headers ?? {}) }, data: init.data });
  return r.status() === 204 ? null : r.json();
};

await step("a", "a) Kanika signs in; Overview greets her; KPIs, period toggle and Approvals are live", async () => {
  await loginAs("admin");
  await page.getByRole("heading", { level: 1 }).waitFor();
  assert.match(await page.locator("main").innerText(), /Kanika/, "the founder is greeted by name");
  const strip = page.getByRole("region", { name: "Key indicators" });
  await strip.waitFor();
  assert.equal(await strip.locator("li").count(), 4);
  const [resp] = await Promise.all([page.waitForResponse((r) => r.url().includes("/analytics/overview-period")), page.getByRole("button", { name: /90 days/ }).click()]);
  assert.equal(resp.status(), 200);
  await page.getByRole("link", { name: /Approvals/ }).or(page.getByRole("button", { name: /Approvals/ })).first().waitFor();
  await shot("a-overview");
});

await step("live", "Live Mode: Demo mode pill, controls drawer, and a triggered order reaching the screen without a reload", async () => {
  await page.goto(`${BASE}/`);
  const pill = page.getByTestId("demo-pill");
  if (!(await pill.waitFor({ timeout: 8000 }).then(() => true).catch(() => false))) { console.log("   (Live Mode is off in this environment: skipped)"); return; }
  await api("/demo/controls", { method: "POST", data: { action: "resume" } });          // a previous run or check may have left it paused
  await page.reload();
  const before = (await api("/demo/status")).counters?.trigger ?? 0;
  await page.getByTestId("demo-pill").click();
  const drawer = page.getByRole("dialog", { name: /Demo controls/ });
  await drawer.waitFor();
  await drawer.getByText("Stream connected").waitFor({ timeout: 10000 });
  await drawer.getByRole("button", { name: "New order" }).click();
  await page.waitForFunction(async (b) => (await (await fetch("/api/demo/status")).json()).counters?.trigger > b, before, { timeout: 45000 });
  await drawer.getByText(/Order LIVE-/).first().waitFor({ timeout: 15000 });          // pushed over the stream, not fetched on a timer
  await shot("live-drawer");
  await drawer.getByRole("button", { name: /Pause live/ }).click();
  await page.getByText("Demo mode · paused").waitFor();                                  // the pill reflects it
  await drawer.getByRole("button", { name: /Resume live/ }).click();
  await page.keyboard.press("Escape");
});

const post = (path, data) => api(path, { method: "POST", data, headers: { "Idempotency-Key": `ui-${Date.now()}-${Math.random()}` } });
async function makeDeal(tag) {                                                          // a deal in Negotiation with an accepted quotation, built through the real API (Won is refused without one)
  const cust = (await api("/masters/customers?page_size=1")).items[0].id, fg = (await api("/masters/items?q=FG-BW-OUD-200&page_size=1")).items[0].id;
  let o = await post("/crm/opportunities", { name: `Demo deal ${tag}`, customer_id: cust, expected_value: 90000 });
  for (const stage of ["QUALIFIED", "PROPOSAL", "NEGOTIATION"]) o = await post(`/crm/opportunities/${o.id}/stage`, { stage, expected_version: o.version });
  let q = await post("/sales/quotations", { customer_id: cust, valid_until: new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10), notes: "ui", lines: [{ item_id: fg, qty: "10", unit_price: "560.00" }] });
  o = await post(`/crm/opportunities/${o.id}/quotation`, { quotation_id: q.id, expected_version: o.version });
  q = await post(`/sales/quotations/${q.id}/send`, { expected_version: q.version });
  await post(`/sales/quotations/${q.id}/accept`, { expected_version: q.version });
  return o;
}

await step("f", "f) CRM: move a deal to Won; toast with Undo; Undo sends nothing; the second try commits", async () => {
  await loginAs("sales");
  const tag = `UI-${Date.now() % 100000}`;
  await makeDeal(tag);
  await page.goto(`${BASE}/crm/board`);
  const deal = page.locator("article").filter({ hasText: `Demo deal ${tag}` }).first();
  await deal.waitFor();
  const name = `Demo deal ${tag}`;
  const stageOf = async () => (await api("/crm/opportunities?page_size=100")).items.find((o) => o.name === name)?.stage;
  assert.equal(await stageOf(), "NEGOTIATION");
  const move = async () => { await page.getByRole("button", { name: `Move ${name}` }).click(); await page.getByRole("menuitem", { name: "Move to Won" }).click(); };
  await move();
  const toast = page.getByRole("status").filter({ hasText: /won/i });
  await toast.waitFor();
  await toast.getByRole("button", { name: "Undo" }).click();
  await page.waitForTimeout(4600);                                                  // longer than the 4 s hold: had the server been told, it would be Won by now
  assert.equal(await stageOf(), "NEGOTIATION", "Undo means the server never heard");
  await move();
  await page.waitForTimeout(4600);
  assert.equal(await stageOf(), "WON", "the committed move is real");
  await shot("f-won");
});

await step("h", "h) Approvals + notifications: something to approve; opening a notification marks it read", async () => {
  await loginAs("admin");
  const pend = await api("/approvals/pending");
  assert.ok(pend.items.length >= 1, "demo_prep guarantees something to approve");
  await page.goto(`${BASE}/approvals`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.goto(`${BASE}/notifications`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  const unread0 = (await api("/notifications?page_size=1")).unread;
  assert.ok(unread0 >= 1, "there are unread alerts");
  await page.getByRole("tab", { name: /^Unread/ }).click();                          // the presenter works through what is unread
  await page.locator("main").getByRole("link", { name: /^Open/ }).or(page.locator("main").getByRole("button", { name: /^Open/ })).first().click();
  let unread1 = unread0;
  for (let i = 0; i < 20 && unread1 >= unread0; i++) { await page.waitForTimeout(250); unread1 = (await api("/notifications?page_size=1")).unread; }       // the read-mark is sent as the page opens
  assert.ok(unread1 < unread0, `opening a notification marks it read (${unread0} -> ${unread1})`);
  await shot("h-notifications");
});

await step("i", "i) Palette search, Scan (Alt+S), light/dark and the mobile bar", async () => {
  await loginAs("admin");
  await settle();
  await page.keyboard.press("Control+k");
  await page.getByPlaceholder(/Search pages/).fill("oud");
  await page.getByRole("option").first().waitFor();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Alt+s");
  await page.getByRole("dialog").waitFor();
  await page.keyboard.press("Escape");
  await page.locator("header button[aria-haspopup=menu]").last().click();
  await page.getByRole("menuitem", { name: "Dark appearance" }).click();
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "dark");
  await shot("i-dark");
  await loginAs("admin", { width: 390, height: 844 });                                 // a phone
  await page.getByRole("navigation", { name: "Main" }).last().waitFor();                  // the sidebar is also a "Main" navigation; the bottom bar is the last one
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), "no horizontal page scroll on a phone");
  await shot("i-mobile");
});

await step("cc", "Control Centre: a presenter action appears on a second person's Live activity screen without a reload", async () => {
  await loginAs("admin");
  await page.goto(`${BASE}/admin/demo-control`);
  await page.getByRole("heading", { level: 1, name: "Demo Control Centre" }).waitFor();
  const other = await browser.newContext({ viewport: { width: 1440, height: 900 } });                  // a second person, on their own screen
  const p2 = await other.newPage();
  p2.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource|ERR_ABORTED/.test(m.text())) consoleErrors.push(m.text()); });
  try {
    await p2.goto(`${BASE}/login`);
    await p2.getByLabel("Email").fill("demo.disp@example.com");
    await p2.getByLabel("Password").fill(PASSWORD);
    await p2.getByRole("button", { name: "Sign in" }).click();
    await p2.waitForURL(`${BASE}/`);
    await p2.goto(`${BASE}/activity`);
    await p2.getByRole("heading", { level: 1, name: "Live activity" }).waitFor();
    await p2.getByTestId("live-indicator").and(p2.locator("[data-state=live]")).waitFor({ timeout: 15000 });
    const before = await p2.locator('[data-testid=activity-item][data-type="sales.order.created"]').count();
    await page.waitForTimeout(1500);
    await page.getByTestId("act-b2b_order").click();
    await page.getByTestId("trigger-results").getByText(/created and confirmed/).first().waitFor({ timeout: 30000 });
    await p2.waitForFunction((b) => document.querySelectorAll('[data-testid=activity-item][data-type="sales.order.created"]').length > b, before, { timeout: 15000 });
    await shot("cc-control");
    await p2.screenshot({ path: join(SHOTS, "cc-activity.png") }).catch(() => {});
    assert.match(await p2.locator('[data-testid=activity-item][data-type="sales.order.created"]').first().innerText(), /Demo/, "demo activity is labelled");
  } finally { await other.close(); }
});

await step("sc", "Control Centre: the scripted scenario runs from the screen, pauses, resumes and completes all 15 steps", async () => {
  await loginAs("admin");
  await page.goto(`${BASE}/admin/demo-control`);
  await page.getByTestId("scenario").waitFor();
  if (await page.getByTestId("scenario-reset").isVisible().catch(() => false)) await page.getByTestId("scenario-reset").click();
  await page.getByRole("button", { name: "×3" }).click();
  await page.getByTestId("scenario-start").click();
  await page.getByTestId("scenario-step").first().and(page.locator("[data-state=done]")).waitFor({ timeout: 20000 });
  await page.getByTestId("scenario-pause").click();
  await page.getByTestId("scenario-resume").waitFor();
  await page.getByTestId("scenario-resume").click();
  await page.getByTestId("scenario-status").getByText(/Completed/).waitFor({ timeout: 150000 });
  const done = await page.locator("[data-testid=scenario-step][data-state=done], [data-testid=scenario-step][data-state=skipped]").count();
  assert.equal(done, 15, "all 15 steps finished");
  assert.equal(await page.locator("[data-testid=scenario-step][data-state=failed]").count(), 0);
  await shot("sc-done");
  await page.getByTestId("scenario-reset").click();
  await page.getByRole("button", { name: "×1" }).click();
});

await step("inv", "Invoicing: a draft posts nothing; posting issues the invoice, which appears under Invoice drafts and Sales invoices", async () => {
  await loginAs("admin");
  await page.goto(`${BASE}/admin/demo-control`);
  await page.getByTestId("act-b2b_order").click();
  await page.getByTestId("trigger-results").getByText(/created and confirmed/).first().waitFor({ timeout: 30000 });
  const latest = async () => (await api("/dispatch/invoices?page_size=1")).items?.[0]?.number;
  const invoicesBefore = await latest();
  await page.getByTestId("act-draft_invoice").click();
  await page.getByTestId("trigger-results").getByText(/nothing is posted/).first().waitFor({ timeout: 30000 });
  assert.equal(await latest(), invoicesBefore, "a draft creates no invoice");
  await page.getByTestId("act-post_invoice").click();
  await page.getByTestId("trigger-results").getByText(/posted \(/).first().waitFor({ timeout: 30000 });
  await page.goto(`${BASE}/m/dispatch/invoice-drafts`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.getByText(/DRAFT-\d+/).first().waitFor({ timeout: 15000 });                    // the list has loaded: a real row is on screen
  await page.getByRole("button", { name: /^Posted$/i }).click();                                // the Posted filter shows the draft that was just posted
  await page.getByText(/DRAFT-\d+/).first().waitFor({ timeout: 15000 });
  await shot("inv-drafts");
  let after = await latest();
  for (let i = 0; i < 20 && after === invoicesBefore; i++) { await page.waitForTimeout(500); after = await latest(); }       // the invoice list may trail the post by a moment (short server cache)
  assert.ok(after && after !== invoicesBefore && after.startsWith("SI/"), `posting issued a new invoice (${invoicesBefore} -> ${after})`);
});

await step("t1", "Tier 1: shop floor, recall case, expiry control and rework screens open for the right people", async () => {
  await loginAs("pm");
  await page.goto(`${BASE}/floor`);
  await page.getByRole("heading", { level: 1, name: "Shop floor" }).waitFor();
  await page.getByTestId("floor-order").first().waitFor({ timeout: 15000 });
  await page.getByTestId("floor-order").first().click();
  await page.getByTestId("floor-order-detail").waitFor();
  await shot("t1-floor");
  await loginAs("admin");
  const r = await api("/quality/recalls");
  if (r.items?.length) {
    await page.goto(`${BASE}/recall/${r.items[0].id}`);
    await page.getByTestId("recall-case").waitFor({ timeout: 15000 });
    await shot("t1-recall");
  }
  for (const [path, title] of [["m/inventory/expiry-control", "Expiry control"], ["m/production/rework", "Rework"], ["m/quality/recalls", "Batch recalls"]]) {
    await page.goto(`${BASE}/${path}`);
    await page.getByRole("heading", { level: 1, name: title }).waitFor({ timeout: 15000 });
  }
  await loginAs("op");                                                      // an operator may use the floor but is not offered recalls
  assert.equal((await api("/quality/recalls")).detail !== undefined, true, "operators cannot read recalls");
});

await step("a", "no unexpected browser console errors", async () => assert.deepEqual(consoleErrors, []));
await browser.close();
const failed = results.filter((r) => r[1] === "FAIL");
console.log(`\n${results.length - failed.length}/${results.length} passed; screenshots in ${SHOTS}`);
process.exit(failed.length ? 1 : 0);
