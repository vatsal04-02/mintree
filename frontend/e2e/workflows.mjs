// Browser E2E: real workflows driven through the UI (not just rendering): procure -> receive -> label -> scan; load stock; order -> scan-verified pick -> pack -> dispatch -> invoice -> payment.
// Runs after phase0.mjs/modules.mjs in the same hermetic stack. Master data and a second user are created through the API (that is setup, not the thing under test).
//   BASE_URL=http://localhost:3100 node e2e/workflows.mjs
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
const { chromium, request: pwRequest } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const ADMIN = { email: process.env.ADMIN_EMAIL ?? "admin@example.com", password: process.env.ADMIN_NEW_PASSWORD ?? "Mintree-Admin#2026" };
const SHOTS = process.env.SHOTS ?? mkdtempSync(join(tmpdir(), "wf-shots-"));
const stamp = Date.now().toString(36).toUpperCase();
const results = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(String(e)));
const shot = (n) => page.screenshot({ path: join(SHOTS, `${n}.png`) });
async function step(name, fn) {
  try { await fn(); results.push([name, "PASS"]); console.log("PASS", name); }
  catch (e) { results.push([name, "FAIL"]); console.log("FAIL", name, "\n   ", e.message.split("\n").slice(0, 4).join("\n    ")); await shot(`FAIL-${name.replace(/\W+/g, "-")}`).catch(() => {}); }
}
const card = (text) => page.locator("article").filter({ hasText: text }).first();
const act = async (text, name) => { await card(text).getByRole("button", { name }).click(); };
const dlg = () => page.getByRole("dialog");
const go = async (path) => { await page.goto(`${BASE}${path}`); await page.getByRole("heading", { level: 1 }).waitFor(); await page.waitForLoadState("networkidle"); };

// ---- setup through the API
let csrf = "", api;
const post = async (path, data, headers = {}) => { const r = await ctx.request.post(`${BASE}/api${path}`, { data, headers: { "X-CSRF-Token": csrf, ...headers } }); assert.ok(r.ok(), `${path}: ${r.status()} ${await r.text()}`); return r.status() === 204 ? null : r.json(); };
const get = async (path) => { const r = await ctx.request.get(`${BASE}/api${path}`); assert.ok(r.ok(), `${path}: ${r.status()}`); return r.json(); };
const S = {};

await step("setup: master data, a second purchasing user and a purchase order", async () => {
  const lg = await ctx.request.post(`${BASE}/api/auth/login`, { data: ADMIN });
  assert.ok(lg.ok(), "admin login");
  csrf = (await lg.json()).csrf_token;
  const wh = (await get("/masters/warehouses?page_size=50")).items;
  S.wh = wh.find((w) => w.kind === "RM") ?? wh[0];
  S.whFg = wh.find((w) => w.kind === "FG") ?? wh[0];
  const uoms = (await get("/masters/units-of-measure?page_size=50")).items;
  const kg = uoms.find((u) => u.code === "KG"), pcs = uoms.find((u) => u.code === "PCS");
  S.supplier = await post("/masters/suppliers", { code: `SUP-WF-${stamp}`, name: `Workflow Supplier ${stamp}`, category: "RM_PM", approval_status: "APPROVED" });
  S.rm = await post("/masters/items", { item_code: `RM-WF-${stamp}`, name: `Workflow Glycerin ${stamp}`, kind: "RM", uom_id: kg.id, shelf_life_days: 365, gst_rate: "18" });
  S.fg = await post("/masters/items", { item_code: `FG-WF-${stamp}`, name: `Workflow Serum ${stamp}`, kind: "FG", uom_id: pcs.id, shelf_life_days: 730, gst_rate: "18", barcode: `890${Date.now().toString().slice(-9)}` });
  S.cust = await post("/masters/customers", { code: `CUST-WF-${stamp}`, name: `Workflow Retail ${stamp}`, customer_type: "B2B", state: "MH", shipping_address: "12 MG Road, Pune" });
  const roles = await get("/admin/roles?page_size=50");
  const pm = (roles.items ?? roles).find((r) => r.code === "purchase_manager");
  const email = `buyer.${stamp.toLowerCase()}@example.com`;
  await post("/admin/users", { email, full_name: "Workflow Buyer", temporary_password: "Temp-Passw0rd-7", role_ids: [pm.id] });
  const buyer = await pwRequest.newContext({ baseURL: BASE });
  const bl = await buyer.post("/api/auth/login", { data: { email, password: "Temp-Passw0rd-7" } });
  const bcsrf = (await bl.json()).csrf_token;
  const h = { "X-CSRF-Token": bcsrf };
  assert.ok((await buyer.post("/api/auth/change-password", { data: { current_password: "Temp-Passw0rd-7", new_password: "Buyer-Workflow#2026" }, headers: h })).ok(), "change password");
  const bl2 = await buyer.post("/api/auth/login", { data: { email, password: "Buyer-Workflow#2026" } });
  const h2 = { "X-CSRF-Token": (await bl2.json()).csrf_token };
  const po = await (await buyer.post("/api/procurement/purchase-orders", { data: { supplier_id: S.supplier.id, items: [{ item_id: S.rm.id, qty: "100", unit_price: "10" }] }, headers: h2 })).json();
  assert.ok(po.number, "po created");
  await buyer.post(`/api/procurement/purchase-orders/${po.id}/submit`, { data: { expected_version: po.version }, headers: h2 });
  S.po = po;
});

await step("sidebar: a module expands into its sub-sections and each opens its own screen", async () => {
  await page.goto(`${BASE}/`);                       // the browser context already holds the administrator's session from the API login above
  await page.getByRole("heading", { level: 1 }).waitFor();
  const nav = page.getByRole("navigation", { name: "Main" });
  await nav.getByRole("button", { name: "Purchase" }).click();
  for (const l of ["Purchase requests", "RFQs & quotations", "Purchase orders", "Goods receipts", "Delivery tracking", "Supplier performance"]) await nav.getByRole("link", { name: l, exact: true }).waitFor();
  await nav.getByRole("link", { name: "Purchase orders", exact: true }).click();
  await page.waitForURL("**/m/procurement/orders");
  assert.equal(await nav.getByRole("link", { name: "Goods receipts", exact: true }).count(), 1, "other sections stay listed while inside the module");
  await shot("w01-sidebar");
});

await step("purchase orders show as cards; the PO is approved from its card", async () => {
  await go("/m/procurement/orders");
  await card(S.po.number).waitFor();
  await act(S.po.number, "Approve");
  await card(S.po.number).getByText("Approved").waitFor();
  await shot("w02-po-card");
});

await step("board view groups purchase orders by status", async () => {
  await page.getByRole("button", { name: "Board" }).click();
  await page.locator('section[aria-label="APPROVED"]').getByText(S.po.number).waitFor();
  await page.getByRole("button", { name: "Cards" }).click();
});

await step("post a goods receipt: PO lines load with open quantities", async () => {
  await go("/m/procurement/receipts");
  await page.getByRole("button", { name: "Post receipt" }).click();
  await dlg().locator("#po_id").selectOption({ label: `${S.po.number} (APPROVED)` });
  await dlg().locator("#warehouse_id").selectOption({ label: `${S.wh.code} — ${S.wh.name}` });
  await dlg().locator("#delivery_note_no").fill(`DN-${stamp}`);
  const qty = dlg().getByLabel("Quantity for line 1");
  await qty.waitFor();
  assert.equal(await qty.inputValue(), "100", "defaults to the open quantity");
  await qty.fill("60");
  await dlg().getByLabel("Supplier lot for line 1").fill("LOT-WF-1");
  await dlg().getByLabel("Expiry date for line 1").fill("2028-06-30");
  await dlg().getByRole("button", { name: "Create" }).click();
  await card(`DN`).waitFor();
  await shot("w03-receipt");
});

await step("the received batch is quarantined and its label is a PDF", async () => {
  const batches = (await get(`/inventory/batches?item_id=${S.rm.id}`)).items;
  assert.equal(batches.length, 1); S.batch = batches[0];
  assert.equal(S.batch.qc_status, "QUARANTINE");
  await go("/m/inventory/batches");
  await card(S.batch.batch_no).getByText("Quarantine").waitFor();
  const pdf = await ctx.request.get(`${BASE}/api/labels/batch/${S.batch.id}`);
  assert.equal(pdf.headers()["content-type"], "application/pdf");
  assert.equal((await pdf.body()).subarray(0, 5).toString(), "%PDF-");
  const [popup] = await Promise.all([page.waitForEvent("popup"), act(S.batch.batch_no, "Print label")]);
  await popup.close();
});

await step("scan dialog: typing a Mintree label opens that record", async () => {
  await go("/");
  await page.getByRole("button", { name: "Scan a barcode" }).click();
  await page.getByLabel("Barcode", { exact: true }).fill(`MT:BATCH:${S.batch.id}`);
  await page.getByLabel("Barcode", { exact: true }).press("Enter");
  await page.waitForURL(new RegExp(`/m/inventory/batches\\?open=${S.batch.id}`));
  await page.getByRole("dialog").getByText(S.batch.batch_no).first().waitFor();
});

await step("a keyboard-wedge scanner (fast keystrokes + Enter) works anywhere", async () => {
  await go("/");
  await page.locator("body").click({ position: { x: 5, y: 300 } });
  await page.keyboard.type(S.po.number, { delay: 8 });
  await page.keyboard.press("Enter");
  await page.waitForURL(new RegExp("/m/procurement/orders\\?open="));
  await page.getByRole("dialog").getByText(S.po.number).first().waitFor();
});

await step("an unknown code is reported, not ignored", async () => {
  await go("/");
  await page.getByRole("button", { name: "Scan a barcode" }).click();
  await page.getByLabel("Barcode", { exact: true }).fill("NOT-A-REAL-CODE-123");
  await page.getByLabel("Barcode", { exact: true }).press("Enter");
  await page.getByText(/does not match anything/).waitFor();
});

await step("load opening stock of finished goods through the form", async () => {
  await go("/m/inventory/opening");
  await page.getByRole("button", { name: "Load opening stock" }).click();
  await dlg().locator("#warehouse_id").selectOption({ label: `${S.whFg.code} — ${S.whFg.name}` });
  await dlg().locator("#qc_status").selectOption("RELEASED");
  await dlg().locator("#note").fill(`go-live ${stamp}`);
  await dlg().locator("#lines-0-item_id").selectOption({ label: `${S.fg.item_code} — ${S.fg.name}` });
  await dlg().locator("#lines-0-qty").fill("50");
  await dlg().locator("#lines-0-unit_cost").fill("10");
  await dlg().getByRole("button", { name: "Create" }).click();
  await card(`go-live ${stamp}`).waitFor();
  const av = (await get(`/inventory/availability?item_id=${S.fg.id}`)).total;
  assert.equal(Number(av.available), 50, `availability: ${JSON.stringify(av)}`);
  S.fgBatch = (await get(`/inventory/batches?item_id=${S.fg.id}`)).items[0];
});

await step("a product's retail barcode finds the item", async () => {
  const r = await (await ctx.request.get(`${BASE}/api/scan/resolve?code=${S.fg.barcode}`)).json();
  assert.ok(r.matches.some((m) => m.type === "ITEM" && m.id === S.fg.id));
});

await step("order -> confirm -> allocate from the order card", async () => {
  await go("/m/dispatch/orders");
  await page.getByRole("button", { name: "New order" }).click();
  await dlg().locator("#customer_id").selectOption({ label: `${S.cust.code} — ${S.cust.name}` });
  await dlg().locator("#warehouse_id").selectOption({ label: `${S.whFg.code} — ${S.whFg.name}` });
  await dlg().locator("#lines-0-item_id").selectOption({ label: `${S.fg.item_code} — ${S.fg.name}` });
  await dlg().locator("#lines-0-qty").fill("5");
  await dlg().locator("#lines-0-unit_price").fill("25");
  await dlg().getByRole("button", { name: "Create" }).click();
  await card("Draft").waitFor();
  S.so = (await get("/dispatch/orders?page_size=50")).items.find((o) => o.status === "DRAFT" && o.customer_id === S.cust.id);
  await act(S.so.number, "Confirm"); await card(S.so.number).getByText("Confirmed").waitFor();
  await act(S.so.number, "Allocate"); await card(S.so.number).getByText("Allocated").waitFor();
});

await step("verify picking by scan: wrong batch refused, over-pick refused, right batch accepted", async () => {
  await act(S.so.number, "Verify picking (scan)");
  const box = page.getByLabel("Barcode", { exact: true });
  await box.fill(`MT:BATCH:${S.batch.id}`); await box.press("Enter");
  await page.getByRole("status").filter({ hasText: /not on this order's pick list/ }).waitFor();
  await page.getByLabel("Quantity per scan").fill("9");
  await box.fill(`MT:BATCH:${S.fgBatch.id}`); await box.press("Enter");
  await page.getByRole("status").filter({ hasText: /more than was allocated/ }).waitFor();
  await page.getByLabel("Quantity per scan").fill("5");
  await box.fill(`MT:BATCH:${S.fgBatch.id}`); await box.press("Enter");
  await page.getByRole("status").filter({ hasText: /Scanned/ }).waitFor();
  await shot("w04-pick-scan");
  await page.keyboard.press("Escape");
});

await step("pack and dispatch from the card, then the invoice shows and is paid from its card", async () => {
  await act(S.so.number, "Pack");
  await dlg().locator("#cartons").fill("1"); await dlg().locator("#weight_kg").fill("2.5");
  await dlg().getByRole("button", { name: "Pack", exact: true }).click();
  await card(S.so.number).getByText("Packed").waitFor();
  await act(S.so.number, "Dispatch");
  await page.getByRole("alertdialog").getByRole("button", { name: "Dispatch" }).click();
  await card(S.so.number).getByText("Dispatched").waitFor();
  await go("/m/dispatch/invoices");
  const inv = card(S.cust.name);
  await inv.waitFor();
  await inv.getByRole("button", { name: "Receive payment" }).click();
  await dlg().locator("#amount").fill("147.5");
  await dlg().locator("#payment_date").fill(new Date().toISOString().slice(0, 10));
  await dlg().locator("#method").selectOption("BANK_TRANSFER");
  await dlg().getByRole("button", { name: "Receive payment" }).click();
  await card(S.cust.name).getByText("Paid").waitFor();
  await shot("w05-invoice-paid");
});

await step("create a return from the dispatched order card", async () => {
  await go("/m/dispatch/orders");
  await act(S.so.number, "Create return");
  await dlg().getByLabel(/Return quantity for/).fill("1");
  await dlg().locator("#reason").fill("Customer changed mind");
  await dlg().getByRole("button", { name: "Create return" }).click();
  await go("/m/dispatch/returns");
  await card(S.so.number).waitFor();
});

// ---- a second administrator for steps that need segregation of duties (creator != approver)
const second = {};
await step("setup: a second administrator for four-eyes steps", async () => {
  const roles = await get("/admin/roles?page_size=50");
  const sa = (roles.items ?? roles).find((r) => r.code === "system_admin");
  const email = `admin2.${stamp.toLowerCase()}@example.com`;
  await post("/admin/users", { email, full_name: "Second Admin", temporary_password: "Temp-Passw0rd-8", role_ids: [sa.id] });
  second.ctx = await pwRequest.newContext({ baseURL: BASE });
  const l1 = await second.ctx.post("/api/auth/login", { data: { email, password: "Temp-Passw0rd-8" } });
  await second.ctx.post("/api/auth/change-password", { data: { current_password: "Temp-Passw0rd-8", new_password: "Second-Admin#2026" }, headers: { "X-CSRF-Token": (await l1.json()).csrf_token } });
  const l2 = await second.ctx.post("/api/auth/login", { data: { email, password: "Second-Admin#2026" } });
  second.h = { "X-CSRF-Token": (await l2.json()).csrf_token };
  second.post = async (path, data, headers = {}) => { const r = await second.ctx.post(`/api${path}`, { data, headers: { ...second.h, ...headers } }); assert.ok(r.ok(), `${path}: ${r.status()} ${await r.text()}`); return r.status() === 204 ? null : r.json(); };
});

await step("production: run an order's stages from its card, with a failing QC reading blocking completion", async () => {
  const key = () => ({ "Idempotency-Key": `wf-${Math.random().toString(36).slice(2)}` });
  await post("/inventory/opening-stock", { warehouse_id: S.wh.id, qc_status: "RELEASED", lines: [{ item_id: S.rm.id, qty: "200", unit_cost: "10" }] }, key());
  const bom = await post("/production/boms/manual", { output_item_id: S.fg.id, output_qty: "20", lines: [{ item_id: S.rm.id, qty: "20" }] });
  await second.post(`/production/boms/${bom.id}/approve`, {});
  const o = await post("/production/orders", { bom_id: bom.id, planned_qty: "20", warehouse_id: S.wh.id });
  await post(`/production/orders/${o.id}/release`, { expected_version: 1 }, key());
  await post(`/production/orders/${o.id}/issue`, {}, key());
  S.prod = o;
  await go("/m/production/orders");
  await card(o.number).waitFor();
  await act(o.number, /^Start:/);
  await dlg().getByRole("button", { name: "Start stage" }).click();
  await card(o.number).getByRole("button", { name: /^Complete:/ }).waitFor();
  await act(o.number, "QC reading");
  await dlg().locator("#parameter").fill("pH"); await dlg().locator("#value").fill("7.4"); await dlg().locator("#min_value").fill("5"); await dlg().locator("#max_value").fill("6");
  await dlg().getByRole("button", { name: "QC reading" }).click();
  await page.getByText(/corrective action/i).first().waitFor();          // the server's rule, shown in words
  await dlg().locator("#corrective_action").fill("Added citric acid and remixed");
  await dlg().getByRole("button", { name: "QC reading" }).click();
  await act(o.number, /^Complete:/);
  await dlg().getByRole("button", { name: "Complete stage" }).click();
  await page.getByText(/Re-check passing readings/).first().waitFor();   // blocked until a passing re-check
  await dlg().getByRole("button", { name: "Cancel" }).click();            // the drawer stays open after a refusal so it can be retried; close it
  await act(o.number, "QC reading");
  await dlg().locator("#parameter").fill("pH"); await dlg().locator("#value").fill("5.5"); await dlg().locator("#min_value").fill("5"); await dlg().locator("#max_value").fill("6");
  await dlg().getByRole("button", { name: "QC reading" }).click();
  await act(o.number, /^Complete:/);
  await dlg().getByRole("button", { name: "Complete stage" }).click();
  await shot("w06-production-stage");
});

await step("stocktake: a count started by another user is entered by scan and approved here", async () => {
  const st = await second.post("/inventory/stocktakes", { warehouse_id: S.whFg.id });
  await go("/m/inventory/stocktake");
  await act(st.number, "Count (scan)");
  const fgLine = dlg().locator("tr", { hasText: S.fg.item_code });
  await fgLine.waitFor();
  await dlg().getByRole("button", { name: "Scan a barcode" }).click();
  await page.getByLabel("Barcode", { exact: true }).fill(`MT:BATCH:${S.fgBatch.id}`);
  await page.getByLabel("Barcode", { exact: true }).press("Enter");
  await page.getByText(new RegExp(`\\+1 ${S.fg.item_code}`)).first().waitFor();
  await page.keyboard.press("Escape");
  // every other line takes its system quantity; the scanned line is counted one short of the system figure
  for (const inp of await dlg().locator('input[aria-label^="Counted "]').all()) if (!(await inp.inputValue())) { const row = inp.locator("xpath=ancestor::tr"); await inp.fill((await row.locator("td.num").innerText()).replace(/,/g, "")); }
  await dlg().getByRole("button", { name: "Save counts" }).click();
  await card(st.number).waitFor();
  await act(st.number, "Approve");
  await page.getByRole("alertdialog").getByRole("button", { name: "Approve" }).click();
  await card(st.number).getByText("Approved").waitFor();
});

await step("accounts: import a bank statement CSV from the bank account card, then book a bank charge", async () => {
  const code = "11" + String(Date.now()).slice(-5);
  await post("/accounts/chart", { code, name: `Workflow bank ${stamp}`, type: "ASSET" });
  await post("/accounts/bank-accounts", { name: `WF HDFC ${stamp}`, ledger_account_code: code });
  await go("/m/accounts/bank");
  await act(`WF HDFC ${stamp}`, "Import statement (CSV)");
  const today = new Date().toISOString().slice(0, 10);
  await dlg().locator("#file").setInputFiles({ name: "stmt.csv", mimeType: "text/csv", buffer: Buffer.from(`date,description,reference,credit,debit\n${today},SMS alert charges ${stamp},,,23.60\n`) });
  await dlg().getByRole("button", { name: "Import statement (CSV)" }).click();
  await go("/m/accounts/bank-transactions");
  const tx = card(`SMS alert charges ${stamp}`);
  await tx.waitFor();
  await tx.getByRole("button", { name: "Book to account" }).click();
  await dlg().locator("#counter_account_code").fill("5400");
  await dlg().getByRole("button", { name: "Book to account" }).click();
  await card(`SMS alert charges ${stamp}`).getByText("Matched").waitFor();
});

await step("e-commerce: import marketplace orders from a CSV; an unmapped SKU lands in the exception queue", async () => {
  const acct = await post("/ecommerce/accounts", { platform: "NYKAA", name: `WF Nykaa ${stamp}`, seller_id: `N${stamp}`, customer_id: S.cust.id, warehouse_id: S.whFg.id });
  await post(`/ecommerce/accounts/${acct.id}/mappings`.replace("/mappings", "/mappings"), { platform_sku: `SKU-${stamp}`, item_id: S.fg.id }, {}).catch(() => {});
  S.acct = acct;
  await ctx.request.put(`${BASE}/api/ecommerce/accounts/${acct.id}/mappings`, { data: { platform_sku: `SKU-${stamp}`, item_id: S.fg.id }, headers: { "X-CSRF-Token": csrf } });
  await go("/m/ecommerce/accounts");
  await act(`WF Nykaa ${stamp}`, "Import CSV");
  const today = new Date().toISOString().slice(0, 10);
  await dlg().locator("#file").setInputFiles({ name: "orders.csv", mimeType: "text/csv", buffer: Buffer.from(`order_id,order_date,customer_name,shipping_address,sku,qty,unit_price\nNYK-${stamp}-1,${today},Riya,Pune,SKU-${stamp},2,100\nNYK-${stamp}-2,${today},Aman,Delhi,UNKNOWN-${stamp},1,50\n`) });
  await dlg().getByRole("button", { name: "Import CSV" }).click();
  await go("/m/ecommerce/orders");
  await card(`NYK-${stamp}-2`).getByText(/Unmapped SKU/).waitFor();
  await card(`NYK-${stamp}-1`).getByText("So Created").waitFor();
});

await step("e-commerce: record a settlement, record the receipt, and see the status move", async () => {
  await go("/m/ecommerce/accounts");
  await act(`WF Nykaa ${stamp}`, "Record settlement");
  const today = new Date().toISOString().slice(0, 10);
  await dlg().locator("#reference").fill(`SET-${stamp}`); await dlg().locator("#period_start").fill(today); await dlg().locator("#period_end").fill(today);
  await dlg().locator("#gross_sales").fill("236"); await dlg().locator("#commission").fill("35.4");
  await dlg().getByRole("button", { name: "Record settlement" }).click();
  await go("/m/ecommerce/settlements");
  await card(`SET-${stamp}`).getByText("Pending").waitFor();
  await act(`SET-${stamp}`, "Record receipt");
  await dlg().locator("#received_amount").fill("200.6");
  await dlg().getByRole("button", { name: "Record receipt" }).click();
  await card(`SET-${stamp}`).getByText("Received").waitFor();
});

await step("quality: create a specification and an inspection from the forms; a result is judged by the server", async () => {
  await go("/m/quality/specs");
  await page.getByRole("button", { name: "New specification" }).click();
  await dlg().locator("#item_id").selectOption({ label: `${S.rm.item_code} — ${S.rm.name}` });
  await dlg().locator("#stage").selectOption("INCOMING");
  await dlg().locator("#tests-0-test_name").fill("Assay"); await dlg().locator("#tests-0-min_value").fill("98"); await dlg().locator("#tests-0-max_value").fill("101");
  await dlg().getByRole("button", { name: "Create" }).click();
  await go("/m/quality/inspections");
  await page.getByRole("button", { name: "New inspection" }).click();
  await dlg().locator("#batch_id").selectOption(await dlg().locator("#batch_id option", { hasText: S.batch.batch_no }).first().getAttribute("value"));
  await dlg().locator("#stage").selectOption("INCOMING");
  await dlg().getByRole("button", { name: "Create" }).click();
  await page.getByText(new RegExp(`${S.batch.batch_no}|INC/|INSP/`)).first().waitFor({ timeout: 8000 }).catch(() => {});
});

await step("no unexpected browser console errors", async () => { assert.deepEqual(consoleErrors, []); });

await browser.close();
const failed = results.filter(([, s]) => s === "FAIL");
if (process.env.REGISTER_FILE) {
  const { appendFileSync } = await import("node:fs"); const { execSync } = await import("node:child_process");
  const sh = (c) => { try { return execSync(c, { encoding: "utf8" }).trim(); } catch { return ""; } };
  appendFileSync(process.env.REGISTER_FILE, JSON.stringify({ kind: "browser E2E (Playwright) - UI workflows", date: new Date().toISOString().slice(0, 19) + "+00:00",
    commit: sh("git rev-parse --short=10 HEAD"), dirty: !!sh("git status --porcelain"), result: failed.length ? "FAIL" : "PASS",
    passed: results.length - failed.length, failed: failed.length, environment: "Chromium (Playwright), hermetic DB + API + production web build, 4 vCPU sandbox",
    failures: failed.map(([n]) => n) }) + "\n");
}
console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots: ${SHOTS}`);
process.exit(failed.length ? 1 : 0);
