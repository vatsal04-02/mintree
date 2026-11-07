// Browser E2E for the AI layer (see e2e/run-ai.sh): assistant panel, document reader, suggestions, with the scripted provider.
//   BASE_URL=http://localhost:3102 DEMO_PASSWORD='Mintree-Demo#2026' node e2e/ai.mjs
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE_URL ?? "http://localhost:3102";
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


const json = async (path) => (await page.context().request.get(`${BASE}/api${path}`)).json();
let docText = "", poNumber = "";
async function buildDocument() {               // a text invoice for a demo PO that has received-but-uninvoiced goods, built from the live master data
  const pos = (await json("/procurement/purchase-orders?page_size=100")).items;
  for (const po of pos) {
    const d = await json(`/procurement/purchase-orders/${po.id}`);
    const line = (d.items ?? []).find((l) => Number(l.qty_received) > Number(l.qty_invoiced ?? 0));
    if (!line) continue;
    const item = await json(`/masters/items/${line.item_id}`);
    const sup = await json(`/masters/suppliers/${d.supplier_id}`);
    const qty = Number(line.qty_received) - Number(line.qty_invoiced ?? 0);
    const sub = qty * Number(line.unit_price), tax = Math.round(sub * Number(line.tax_rate)) / 100;
    poNumber = d.number;
    docText = `TYPE: INVOICE\nSUPPLIER: ${sup.name}\nINVOICE NO: AI-E2E-${Date.now() % 100000}\nDATE: ${new Date().toISOString().slice(0, 10)}\nPO: ${d.number}\nLINE: ${item.item_code} | ${qty} | KG | ${line.unit_price}\nGST: ${line.tax_rate}\nSUBTOTAL: ${sub.toFixed(2)}\nTAX: ${tax.toFixed(2)}\nTOTAL: ${(sub + tax).toFixed(2)}\n`;
    return;
  }
  throw new Error("no demo PO with uninvoiced receipts");
}
const upload = async (kindLabel = "Supplier invoice") => {
  await open("/ai/extract");
  await page.getByLabel("Document type").selectOption({ label: kindLabel });
  await page.getByTestId("ai-file").setInputFiles({ name: "invoice.txt", mimeType: "text/plain", buffer: Buffer.from(docText) });
  await page.getByTestId("ai-read").click();
  await page.getByTestId("ai-result").waitFor();
};

await step("management has the assistant but not the document reader; a question is answered from records with sources", async () => {
  await loginAs("mgmt");
  await page.getByTestId("ai-open").waitFor();
  assert.equal(await nav().getByRole("link", { name: "Document reader" }).count(), 0);
  await page.keyboard.press("Control+j");
  const panel = page.getByRole("dialog", { name: "Assistant" });
  await panel.waitFor();
  await panel.getByText("Demo provider").waitFor();
  await panel.getByText("Today", { exact: true }).waitFor();
  await panel.getByRole("button", { name: "What is overdue?" }).click();
  await panel.locator("[data-role=assistant]").first().waitFor();
  const answer = await panel.locator("[data-role=assistant]").first().innerText();
  assert.match(answer, /records show/i);
  assert.equal(await panel.getByTestId("ai-unverified").count(), 0, "figures echoed from tools are verified");
  await shot("a01-assistant-panel");
  await page.keyboard.press("Escape");
});

await step("the assistant's dark theme renders without clipped content", async () => {
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
  await page.getByTestId("ai-open").click();
  const panel = page.getByRole("dialog", { name: "Assistant" });
  await panel.waitFor();
  await shot("a02-assistant-dark");
  await page.waitForTimeout(400);
  const box = await panel.boundingBox();
  assert.ok(box && box.x + box.width <= 1440 + 1);
  await page.keyboard.press("Escape");
  await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
});

await step("a purchase manager reads an invoice, sees the checks, and cannot post it without the accounts permission", async () => {
  await loginAs("buyer");
  assert.ok(await nav().getByRole("link", { name: "Document reader" }).count() >= 1);
  await buildDocument();
  await upload();
  const flags = await page.getByTestId("ai-flags").count();
  assert.equal(flags, 0, "a clean invoice has no flags");
  await page.getByText("Draft to post").waitFor();
  assert.ok(await page.getByTestId("ai-post").isDisabled(), "buyers cannot post supplier invoices");
  await shot("a03-extraction-buyer");
});

await step("an accounts manager reviews and posts the same draft through the normal endpoint, and a repeat is blocked as a duplicate", async () => {
  await loginAs("acct");
  await upload();
  assert.ok(await page.getByTestId("ai-post").isEnabled());
  await page.getByTestId("ai-post").click();
  await page.getByTestId("ai-done").waitFor();
  const invNo = docText.match(/INVOICE NO: (\S+)/)[1];
  const list = await json("/accounts/supplier-invoices?page_size=100");
  assert.ok(list.items.some((i) => i.supplier_invoice_no === invNo), "the invoice exists in the books");
  await shot("a04-extraction-posted");
  await upload();
  await page.getByTestId("ai-flags").waitFor();
  assert.match(await page.getByTestId("ai-flags").innerText(), /already recorded|exceeds|received/i);
  assert.ok(await page.getByTestId("ai-post").isDisabled(), "blocking checks disable posting");
});

await step("tampered numbers are caught by the checks, not trusted", async () => {
  docText = docText.replace(/TOTAL: .*/, "TOTAL: 1.00").replace(/INVOICE NO: \S+/, "INVOICE NO: AI-E2E-TAMPER");
  await upload();
  assert.match(await page.getByTestId("ai-flags").innerText(), /not the printed total/i);
  assert.ok(await page.getByTestId("ai-post").isDisabled());
});

await step("suggestions: generate, accept one in the user's name, and see the result in the normal screen", async () => {
  await loginAs("buyer");
  await open("/ai/proposals");
  await page.getByTestId("ai-generate").click();
  await page.waitForLoadState("networkidle");
  const api1 = (await json("/ai/proposals")).items;
  const rows = page.getByTestId("ai-proposals").locator("li");
  if (api1.length === 0) { await page.getByText("No open suggestions").waitFor(); return; }
  await rows.first().waitFor();
  assert.equal(await rows.count(), api1.length);
  const first = api1[0];
  await shot("a05-suggestions");
  await page.getByTestId(`ai-accept-${first.id}`).click();
  await page.waitForLoadState("networkidle");
  const after = (await json("/ai/proposals?status=ACCEPTED")).items.find((p) => p.id === first.id);
  assert.ok(after && after.result?.id, "accepted proposal records what it created");
});

await step("the plain-language kill switch state and permissions: a role without AI access sees no assistant", async () => {
  await loginAs("op");
  await page.waitForLoadState("networkidle");
  assert.equal(await page.getByTestId("ai-open").count(), 0);
  const r = await page.context().request.get(`${BASE}/api/ai/briefing`);
  assert.equal(r.status(), 403);
});

await step("no unexpected browser console errors", async () => assert.deepEqual(consoleErrors, []));

await browser.close();
const failed = results.filter(([, s]) => s === "FAIL");
if (process.env.REGISTER_FILE) {
  const { appendFileSync } = await import("node:fs"); const { execSync } = await import("node:child_process");
  const sh = (c) => { try { return execSync(c, { encoding: "utf8" }).trim(); } catch { return ""; } };
  appendFileSync(process.env.REGISTER_FILE, JSON.stringify({ kind: "browser E2E (Playwright) - AI layer journeys", date: new Date().toISOString().slice(0, 19) + "+00:00",
    commit: sh("git rev-parse --short=10 HEAD"), dirty: !!sh("git status --porcelain"), result: failed.length ? "FAIL" : "PASS",
    passed: results.length - failed.length, failed: failed.length, environment: "Chromium (Playwright), demo-seeded DB + API (scripted provider) + production web build, 4 vCPU sandbox",
    failures: failed.map(([n]) => n) }) + "\n");
}
console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots: ${SHOTS}`);
process.exit(failed.length ? 1 : 0);
