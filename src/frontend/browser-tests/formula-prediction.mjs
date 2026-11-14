import assert from "node:assert/strict";
import { chromium } from "@playwright/test";

const base = process.env.AUDIT_BROWSER_TEST_URL;
if (base !== "http://127.0.0.1:8766") throw new Error("Use a disposable-data server on port 8766.");
const cases = await (await fetch(`${base}/api/cases`)).json();
const project = cases.find(project => project.sources.length >= 19);
assert.ok(project);
const read = async () => (await fetch(`${base}/api/cases/${project.id}`)).json();
const before = await read();
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1720, height: 1100 } });
  await page.goto(`${base}/?case=${project.id}`);
  await page.getByRole("button", { name: "Редактировать формулу", exact: true }).click();
  const expression = page.getByRole("textbox", { name: "Математическая формула" });
  const originalExpression = await expression.inputValue();
  await page.getByRole("button", { name: "Предсказать ИИ", exact: true }).click();
  await page.getByRole("button", { name: "Сохранить предложение" }).waitFor({ timeout: 120000 });
  assert.equal(await expression.inputValue(), originalExpression);
  assert.equal((await read()).revision, before.revision);
  assert.equal(await page.locator(".prediction-sources button").count(), 4);
  assert.ok((await page.locator(".formula-prediction").innerText()).includes("1 133 298,1"));
  await page.locator(".prediction-sources button").first().click();
  await page.getByRole("button", { name: "Сохранить предложение" }).click();
  await page.getByRole("button", { name: "Редактировать формулу", exact: true }).waitFor();
  const after = await read();
  assert.equal(after.revision, before.revision + 1);
  const target = after.checks.find(check => check.code === "0800" && check.row === "2026");
  assert.equal(target.expected, "1133298.10");
  assert.equal(target.inputs.length, 4);
  assert.equal(target.delta, "12500.00");
  await page.getByLabel("Действия с проектом").click();
  await page.getByRole("button", { name: "Заключение и экспорт" }).click();
  await page.getByRole("heading", { name: "Заключение о проверке" }).waitFor();
  assert.equal(await page.getByRole("link", { name: "JSON с источниками и историей" }).count(), 1);
  console.log("PASS local prediction, four sources, source navigation, explicit save and conclusion");
} finally {
  await browser.close();
}
