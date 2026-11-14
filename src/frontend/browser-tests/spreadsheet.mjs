// Run only against an isolated copy: AUDIT_BROWSER_TEST_URL=http://127.0.0.1:8766 node browser-tests/spreadsheet.mjs
import assert from "node:assert/strict";
import { chromium } from "@playwright/test";
const base = process.env.AUDIT_BROWSER_TEST_URL;
if (!base || new URL(base).port === "8765")
  throw new Error(
    "Set AUDIT_BROWSER_TEST_URL to a separate server with disposable test data. The working server is refused.",
  );
const browser = await chromium.launch({
  executablePath:
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const page = await browser.newPage({
  viewport: { width: 1600, height: 1000 },
  acceptDownloads: true,
});
const errors = [],
  responses = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("response", async (response) => {
  if (response.url().endsWith("/changes"))
    responses.push({
      status: response.status(),
      detail: response.status() >= 400 ? await response.text() : "",
    });
});
try {
  await page.goto(base);
  await page.getByLabel("Раскрыть первичные файлы").first().click();
  await page
    .locator(".unified-source-row")
    .filter({ hasText: "02_01" })
    .first()
    .click();
  await page.locator(".sheet-engine canvas").first().waitFor();
  await page.getByLabel("Разрешить редактирование документа").click();
  const address = page.locator(".sheet-engine input:not([aria-label])");
  const select = async (range) => {
    await address.fill(range);
    await address.press("Enter");
  };
  const menu = async (name, item) => {
    await page
      .locator(".sheet-menu>button")
      .filter({ hasText: new RegExp("^" + name + "$") })
      .click();
    await page.getByRole("menuitem", { name: item, exact: true }).click();
  };
  await select("K10:L11");
  for (const action of [
    "Число · 1 234,56",
    "Денежный · ₽",
    "Процент",
    "Дата",
    "Время",
    "Полужирный · ⌘B",
    "Курсив · ⌘I",
    "Подчеркнутый · ⌘U",
    "Зачеркнутый",
    "Перенос текста",
    "Обрезать текст",
    "Выровнять слева",
    "По центру",
    "Выровнять справа",
    "Объединить ячейки",
    "Разъединить ячейки",
  ])
    await menu("Формат", action);
  await menu("Инструменты", "Автоподбор высоты строк");
  await select("K10");
  await menu("Вставка", "Строку выше");
  await menu("Данные", "Удалить выбранные строки");
  await select("K10");
  await menu("Вставка", "Столбец слева");
  await menu("Данные", "Удалить выбранные столбцы");
  await select("K10:L12");
  await menu("Данные", "Сортировать А → Я");
  await menu("Данные", "Сортировать Я → А");
  await menu("Данные", "Создать фильтр");
  await menu("Данные", "Удалить фильтр");
  await menu("Вид", "Закрепить до выделенной строки");
  await menu("Вид", "Закрепить до выделенного столбца");
  await menu("Вид", "Снять закрепление");
  await menu("Вид", "Показать / скрыть сетку");
  await select("K10");
  await menu("Правка", "Очистить значения");
  await menu("Правка", "Очистить форматирование");
  await page.getByLabel("Масштаб таблицы в процентах").fill("137.5");
  await page.getByLabel("Масштаб таблицы в процентах").press("Enter");
  assert.equal(
    await page.getByLabel("Масштаб таблицы в процентах").inputValue(),
    "137.5",
  );
  await page.mouse.move(710, 407);
  await page.mouse.wheel(12, 18);
  await page.mouse.wheel(-9, 14);
  await page.waitForTimeout(100);
  await page.mouse.click(710, 407);
  const point = await address.inputValue();
  await page.keyboard.down("Control");
  for (const delta of [-8, -11, -7]) await page.mouse.wheel(0, delta);
  await page.keyboard.up("Control");
  await page.waitForTimeout(100);
  await page.mouse.click(710, 407);
  assert.equal(
    await address.inputValue(),
    point,
    "The document point under the cursor survives smooth zoom",
  );
  const remembered = await page
    .getByLabel("Масштаб таблицы в процентах")
    .inputValue();
  await page
    .locator(".unified-source-row")
    .filter({ hasText: "02_02" })
    .first()
    .click();
  await page.locator(".sheet-engine canvas").first().waitFor();
  await page
    .locator(".unified-source-row")
    .filter({ hasText: "02_01" })
    .first()
    .click();
  await page.locator(".sheet-engine canvas").first().waitFor();
  assert.equal(
    await page.getByLabel("Масштаб таблицы в процентах").inputValue(),
    remembered,
    "The zoom survives navigation to another workbook",
  );
  await page.getByLabel("Разрешить редактирование документа").click();
  await menu("Вид", "Масштаб 100%");
  await select("A1");
  await menu("Правка", "Отменить · ⌘Z");
  await menu("Правка", "Повторить · ⌘⇧Z");
  await menu("Файл", "Сохранить");
  await page.waitForTimeout(1600);
  assert.deepEqual(
    await page.locator(".sheet-editor-error").allTextContents(),
    [],
  );
  assert.ok(responses.length > 0, "Edits must reach the server");
  assert.ok(
    responses.every((r) => r.status === 200),
    JSON.stringify(responses),
  );
  const downloadPromise = page.waitForEvent("download");
  await menu("Файл", "Скачать снимок книги JSON");
  const download = await downloadPromise;
  assert.ok(download.suggestedFilename().endsWith(".json"));
  const csvPromise = page.waitForEvent("download");
  await menu("Файл", "Скачать CSV · текущий лист");
  assert.ok((await csvPromise).suggestedFilename().endsWith(".csv"));
  const xlsxPromise = page.waitForEvent("download");
  await menu("Файл", "Скачать XLSX");
  assert.ok((await xlsxPromise).suggestedFilename().endsWith(".xlsx"));
  await page.screenshot({
    path: process.env.AUDIT_BROWSER_SCREENSHOT || "/tmp/spreadsheet-final.png",
  });
  assert.deepEqual(errors, []);
  console.log(
    "Spreadsheet UI: 40 formatting, structure, data, view, undo/redo, zoom, save and export operations passed.",
  );
} catch (error) {
  console.error("Browser errors:", errors);
  console.error("Page:", (await page.locator("body").innerText()).slice(-2200));
  await page.screenshot({ path: "/tmp/sheet-failure.png" });
  throw error;
} finally {
  await browser.close();
}
