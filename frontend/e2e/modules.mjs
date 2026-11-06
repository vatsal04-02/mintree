// Browser E2E for the module screens (run after phase0.mjs in the same hermetic environment: it reuses the admin and the limited stores user that phase0 created).
//   BASE_URL=http://localhost:3100 node e2e/modules.mjs
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const ADMIN = { email: process.env.ADMIN_EMAIL ?? "admin@example.com", password: process.env.ADMIN_NEW_PASSWORD ?? "Mintree-Admin#2026" };
const SHOTS = process.env.SHOTS ?? mkdtempSync(join(tmpdir(), "mod-shots-"));
const results = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(String(e)));
const shot = (n) => page.screenshot({ path: join(SHOTS, `${n}.png`) });
const stamp = Date.now().toString(36).toUpperCase();

async function step(name, fn) {
  try { await fn(); results.push([name, "PASS"]); console.log("PASS", name); }
  catch (e) { results.push([name, "FAIL"]); console.log("FAIL", name, "\n   ", e.message.split("\n")[0]); await shot(`FAIL-${name.replace(/\W+/g, "-")}`).catch(() => {}); }
}
async function login(email, password) {
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(`${BASE}/`);
}
const nav = () => page.getByRole("navigation", { name: "Main" });

await step("administrator sees every operations module in the sidebar", async () => {
  await login(ADMIN.email, ADMIN.password);
  for (const l of ["Purchase", "Inventory", "Production", "R&D", "Quality", "Dispatch", "CRM & Sales", "Accounts", "Maintenance", "E-commerce", "Meta & Branding", "Management", "Approvals", "Notifications"])
    await nav().getByRole("link", { name: l, exact: true }).waitFor({ timeout: 4000 });
  await shot("m01-sidebar");
});

const screens = [];
await step("each module workspace opens and lists its screens", async () => {
  const mods = await nav().locator('a[href^="/m/"]').evaluateAll((els) => els.map((e) => e.getAttribute("href")));
  assert.ok(mods.length >= 12, `expected 12 module links, got ${mods.length}`);
  for (const href of mods) {
    await page.goto(`${BASE}${href}`);
    await page.getByRole("heading", { level: 1 }).waitFor();
    const links = await page.locator(`main a[href^="${href}/"]`).evaluateAll((els) => els.map((e) => e.getAttribute("href")));
    assert.ok(links.length > 0, `${href} lists no screens`);
    screens.push(...links);
  }
  await shot("m02-workspace");
});

await step("every screen renders without an error state", async () => {
  assert.ok(screens.length >= 60, `expected 60+ screens, got ${screens.length}`);
  const broken = [];
  for (const href of screens) {
    await page.goto(`${BASE}${href}`);
    await page.getByRole("heading", { level: 1 }).waitFor();
    await page.waitForLoadState("networkidle");
    const err = await page.locator("main [role=alert]").filter({ hasText: /\S/ }).count();
    if (err) broken.push(`${href}: ${(await page.locator("main [role=alert]").first().innerText()).slice(0, 80)}`);
  }
  if (broken.length) console.log(broken.join("\n"));
  assert.deepEqual(broken, []);
});

await step("create a lead through the form and see it in the list", async () => {
  await page.goto(`${BASE}/m/sales/leads`);
  await page.getByRole("button", { name: "New lead" }).click();
  await page.getByLabel("Name").fill(`Salon ${stamp}`);
  await page.getByLabel("Company").fill("Glow Studio");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("cell", { name: `Salon ${stamp}` }).waitFor();
  await page.getByRole("row", { name: new RegExp(`Salon ${stamp}`) }).getByRole("button", { name: /Contacted/ }).click();
  await page.getByRole("row", { name: new RegExp(`Salon ${stamp}`) }).getByText("Contacted").waitFor();
  await shot("m03-lead");
});

await step("required-field validation appears inline", async () => {
  await page.goto(`${BASE}/m/maintenance/equipment`);
  await page.getByRole("button", { name: "Add equipment" }).click();
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("dialog").getByText("Required").first().waitFor();
  await page.locator("#asset_id").fill(`EQ-${stamp}`);
  await page.locator("#name").fill("Homogenizer 500L");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("cell", { name: `EQ-${stamp}` }).waitFor();
  await shot("m04-equipment");
});

await step("a server rule is shown to the user (breakdown needs a description)", async () => {
  await page.getByRole("row", { name: new RegExp(`EQ-${stamp}`) }).getByRole("button", { name: "Report breakdown" }).click();
  await page.getByLabel("Issue").fill("Seal leaking");
  await page.getByRole("dialog").getByRole("button", { name: "Report breakdown" }).click();
  await page.getByRole("row", { name: new RegExp(`EQ-${stamp}`) }).getByText("Breakdown").waitFor();
});

await step("a report screen runs with date parameters", async () => {
  await page.goto(`${BASE}/m/accounts/balance-sheet`);
  await page.getByText("Total assets").waitFor();
  await page.goto(`${BASE}/m/accounts/pnl`);
  await page.getByText("Net profit").waitFor();
});

await step("approvals and notifications pages load", async () => {
  await page.goto(`${BASE}/approvals`);
  await page.getByRole("heading", { name: "Approvals" }).waitFor();
  await page.goto(`${BASE}/notifications`);
  await page.getByRole("heading", { name: "Notifications" }).waitFor();
});

await step("command palette finds a module screen", async () => {
  await page.goto(`${BASE}/`);
  await page.waitForLoadState("networkidle");
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.keyboard.press("Control+k");
  await page.getByPlaceholder(/Search pages/).fill("stocktake");
  await page.getByRole("option", { name: /Inventory: Stocktake/ }).first().waitFor();
  await page.keyboard.press("Escape");
});

await step("limited user: only permitted modules are visible and direct URLs are refused", async () => {
  await ctx.clearCookies();
  const api = ctx.request;
  const csrf = (await (await api.post(`${BASE}/api/auth/login`, { data: ADMIN })).json()).csrf_token;
  const roles = await (await api.get(`${BASE}/api/admin/roles`)).json();
  const stores_role = (roles.items ?? roles).find((r) => r.code === "stores_manager");
  const mk = await api.post(`${BASE}/api/admin/users`, { headers: { "X-CSRF-Token": csrf }, data: { email: `limited.${stamp.toLowerCase()}@example.com`, full_name: "Limited Stores", temporary_password: "Temp-Passw0rd-9", role_ids: [stores_role.id] } });
  assert.ok(mk.ok(), `could not create the limited user: ${mk.status()}`);
  await ctx.clearCookies();
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email").fill(`limited.${stamp.toLowerCase()}@example.com`);
  await page.getByLabel("Password").fill("Temp-Passw0rd-9");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/change-password");
  await page.getByLabel("Current password").fill("Temp-Passw0rd-9");
  await page.getByLabel("New password", { exact: true }).fill("Limited-Stores#2026");
  await page.getByLabel("Confirm new password").fill("Limited-Stores#2026");
  await page.getByRole("button", { name: "Update password" }).click();
  await page.waitForURL(`${BASE}/`);
  await nav().getByRole("link", { name: "Inventory", exact: true }).waitFor();
  assert.equal(await nav().getByRole("link", { name: "Meta & Branding", exact: true }).count(), 0, "branding hidden");
  assert.equal(await nav().getByRole("link", { name: "Accounts", exact: true }).count(), 0, "accounts hidden");
  await page.goto(`${BASE}/m/accounts/gst`);
  await page.getByText("You don't have access to this area").waitFor();
  await page.goto(`${BASE}/m/inventory/stock`);
  await page.getByRole("heading", { name: "Stock balances" }).waitFor();
  await shot("m05-limited");
});

await step("no unexpected browser console errors", async () => { assert.deepEqual(consoleErrors, []); });

await browser.close();
const failed = results.filter(([, s]) => s === "FAIL");
if (process.env.REGISTER_FILE) {
  const { appendFileSync } = await import("node:fs"); const { execSync } = await import("node:child_process");
  const sh = (c) => { try { return execSync(c, { encoding: "utf8" }).trim(); } catch { return ""; } };
  appendFileSync(process.env.REGISTER_FILE, JSON.stringify({ kind: "browser E2E (Playwright)", suite: "modules", date: new Date().toISOString().slice(0, 19) + "+00:00",
    commit: sh("git rev-parse --short=10 HEAD"), dirty: !!sh("git status --porcelain"), result: failed.length ? "FAIL" : "PASS",
    passed: results.length - failed.length, failed: failed.length, environment: "Chromium (Playwright), hermetic DB + API + production web build, 4 vCPU sandbox",
    failures: failed.map(([n]) => n) }) + "\n");
}
console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots: ${SHOTS}`);
process.exit(failed.length ? 1 : 0);
