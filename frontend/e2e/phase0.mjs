// Browser E2E for Phase 0. Requires API (:8000) and web (:3000) running against a freshly seeded DB.
//   BASE_URL=http://localhost:3000 ADMIN_EMAIL=... ADMIN_PASSWORD=... SHOTS=/dir node e2e/phase0.mjs
import assert from "node:assert/strict";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRequire } from "node:module";
const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const ADMIN = { email: process.env.ADMIN_EMAIL ?? "admin@example.com", password: process.env.ADMIN_PASSWORD ?? "Mintree#Dev2026" };
const NEW_ADMIN_PW = "Mintree-Admin#2026";
const SHOTS = process.env.SHOTS ?? mkdtempSync(join(tmpdir(), "shots-"));
const stamp = Date.now().toString(36).toUpperCase();
const results = [];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(String(e)));
// Next injects an empty role=alert route announcer; only match alerts that carry text.
const d = () => page.getByRole("dialog");
const alertBox = () => page.locator("form [role=alert]").first();
const shot = (n) => page.screenshot({ path: join(SHOTS, `${n}.png`) });

async function step(name, fn) {
  try { await fn(); results.push([name, "PASS"]); console.log("PASS", name); }
  catch (e) { results.push([name, "FAIL"]); console.log("FAIL", name, "\n   ", e.message.split("\n")[0]); await shot(`FAIL-${name.replace(/\W+/g, "-")}`).catch(() => {}); }
}
async function login(email, password) {
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

await step("unauthenticated visit redirects to login", async () => {
  await page.goto(`${BASE}/masters/items`);
  await page.waitForURL("**/login");
});

await step("wrong password shows a generic error", async () => {
  await login(ADMIN.email, "definitely-wrong");
  await alertBox().waitFor();
  assert.match(await alertBox().innerText(), /Invalid email or password/);
  await shot("01-login-error");
});

await step("first login forces password change, then lands on overview", async () => {
  await login(ADMIN.email, ADMIN.password);
  await page.waitForURL("**/change-password");
  await page.getByLabel("Current password").fill(ADMIN.password);
  await page.getByLabel("New password", { exact: true }).fill("weak");
  await page.getByLabel("Confirm new password").fill("weak");
  await page.getByRole("button", { name: "Update password" }).click();
  await alertBox().waitFor();
  assert.match(await alertBox().innerText(), /at least 10/i);
  await page.getByLabel("New password", { exact: true }).fill(NEW_ADMIN_PW);
  await page.getByLabel("Confirm new password").fill(NEW_ADMIN_PW);
  await page.getByRole("button", { name: "Update password" }).click();
  await page.waitForURL(`${BASE}/`);
  await page.getByRole("heading", { level: 1 }).waitFor();
});

await step("sidebar shows every area to a system administrator", async () => {
  for (const l of ["Items", "Suppliers", "Customers", "Document centre", "Users", "Roles & permissions", "Audit log"])
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: l }).waitFor({ timeout: 4000 });
  await shot("02-overview");
});

await step("create an item through the drawer form", async () => {
  await page.goto(`${BASE}/masters/items`);
  await page.getByRole("button", { name: /New item/ }).click();
  await d().getByLabel("Item code").fill(`rm-${stamp}`);
  await d().getByLabel("Item name").fill("Niacinamide");
  await d().getByLabel("Type").selectOption("RM");
  await page.getByRole("tab", { name: "Inventory" }).click();
  await d().getByLabel("Unit of measure").selectOption({ label: "KG — Kilogram" });
  await d().getByLabel("Reorder level").fill("150");
  await d().getByLabel("Shelf life (days)").fill("730");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("cell", { name: `RM-${stamp}` }).waitFor();
  await shot("03-items");
});

await step("required-field and server-side validation errors appear inline", async () => {
  await page.goto(`${BASE}/masters/suppliers`);
  await page.getByRole("button", { name: /New supplier/ }).click();
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByText("Required").first().waitFor();
  await d().getByLabel("Supplier code").fill(`SUP-${stamp}`);
  await d().getByLabel("Supplier name").fill("Acme Cosmetic Ingredients");
  await d().getByLabel("Supplies").selectOption("RM_PM");
  await d().getByLabel("Approval status").selectOption("APPROVED");
  await d().getByLabel("GSTIN").fill("NOT-A-GSTIN");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByText(/Invalid GSTIN/).waitFor();
  await shot("04-supplier-validation");
  await d().getByLabel("GSTIN").fill("27AAPFU0939F1ZV");
  await page.getByRole("tab", { name: "Bank details" }).click();
  await d().getByLabel("Account number").fill("123456789012");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("cell", { name: `SUP-${stamp}` }).waitFor();
});

await step("duplicate code is rejected with a clear message", async () => {
  await page.getByRole("button", { name: /New supplier/ }).click();
  await d().getByLabel("Supplier code").fill(`SUP-${stamp}`);
  await d().getByLabel("Supplier name").fill("Duplicate");
  await d().getByLabel("Supplies").selectOption("RM");
  await d().getByLabel("Approval status").selectOption("PENDING");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByText(/already exists/).waitFor();
  await page.keyboard.press("Escape");
});

await step("search, deactivate and show-inactive work", async () => {
  await page.getByPlaceholder("Search suppliers…").fill(`SUP-${stamp}`);
  const row = page.getByRole("row", { name: new RegExp(`SUP-${stamp}`) });
  await row.waitFor();
  await page.getByText(/1–1 of 1|1-1 of 1/).waitFor(); // search applied
  await row.getByRole("button", { name: "Deactivate" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Deactivate" }).click();
  await page.getByText(/No records found/).waitFor();
  await page.getByLabel("Show inactive").check();
  await page.getByRole("cell", { name: `SUP-${stamp}` }).waitFor();
  await row.getByRole("button", { name: "Reactivate" }).click();
  await page.getByLabel("Show inactive").uncheck();
  await page.getByRole("cell", { name: `SUP-${stamp}` }).waitFor();
});

await step("document upload, download link and new version", async () => {
  const f = join(mkdtempSync(join(tmpdir(), "up-")), "coa-niacinamide.pdf");
  writeFileSync(f, "%PDF-1.4 e2e certificate of analysis");
  await page.goto(`${BASE}/documents`);
  await page.getByRole("button", { name: /Upload document/ }).click();
  await d().getByLabel("File *").setInputFiles(f);
  await d().getByLabel("Type", { exact: true }).selectOption("COA");
  await d().getByLabel("Record type").selectOption("supplier");
  await d().getByLabel("Record ID").fill("1");
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("cell", { name: "coa-niacinamide" }).first().click();
  await page.getByText("SHA-256").waitFor();
  const href = await page.getByRole("link", { name: "Download" }).first().getAttribute("href");
  const r = await ctx.request.get(`${BASE}${href}`);
  assert.equal(r.status(), 200);
  assert.match(await r.text(), /certificate of analysis/);
  writeFileSync(f, "%PDF-1.4 revised");
  await d().getByLabel("New version file").setInputFiles(f);
  await page.getByRole("button", { name: "Upload version" }).click();
  await page.getByText("v2 · coa-niacinamide.pdf").waitFor();
  await shot("05-document");
});

await step("create a restricted user and see their permissions enforced in the UI", async () => {
  await page.goto(`${BASE}/admin/users`);
  await page.getByRole("button", { name: /New user/ }).click();
  await d().getByLabel(/Full name/).fill("Sunita Rao");
  await d().getByLabel(/^Email/).fill(`sunita.${stamp.toLowerCase()}@example.com`);
  await d().getByLabel(/Temporary password/).fill("Temp-Passw0rd-1");
  await d().getByLabel("Stores Manager").check();
  await shot("06-new-user");
  await page.getByRole("button", { name: "Create user" }).click();
  await page.getByRole("cell", { name: "Sunita Rao" }).waitFor();
});

await step("roles matrix shows permissions and locks system admin", async () => {
  await page.goto(`${BASE}/admin/roles`);
  await page.getByRole("button", { name: /Stores Manager/ }).click();
  await page.getByLabel("masters.item:read").waitFor();
  assert.equal(await page.getByLabel("masters.item:read").isChecked(), true);
  assert.equal(await page.getByLabel("masters.supplier:create").isChecked(), false);
  await shot("07-roles");
  await page.getByRole("button", { name: /System Administrator/ }).click();
  assert.equal(await page.getByLabel("masters.item:read").isDisabled(), true);
});

await step("audit log records the actions performed above", async () => {
  await page.goto(`${BASE}/admin/audit`);
  await page.getByRole("cell", { name: "Create" }).first().waitFor();
  await page.getByPlaceholder(/Search user/).fill(`SUP`);
  await page.getByLabel("Record type").fill("supplier");
  await page.getByRole("cell", { name: /Supplier/ }).first().click();
  await page.getByRole("dialog").getByText("Request ID").waitFor();
  assert.ok(!(await page.getByRole("dialog").innerText()).includes("123456789012"), "bank account number must not appear in audit");
  await shot("08-audit-detail");
  await page.keyboard.press("Escape");
});

await step("command palette finds records", async () => {
  await page.goto(`${BASE}/`);
  await page.waitForLoadState("networkidle");
  await page.getByText("Recent activity").waitFor();
  await page.keyboard.press("Control+k");
  await page.getByPlaceholder(/Search pages/).fill("niacin");
  await page.getByRole("option", { name: /Niacinamide/ }).waitFor();
  await page.keyboard.press("Escape");
});

await step("dark theme renders", async () => {
  await page.getByRole("button", { name: /^S?$|/ }).first(); // noop warm-up
  await page.locator("header button[aria-haspopup=menu]").last().click();
  await page.getByRole("menuitem", { name: "Dark theme" }).click();
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "dark");
  await shot("09-dark-overview");
  await page.getByRole("menuitem", { name: "Light theme" }).click().catch(async () => { await page.locator("header button[aria-haspopup=menu]").last().click(); await page.getByRole("menuitem", { name: "Light theme" }).click(); });
});

await step("limited user: forced password change, restricted navigation, no write controls", async () => {
  const signOut = page.getByRole("menuitem", { name: "Sign out" });
  if (!(await signOut.isVisible())) await page.locator("header button[aria-haspopup=menu]").last().click();
  await signOut.click();
  await page.waitForURL("**/login");
  await login(`sunita.${stamp.toLowerCase()}@example.com`, "Temp-Passw0rd-1");
  await page.waitForURL("**/change-password");
  await page.getByLabel("Current password").fill("Temp-Passw0rd-1");
  await page.getByLabel("New password", { exact: true }).fill("Sunita-Stores#2026");
  await page.getByLabel("Confirm new password").fill("Sunita-Stores#2026");
  await page.getByRole("button", { name: "Update password" }).click();
  await page.waitForURL(`${BASE}/`);
  const nav = page.getByRole("navigation", { name: "Main" });
  await nav.getByRole("link", { name: "Items" }).waitFor();
  assert.equal(await nav.getByRole("link", { name: "Users" }).count(), 0, "Users link must be hidden");
  assert.equal(await nav.getByRole("link", { name: "Audit log" }).count(), 0, "Audit link must be hidden");
  await page.goto(`${BASE}/masters/suppliers`);
  await page.getByRole("cell", { name: `SUP-${stamp}` }).waitFor();
  assert.equal(await page.getByRole("button", { name: /New supplier/ }).count(), 0, "no create button without permission");
  await page.getByRole("cell", { name: `SUP-${stamp}` }).click();
  await page.getByRole("tab", { name: "Bank details" }).waitFor({ state: "detached", timeout: 1500 }).catch(() => {});
  assert.equal(await page.getByRole("tab", { name: "Bank details" }).count(), 0, "bank tab hidden without bank permission");
  await shot("10-limited-user-supplier");
  await page.keyboard.press("Escape");
  // Typing the admin URL directly still yields no data (backend 403), not a leaked page.
  await page.goto(`${BASE}/admin/users`);
  await page.getByText("You don't have access to this area").waitFor();
});

await step("no unexpected browser console errors", async () => {
  assert.deepEqual(consoleErrors, []);
});

await browser.close();
const failed = results.filter(([, s]) => s === "FAIL");
console.log(`\n${results.length - failed.length}/${results.length} passed. Screenshots: ${SHOTS}`);
process.exit(failed.length ? 1 : 0);
