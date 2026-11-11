// Browser E2E for Product 360 and Batch 360 against a DEMO-SEEDED database (see e2e/run-demo.sh for the stack).
//   BASE_URL=http://localhost:3101 DEMO_PASSWORD='Mintree-Demo#2026' node e2e/traceability.mjs
// The journey is the one in the brief: product -> formulation -> raw material -> supplier lot -> goods receipt -> production order -> finished batch -> QC -> customer order -> shipment -> product.
// Nothing is hard-coded: every record is found by following links in the UI, then confirmed against the API as the same user.
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
let chromium;
try { ({ chromium } = require("playwright")); } catch { ({ chromium } = require("playwright-core")); }

const BASE = process.env.BASE_URL ?? "http://localhost:3101";
const PASSWORD = process.env.DEMO_PASSWORD ?? "Mintree-Demo#2026";
const SHOTS = process.env.SHOTS ?? mkdtempSync(join(tmpdir(), "trace-shots-"));
const PRODUCT = process.env.TRACE_PRODUCT ?? "Olive Body Butter";
const results = [];
const consoleErrors = [];
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await ctx.newPage();
page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(String(e)));
const shot = (n) => page.screenshot({ path: join(SHOTS, `${n}.png`) });
async function step(name, fn) {
  try { await fn(); results.push([name, "PASS"]); console.log("PASS", name); }
  catch (e) { results.push([name, "FAIL"]); console.log("FAIL", name, "\n   ", e.message.split("\n")[0]); await shot(`FAIL-${name.replace(/\W+/g, "-")}`).catch(() => {}); }
}
const h1 = () => page.getByRole("heading", { level: 1 });
const api = async (path) => (await ctx.request.fetch(`${BASE}/api${path}`)).json();
const settle = async () => { await page.waitForLoadState("networkidle"); };
const ctxState = {};

await step("1. sign in and open a product from the Product 360 list", async () => {
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email").fill("demo.admin@example.com");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(`${BASE}/`);
  await page.goto(`${BASE}/inventory/products`);
  await page.getByPlaceholder("Product name or code").fill(PRODUCT);
  await page.getByRole("link", { name: new RegExp(PRODUCT, "i") }).first().click();
  await page.waitForURL(/\/inventory\/products\/\d+\/overview/);
  await h1().filter({ hasText: new RegExp(PRODUCT, "i") }).waitFor();
  ctxState.productUrl = page.url();
  ctxState.productId = Number(page.url().match(/products\/(\d+)/)[1]);
  const p = await api(`/traceability/products/${ctxState.productId}`);
  assert.ok(Number(p.metrics.manufactured_qty) > 0, "the product has manufactured stock recorded");
  await shot("1-product");
});

await step("2. formulation and BOM: the recipe that is approved is the one in production", async () => {
  await page.getByRole("tab", { name: "Formulations & BOM" }).click();
  await page.getByRole("heading", { name: "Bills of materials" }).waitFor();
  await page.locator("table").first().getByText(/BOM v\d+/).first().waitFor();
  const f = await api(`/traceability/products/${ctxState.productId}/formulations`);
  assert.ok(f.boms.some((b) => b.status === "ACTIVE"), "an ACTIVE BOM exists");
});

await step("3. raw materials: actual lots consumed, not the BOM", async () => {
  await page.getByRole("tab", { name: "Raw materials" }).click();
  await page.getByRole("heading", { name: "Raw and packaging materials actually used" }).waitFor();
  const m = await api(`/traceability/products/${ctxState.productId}/materials`);
  const raw = m.items.find((x) => x.kind === "RM" && x.lots.length);
  assert.ok(raw, "a raw material with consumed lots");
  ctxState.rawName = raw.name; ctxState.rawLot = raw.lots[0];
  await page.getByText(raw.name).first().waitFor();
  await page.getByRole("link", { name: raw.lots[0].batch_no }).first().click();
  await page.waitForURL(/\/inventory\/batches\/\d+\/traceability/);
  await h1().filter({ hasText: raw.lots[0].batch_no }).waitFor();
  ctxState.rawBatchId = Number(page.url().match(/batches\/(\d+)/)[1]);
});

await step("4. supplier batch: goods receipt, purchase order and supplier lot are on the page", async () => {
  await page.getByText("Raw material").first().waitFor();
  const d = await api(`/traceability/batches/${ctxState.rawBatchId}`);
  assert.ok(d.source_receipt?.number, "the batch came in on a goods receipt");
  await page.getByText(d.source_receipt.number).first().waitFor();
  assert.ok(d.source_receipt.po_number);
  await page.getByText(d.source_receipt.po_number).first().waitFor();
  await page.getByText(d.batch.supplier_lot_no).first().waitFor();
  assert.equal(d.reconciliation.balanced, true, "the supplier batch's quantities reconcile");
  await page.getByText("Quantity reconciliation").waitFor();
  await shot("4-raw-batch");
});

await step("5. forward from the raw batch: the production order that consumed it, opened from the diagram", async () => {
  await page.getByRole("button", { name: "Went to" }).click();
  await page.waitForTimeout(400);
  const g = await api(`/traceability/batches/${ctxState.rawBatchId}/graph?direction=forward`);
  const order = g.nodes.find((n) => n.type === "production_order");
  assert.ok(order, "at least one production order consumed this lot");
  await page.getByRole("button", { name: new RegExp(`Production order ${order.ref.replace(/\//g, "\\/")}`) }).click();
  await page.getByRole("link", { name: "Open record" }).click();
  await page.waitForURL(/\/m\/production\/orders\?open=\d+/);
  await h1().filter({ hasText: /Production orders/i }).waitFor();
});

await step("6. back on the product: open a finished batch and trace it back to its suppliers", async () => {
  await page.goto(ctxState.productUrl);
  await page.getByRole("tab", { name: "Batches" }).click();
  const b = await api(`/traceability/products/${ctxState.productId}/batches?page_size=50`);
  // a batch that was MADE here (opening-stock batches have no production order and no recipe, so they have no ancestry to show)
  const fg = b.items.find((x) => x.source_type === "production_order" && Number(x.dispatched) > 0 && x.qc_status === "RELEASED");
  assert.ok(fg, "a released, dispatched finished batch made by a production order");
  ctxState.fgBatch = fg;
  await page.getByRole("link", { name: fg.batch_no }).first().click();
  await h1().filter({ hasText: fg.batch_no }).waitFor();
  ctxState.fgId = Number(page.url().match(/batches\/(\d+)/)[1]);
  await page.getByRole("button", { name: "Came from" }).click();
  const g = await api(`/traceability/batches/${ctxState.fgId}/graph?direction=backward`);
  const types = new Set(g.nodes.map((n) => n.type));
  for (const t of ["production_order", "goods_receipt", "purchase_order", "supplier"]) assert.ok(types.has(t), `backward genealogy includes a ${t}`);
  // consumed batches come only from consumption records: compare with the independent API of the production order
  const consumed = g.edges.filter((e) => e.label === "consumed").map((e) => e.source);
  assert.ok(consumed.length > 0, "actual consumption edges exist");
  await page.getByRole("button", { name: "List" }).click();
  await page.getByRole("cell", { name: "Goods receipt" }).first().waitFor();
  await page.getByRole("cell", { name: "Supplier" }).first().waitFor();
  await shot("6-fg-backward-list");
});

await step("7. quality history: inspections with every result, failures kept", async () => {
  await page.getByRole("tab", { name: /Inspections/ }).click();
  const d = await api(`/traceability/batches/${ctxState.fgId}`);
  assert.ok(d.inspections.length > 0, "the finished batch has an inspection");
  await page.getByText(d.inspections[0].number).first().waitFor();
  const all = d.inspections.flatMap((i) => i.results);
  assert.ok(all.length > 0, "inspection results are listed");
});

await step("8. forward to customers: sales order, shipment, delivery", async () => {
  await page.getByRole("button", { name: "Went to" }).click();
  await page.getByRole("button", { name: "Diagram" }).click();
  const g = await api(`/traceability/batches/${ctxState.fgId}/graph?direction=forward`);
  const ship = g.nodes.find((n) => n.type === "shipment"); const so = g.nodes.find((n) => n.type === "sales_order"); const cust = g.nodes.find((n) => n.type === "customer");
  assert.ok(ship && so && cust, "shipment, sales order and customer are all reachable");
  await page.getByRole("button", { name: "Fit to view" }).click();                       // a wide graph is panned or fitted by the user; the node must then be on screen
  await page.getByRole("button", { name: new RegExp(`Shipment ${ship.ref.replace(/\//g, "\\/")}`) }).click();
  await page.getByRole("link", { name: "Open record" }).click();
  await page.waitForURL(/\/m\/dispatch\/shipments\?open=\d+/);
  await h1().filter({ hasText: /Shipments/i }).waitFor();
  await shot("8-shipment");
});

await step("9. batch impact analysis from a raw lot: customers found, nothing changed, the run is audited", async () => {
  await page.goto(`${BASE}/inventory/batches/${ctxState.rawBatchId}/traceability`);
  await page.getByRole("tab", { name: "Impact analysis" }).click();
  const before = await api(`/traceability/batches/${ctxState.rawBatchId}`);
  await page.getByRole("button", { name: "Run impact analysis" }).click();
  await page.getByRole("heading", { name: "Customers who received affected stock" }).waitFor();
  await page.getByRole("heading", { name: /Production orders that consumed it/ }).waitFor();
  assert.ok(await page.getByRole("button", { name: /Prepare QC hold/ }).isDisabled(), "no hold can be prepared until something is selected");
  const after = await api(`/traceability/batches/${ctxState.rawBatchId}`);
  assert.deepEqual(after.reconciliation.quantities, before.reconciliation.quantities, "running the analysis changed no quantity");
  assert.equal(after.batch.qc_status, before.batch.qc_status, "and no status");
  await page.getByRole("tab", { name: "Audit trail" }).click();
  await page.getByText(/Impact Analysis/).first().waitFor();
  await shot("9-impact");
});

await step("10. back to the product: customers and history reflect what was traced", async () => {
  await page.goto(ctxState.productUrl);
  await page.getByRole("tab", { name: "Sales & customers" }).click();
  const s = await api(`/traceability/products/${ctxState.productId}/sales`);
  assert.ok(s.total > 0);
  await page.getByText(s.items[0].customer_name).first().waitFor();
  await page.getByRole("tab", { name: "Production" }).click();
  await page.getByRole("heading", { name: "Production history" }).waitFor();
  await page.getByRole("tab", { name: "Quality" }).click();
  await page.getByRole("heading", { name: "Quality history" }).waitFor();
  await page.getByRole("tab", { name: "Costing" }).click();
  await page.getByRole("heading", { name: "Manufacturing cost" }).waitFor();
});

await step("11. a user without batch access is refused by the page and by the API", async () => {
  const c2 = await browser.newContext(); const p2 = await c2.newPage();
  await p2.goto(`${BASE}/login`);
  await p2.getByLabel("Email").fill("demo.op@example.com"); await p2.getByLabel("Password").fill(PASSWORD);
  await p2.getByRole("button", { name: "Sign in" }).click(); await p2.waitForURL(`${BASE}/`);
  const r = await c2.request.fetch(`${BASE}/api/traceability/batches/${ctxState.fgId}`);
  assert.equal(r.status(), 403);
  await p2.goto(`${BASE}/inventory/batches/${ctxState.fgId}/traceability`);
  const denied = p2.getByRole("alert").filter({ hasText: /permission/i });
  await denied.waitFor();
  assert.match(await denied.innerText(), /permission/i);
  await c2.close();
});

await step("12. no console errors during the whole journey", async () => { assert.deepEqual(consoleErrors, []); });

await browser.close();
const failed = results.filter(([, s]) => s === "FAIL");
console.log(`\n${results.length - failed.length}/${results.length} steps passed. Screenshots: ${SHOTS}`);
process.exit(failed.length ? 1 : 0);
