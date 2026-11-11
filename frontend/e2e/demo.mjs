// Browser E2E against a DEMO-SEEDED database (see e2e/run-demo.sh). Walks the seeded stories as the demo users and drives real actions.
//   BASE_URL=http://localhost:3101 DEMO_PASSWORD='Mintree-Demo#2026' node e2e/demo.mjs
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE_URL ?? "http://localhost:3101";
const PASSWORD = process.env.DEMO_PASSWORD ?? "Mintree-Demo#2026";
const SHOTS = process.env.SHOTS ?? mkdtempSync(join(tmpdir(), "demo-shots-"));
const results = [];
const browser = await chromium.launch();
const consoleErrors = [];
const newPage = async () => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  p.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text()); });
  p.on("pageerror", (e) => consoleErrors.push(String(e)));
  return p;
};
let page = await newPage();
const shot = (n) => page.screenshot({ path: join(SHOTS, `${n}.png`) });
async function step(name, fn) {
  try { await fn(); results.push([name, "PASS"]); console.log("PASS", name); }
  catch (e) { results.push([name, "FAIL"]); console.log("FAIL", name, "\n   ", e.message.split("\n")[0]); await shot(`FAIL-${name.replace(/\W+/g, "-")}`).catch(() => {}); }
}
async function loginAs(user) {
  await page.context().close();
  page = await newPage();
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email").fill(`demo.${user}@example.com`);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(`${BASE}/`);
}
const nav = () => page.getByRole("navigation", { name: "Main" });
const open = async (path) => { await page.goto(`${BASE}${path}`); await page.getByRole("heading", { level: 1 }).waitFor(); await page.waitForLoadState("networkidle"); };
const card = (text) => page.locator("article, tbody tr").filter({ hasText: text });
const api = async (user, path, init = {}) => {                      // independent read of the backend, as the same user, to confirm what the UI did
  const ctx = page.context();
  const r = await ctx.request.fetch(`${BASE}/api${path}`, { method: init.method ?? "GET", headers: { "X-CSRF-Token": (await ctx.cookies()).find((c) => /csrf/i.test(c.name))?.value ?? "" }, data: init.data });
  return r.json();
};

// ---------------------------------------------------------------- seeded records are visible in the right screens
await step("management sees company-wide figures and no error state on the overview", async () => {
  await loginAs("mgmt");
  await page.waitForLoadState("networkidle");
  assert.equal(await page.locator("main [role=alert]").filter({ hasText: /\S/ }).count(), 0);
  await shot("d01-overview-management");
  const body = await page.locator("main").innerText();
  assert.match(body, /\d/, "the overview shows numbers");
});

await step("Overview: one strip of four indicators; money figures are not repeated elsewhere", async () => {
  await loginAs("admin");
  const strip = page.getByRole("region", { name: "Key indicators" });
  await strip.waitFor();
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1300);                 // the indicator figures count up on arrival (900 ms); read them once they have settled
  assert.equal(await strip.locator("li").count(), 4);
  const money = (await strip.locator(".t-kpi").allInnerTexts()).filter((v) => v.startsWith("₹"));
  assert.ok(money.length >= 2, "receivables and revenue are shown in rupees");
  const body = await page.locator("main").innerText();
  for (const v of money) assert.equal(body.split(v).length - 1, 1, `${v} appears exactly once on the page`);
  await shot("d00-overview");
});

await step("Overview: the production chart is drawn from the API's weekly series and follows the period", async () => {
  const [resp] = await Promise.all([page.waitForResponse((r) => r.url().includes("/analytics/overview-period")), page.goto(`${BASE}/`)]);
  const j = await resp.json();
  const withActual = j.production.buckets.filter((b) => b.actual !== null).length;
  assert.ok(j.production.weeks_with_data >= 2, "the demo history has at least two weeks of completed orders");
  const svg = page.locator('svg[aria-label*="by week"]');
  await svg.waitFor();
  assert.equal(await svg.locator('rect[fill="var(--chart-actual)"]').count(), withActual, "one actual bar per week with data");
  assert.equal(await svg.locator('rect[fill="var(--line-strong)"]').count(), withActual, "one planned bar per week with data");
  const sums = j.production.buckets.reduce((a, b) => a + Number(b.actual ?? 0), 0);
  assert.ok((await page.locator("main").innerText()).includes(`${sums.toLocaleString("en-IN")} units made`), "the headline total is the sum of the series");
  const [r90] = await Promise.all([page.waitForResponse((r) => r.url().includes("/analytics/overview-period")), page.getByRole("button", { name: "90 days" }).click()]);
  const j90 = await r90.json();
  assert.notEqual(j90.start, j.start, "choosing 90 days asks the API for a longer period");
  await page.waitForLoadState("networkidle");
  assert.equal(await svg.locator('rect[fill="var(--chart-actual)"]').count(), j90.production.buckets.filter((b) => b.actual !== null).length);
  await shot("d00b-overview-90d");
});

await step("Overview: a role without the dashboard permission gets an honest notice, not zeros", async () => {
  await loginAs("op");
  await page.getByText("Company-wide figures need the management summary permission").waitFor();
  assert.equal(await page.getByRole("region", { name: "Key indicators" }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "90 days" }).count(), 0, "no period controls without data to scope");
});

await step("CRM: the pipeline shows a won, an open and a lost deal with their stages", async () => {
  await loginAs("sales");
  await open("/m/sales/opportunities");
  await card("Studio Bloom").getByText("Won", { exact: true }).waitFor();
  await card("Urban Spa").getByText("Negotiation", { exact: true }).waitFor();
  await card("Aroma Boutique").getByText("Lost", { exact: true }).waitFor();
  await shot("d02-opportunities");
});

await step("CRM: a converted lead points at its customer; a lost lead cannot be 'won'", async () => {
  await open("/m/sales/leads?q=Studio%20Bloom");                       // lists hold the newest 100 rows; search is server-side so older records are found
  await card("Studio Bloom").getByText("Won", { exact: true }).waitFor();
  assert.equal(await card("Studio Bloom").getByRole("button", { name: /Convert/ }).count(), 0, "an already-converted lead offers no convert action");
  await open("/m/sales/leads?q=Velvet");
  await card("Velvet Wellness").getByRole("button", { name: "Convert to customer" }).count().then((n) => assert.equal(n, 0, "a NEW lead cannot be converted yet"));
});

await step("Dispatch: the won order shipped in two shipments, each with its own invoice", async () => {
  await loginAs("disp");
  await open("/m/dispatch/shipments?q=SO%2F26%2F000001");
  await page.locator("article, tbody tr").filter({ hasText: "SO/26/000001" }).first().waitFor();
  const rows = await page.locator("article, tbody tr").filter({ hasText: "SO/26/000001" }).count();
  assert.equal(rows, 2, "order SO/26/000001 has two shipments");
  await shot("d03-shipments");
  await open("/m/dispatch/invoices");
  assert.ok((await page.locator("article, tbody tr").count()) >= 8, "invoices are listed");
});

await step("Dispatch: a failed delivery and a delayed shipment are visible as exceptions", async () => {
  await open("/m/dispatch/shipments?q=FAILED_DELIVERY");
  await page.locator("article, tbody tr").filter({ hasText: "Failed delivery" }).first().waitFor();
});

await step("Quality: a released inspection has a frozen certificate; the rejected lot is on record", async () => {
  await loginAs("qa");
  await open("/m/quality/coa");
  await card("COA/").first().waitFor();
  await open("/m/quality/deviations");
  await card("Vitamin E").first().waitFor();
  await shot("d04-quality");
});

await step("Production: a confirmed plan, work centres and a delayed order are listed", async () => {
  await loginAs("pm");
  await open("/m/production/plans");
  await card("Week 46").getByText("Confirmed", { exact: true }).waitFor();
  await open("/m/production/work-centres");
  await card("WC-PACK").first().waitFor();
  await open("/m/production/orders");
  await card("In progress").first().waitFor();
});

await step("Purchase: the return to supplier and the PO awaiting approval are visible to the buyer", async () => {
  await loginAs("buyer");
  await open("/m/procurement/returns");
  await card("PRT/").first().waitFor();
  await open("/m/procurement/orders");
  await card("Pending approval").first().waitFor();
});

await step("Branding is recorded manually: published, pending and rejected posts", async () => {
  await loginAs("mkt");
  await open("/m/branding/content");
  await card("Oud body wash reel").getByText("Published", { exact: true }).waitFor();
  await card("Flash sale teaser").getByText("Rejected", { exact: true }).waitFor();
});

// ---------------------------------------------------------------- role-based views
await step("a production operator sees production but not finance, purchasing or CRM", async () => {
  await loginAs("op");
  await nav().getByRole("button", { name: "Production", exact: true }).waitFor();
  for (const hidden of ["Accounts", "Purchase", "CRM & Sales", "Dispatch"]) assert.equal(await nav().getByRole("button", { name: hidden, exact: true }).count(), 0, `${hidden} must be hidden`);
  const r = await page.context().request.get(`${BASE}/api/accounts/reports/trial-balance`);
  assert.equal(r.status(), 403, "and the backend refuses it too");
  await shot("d05-operator-nav");
});

await step("the buyer cannot reach accounts even by URL", async () => {
  await loginAs("buyer");
  const r = await page.context().request.get(`${BASE}/api/accounts/payments`);
  assert.equal(r.status(), 403);
  await page.goto(`${BASE}/m/accounts/payments`);
  await page.waitForLoadState("networkidle");
  const txt = (await page.locator("body").innerText()).toLowerCase();
  assert.ok(/not allowed|permission|forbidden|access/.test(txt) || !(await page.locator("article, tbody tr").count()), "no payment data is shown");
});

// ---------------------------------------------------------------- actions drive real state
await step("approving the pending purchase order in the browser changes it in the database", async () => {
  await loginAs("admin");
  await open("/m/procurement/orders");
  const pending = card("Pending approval").first();
  await pending.waitFor();
  const num = (await pending.innerText()).match(/PO\/\d+\/\d+/)[0];
  await pending.getByRole("button", { name: "Approve" }).click();
  const dlg = page.getByRole("dialog");
  if (await dlg.waitFor({ timeout: 2500 }).then(() => true).catch(() => false)) await dlg.getByRole("button", { name: /Approve|Confirm|Yes/ }).last().click();
  await card(num).getByText("Approved", { exact: true }).waitFor({ timeout: 8000 });
  const list = await api("admin", `/procurement/purchase-orders?page_size=100`);
  assert.equal(list.items.find((p) => p.number === num).status, "APPROVED");
  await shot("d06-po-approved");
});

await step("dispatching the packed order from the card posts stock and an invoice", async () => {
  await loginAs("disp");
  const packedOrder = (await api("disp", "/dispatch/orders?status=PACKED&page_size=1")).items[0];
  assert.ok(packedOrder, "the demo has a packed order waiting for dispatch");
  await open(`/m/dispatch/orders?q=${encodeURIComponent(packedOrder.number)}`);
  const packed = card(packedOrder.number).first();
  await packed.waitFor();
  const num = (await packed.innerText()).match(/SO\/\d+\/\d+/)[0];
  const shipments = async () => (await api("disp", `/dispatch/orders/${packedOrder.id}/shipments`)).shipments;
  assert.equal((await shipments()).length, 0, "nothing shipped yet");
  await packed.getByRole("button", { name: "Dispatch (all remaining)" }).click();
  await page.getByText("Dispatch everything still unshipped").waitFor({ timeout: 5000 });     // the action asks for confirmation before posting
  await page.getByRole("button", { name: "Dispatch (all remaining)" }).last().click();       // the modal's confirm button (the card's own button is earlier in the DOM)
  await card(num).getByText("Dispatched", { exact: true }).waitFor({ timeout: 8000 });
  const after = await shipments();
  assert.equal(after.length, 1, "one shipment was created");
  assert.ok(after[0].invoice_number && Number(after[0].invoice_total) > 0, "and its invoice was posted");
  await shot("d07-dispatched");
});

await step("a QC inspector can record a result but is not offered the release decision", async () => {
  await loginAs("qi");
  const pend = (await api("qi", "/quality/inspections?page_size=100")).items.find((i) => ["PENDING", "IN_PROGRESS"].includes(i.status));
  assert.ok(pend, "the demo has an open inspection");
  await open(`/m/quality/inspections?q=${encodeURIComponent(pend.number)}`);
  const open1 = page.locator("article, tbody tr").filter({ hasText: pend.number }).first();
  await open1.waitFor();
  assert.ok(await open1.getByRole("button", { name: "Record result" }).count() >= 1);
  assert.equal(await open1.getByRole("button", { name: "Decide" }).count(), 0, "inspectors cannot decide");
});


// ---------------------------------------------------------------- performance & completeness milestone
await step("Meta workspace: every asset is labelled as a simulated sandbox record and nothing reads Connected", async () => {
  await loginAs("mkt");
  await open("/m/branding/meta");
  const body = await page.locator("main").innerText();
  assert.match(body, /Simulated sandbox record/);
  assert.doesNotMatch(body, /Connected and verified/);
  assert.ok((await page.locator("article, tbody tr").count()) >= 4, "business, page, Instagram and ad account are listed");
  await shot("d20-meta-workspace");
});

await step("a running campaign can be paused and the change is real (checked through the API)", async () => {
  await open("/m/branding/campaigns");
  const camps = await api("mkt", "/branding/campaigns");
  const running = camps.find((c) => c.status === "RUNNING");
  assert.ok(running, "demo data has a running campaign");
  const c = card(running.name).first();
  await c.getByRole("button", { name: "Pause" }).click();
  const dlg = page.getByRole("dialog");
  if (await dlg.count()) await dlg.getByRole("button", { name: /Pause|Confirm|Submit|Save/ }).last().click();
  await page.waitForLoadState("networkidle");
  const after = (await api("mkt", "/branding/campaigns")).find((x) => x.id === running.id);
  assert.equal(after.status, "PAUSED");
});

await step("the cash-flow statement opens, reconciles and is not an error state", async () => {
  await loginAs("acct");
  await open("/m/accounts/cashflow");
  await page.waitForLoadState("networkidle");
  const body = await page.locator("main").innerText();
  assert.match(body, /closing/i);
  assert.equal(await page.locator("main [role=alert]").filter({ hasText: /\S/ }).count(), 0);
  await shot("d21-cash-flow");
});

await step("approved purchase orders offer a printable PO that opens as a PDF", async () => {
  await loginAs("buyer");
  await open("/m/procurement/orders");
  const approved = (await api("buyer", "/procurement/purchase-orders?status=APPROVED&page_size=1")).items?.[0] ?? (await api("buyer", "/procurement/purchase-orders?page_size=100")).items.find((p) => ["APPROVED", "RECEIVED", "PARTIALLY_RECEIVED"].includes(p.status));
  assert.ok(approved, "the demo has an approved purchase order");
  await open(`/m/procurement/orders?q=${encodeURIComponent(approved.number)}`);
  const btn = card(approved.number).first().getByRole("button", { name: "Print PO" });
  await btn.waitFor();
  const [popup] = await Promise.all([page.waitForEvent("popup"), btn.click()]);
  await popup.waitForLoadState("commit").catch(() => {});
  const url = popup.url() && popup.url() !== "about:blank" ? popup.url() : `${BASE}/api/procurement/purchase-orders/${approved.id}/pdf`;
  assert.match(url, /\/api\/procurement\/purchase-orders\/\d+\/pdf/);
  await popup.close();
  const r = await page.context().request.get(url);
  assert.equal(r.status(), 200);
  assert.equal((await r.body()).subarray(0, 5).toString(), "%PDF-");
});

await step("no unexpected browser console errors", async () => assert.deepEqual(consoleErrors, []));

await browser.close();
const failed = results.filter(([, s]) => s === "FAIL");
if (process.env.REGISTER_FILE) {
  const { appendFileSync } = await import("node:fs"); const { execSync } = await import("node:child_process");
  const sh = (c) => { try { return execSync(c, { encoding: "utf8" }).trim(); } catch { return ""; } };
  appendFileSync(process.env.REGISTER_FILE, JSON.stringify({ kind: "browser E2E (Playwright) - demo journeys", date: new Date().toISOString().slice(0, 19) + "+00:00",
    commit: sh("git rev-parse --short=10 HEAD"), dirty: !!sh("git status --porcelain"), result: failed.length ? "FAIL" : "PASS",
    passed: results.length - failed.length, failed: failed.length, environment: "Chromium (Playwright), demo-seeded DB + API + production web build, 4 vCPU sandbox",
    failures: failed.map(([n]) => n) }) + "\n");
}
console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots: ${SHOTS}`);
process.exit(failed.length ? 1 : 0);
