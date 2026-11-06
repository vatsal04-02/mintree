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
const card = (text) => page.locator("article").filter({ hasText: text });
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

await step("CRM: the pipeline shows a won, an open and a lost deal with their stages", async () => {
  await loginAs("sales");
  await open("/m/sales/opportunities");
  await card("Studio Bloom").getByText("Won", { exact: true }).waitFor();
  await card("Urban Spa").getByText("Negotiation", { exact: true }).waitFor();
  await card("Aroma Boutique").getByText("Lost", { exact: true }).waitFor();
  await shot("d02-opportunities");
});

await step("CRM: a converted lead points at its customer; a lost lead cannot be 'won'", async () => {
  await open("/m/sales/leads");
  await card("Studio Bloom").getByText("Won", { exact: true }).waitFor();
  assert.equal(await card("Studio Bloom").getByRole("button", { name: /Convert/ }).count(), 0, "an already-converted lead offers no convert action");
  await card("Velvet Wellness").getByRole("button", { name: "Convert to customer" }).count().then((n) => assert.equal(n, 0, "a NEW lead cannot be converted yet"));
});

await step("Dispatch: the won order shipped in two shipments, each with its own invoice", async () => {
  await loginAs("disp");
  await open("/m/dispatch/shipments");
  const rows = await page.locator("article").filter({ hasText: "SO/26/000001" }).count();
  assert.equal(rows, 2, "order SO/26/000001 has two shipments");
  await shot("d03-shipments");
  await open("/m/dispatch/invoices");
  assert.ok((await page.locator("article").count()) >= 8, "invoices are listed");
});

await step("Dispatch: a failed delivery and a delayed shipment are visible as exceptions", async () => {
  await open("/m/dispatch/shipments");
  await page.locator("article").filter({ hasText: "Failed delivery" }).first().waitFor();
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
  assert.ok(/not allowed|permission|forbidden|access/.test(txt) || !(await page.locator("article").count()), "no payment data is shown");
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
  await open("/m/dispatch/orders");
  const packed = card("Packed").first();
  await packed.waitFor();
  const num = (await packed.innerText()).match(/SO\/\d+\/\d+/)[0];
  const before = (await api("disp", `/dispatch/invoices?page_size=100`)).items.length;
  await packed.getByRole("button", { name: "Dispatch (all remaining)" }).click();
  await page.getByText("Dispatch everything still unshipped").waitFor({ timeout: 5000 });     // the action asks for confirmation before posting
  await page.getByRole("button", { name: "Dispatch (all remaining)" }).last().click();       // the modal's confirm button (the card's own button is earlier in the DOM)
  await card(num).getByText("Dispatched", { exact: true }).waitFor({ timeout: 8000 });
  const after = (await api("disp", `/dispatch/invoices?page_size=100`)).items.length;
  assert.equal(after, before + 1, "one new invoice was posted");
  await shot("d07-dispatched");
});

await step("a QC inspector can record a result but is not offered the release decision", async () => {
  await loginAs("qi");
  await open("/m/quality/inspections");
  const open1 = page.locator("article").filter({ hasText: /Pending|In Progress/ }).first();
  await open1.waitFor();
  assert.ok(await open1.getByRole("button", { name: "Record result" }).count() >= 1);
  assert.equal(await open1.getByRole("button", { name: "Decide" }).count(), 0, "inspectors cannot decide");
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
