// Run against a disposable app database on the isolated development server.
import assert from "node:assert/strict";
import { chromium, expect } from "@playwright/test";

const base = process.env.AUDIT_STRUCTURE_TEST_URL;
if (!base || new URL(base).origin !== "http://127.0.0.1:8877")
  throw new Error("Use the isolated disposable-data server at 127.0.0.1:8877.");

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  await page.goto(base);
  const toolbar = page.locator(".viewer-floating-toolbar");
  const mode = toolbar.getByRole("button", { name: "Иерархия полей" });
  const canvas = page.locator(".file-page-canvas");
  const tree = page.getByRole("complementary", { name: "Структура документа" });
  await expect(mode).toBeVisible();
  await expect(mode).toHaveAttribute("aria-pressed", "false");
  assert.equal(await toolbar.locator(":scope > :last-child button").innerText(), "Иерархия полей");
  await expect(page.locator(".structure-regions")).toHaveCount(0);

  await mode.click();
  await expect(mode).toHaveAttribute("aria-pressed", "true");
  await expect(tree.getByText(/Таблица · 17 граф/)).toBeVisible({ timeout: 60_000 });
  await expect(tree.getByText(/Исполнитель\s*:/)).toBeVisible();
  assert.ok(await canvas.locator(".structure-branch").count() > 2);

  await tree.locator(".structure-node").filter({ hasText: /Затраты на оплату труда.*Стать/ }).first().click();
  await expect(tree.getByRole("button", { name: /^в том числе:/i }).first()).toBeVisible();
  assert.ok(await canvas.locator(".structure-field.related").count() > 2);

  const pair = tree.getByRole("button", { name: /Исполнитель\s*:/ }).first();
  const pairId = await pair.getAttribute("data-structure-select");
  await pair.click();
  const editor = page.getByRole("form", { name: "Текст поля" });
  const value = editor.getByRole("textbox", { name: "Значение поля" });
  await expect(value).toHaveAttribute("readonly", "");
  await expect(canvas.locator(".structure-key.chosen")).toHaveCount(1);
  const original = await value.inputValue();
  const corrected = original + " (сверено)";
  await toolbar.getByRole("button", { name: "Разрешить редактирование документа" }).click();
  await value.fill(corrected);
  await editor.getByRole("button", { name: "Сохранить" }).click();
  await expect(editor.getByText("Правка сохранена")).toBeVisible();
  assert.ok((await pair.innerText()).includes(corrected));

  // Selecting directly on the existing document image opens the same editable field.
  const region = canvas.locator(".structure-field.chosen");
  const hit = await region.boundingBox();
  assert.ok(hit);
  await editor.getByRole("button", { name: "Снять выбор поля" }).click();
  await page.mouse.click(hit.x + hit.width / 2, hit.y + hit.height / 2);
  await expect(tree.locator(`[data-structure-select="${pairId}"]`)).toHaveAttribute("aria-pressed", "true");
  await expect(value).toHaveValue(corrected);
  await mode.click();
  await expect(tree).toHaveCount(0);
  await expect(page.locator(".structure-regions")).toHaveCount(0);

  await page.reload();
  await mode.click();
  await tree.getByRole("button", { name: /Исполнитель\s*:/ }).first().click();
  await expect(value).toHaveValue(corrected);
  await toolbar.getByRole("button", { name: "Разрешить редактирование документа" }).click();
  await value.fill(original);
  await editor.getByRole("button", { name: "Сохранить" }).click();
  await expect(editor.getByText("Правка сохранена")).toBeVisible();

  if (process.env.AUDIT_STRUCTURE_SCREENSHOT)
    await page.screenshot({ path: process.env.AUDIT_STRUCTURE_SCREENSHOT });

  const photo = page.locator('.project-history [title$=".png"]').first();
  if (!await photo.count())
    await page.getByRole("button", { name: "Раскрыть первичные файлы" }).first().click();
  await photo.click();
  await expect(mode).toHaveAttribute("aria-pressed", "false");
  await mode.click();
  await expect(tree.getByText(/Реквизиты · ключ: значение/)).toBeVisible({ timeout: 60_000 });
  assert.ok(await canvas.locator(".structure-key").count() > 0);
  const size = await canvas.locator(".structure-regions").boundingBox();
  const image = await canvas.locator("img").boundingBox();
  assert.ok(size && image && Math.abs(size.height - image.height) < 1);
  assert.deepEqual(errors, []);
  console.log("PDF hierarchy, paired regions, picture OCR, save/reload and toolbar mode verified");
} finally {
  await browser.close();
}
