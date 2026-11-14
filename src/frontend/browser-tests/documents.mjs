// Run against disposable data with AUDIT_BROWSER_TEST_URL=http://127.0.0.1:8766.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { chromium } from "@playwright/test";

const base = process.env.AUDIT_BROWSER_TEST_URL;
if (
  !base ||
  new URL(base).hostname !== "127.0.0.1" ||
  new URL(base).port !== "8766"
) {
  throw new Error("Use the isolated disposable-data server at 127.0.0.1:8766.");
}
const browser = await chromium.launch({
  executablePath:
    process.env.CHROME_PATH ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
  acceptDownloads: true,
});
const page = await context.newPage();
const errors = [];
const saves = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("response", async (response) => {
  if (response.url().endsWith("/changes")) {
    saves.push({
      status: response.status(),
      detail: response.status() >= 400 ? await response.text() : "",
    });
  }
});
const content = page.locator(".rich-document-content.ProseMirror");
const menu = async (name, item) => {
  await page
    .locator(".rich-menu > button")
    .filter({ hasText: new RegExp("^" + name + "$") })
    .click();
  await page.getByRole("menuitem", { name: item, exact: true }).click();
};
const selectText = async (text, collapse = false) => {
  await content.evaluate(
    (element, { text: wanted, collapse }) => {
      element.focus();
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const index = node.textContent.indexOf(wanted);
        if (index < 0) continue;
        const range = document.createRange();
        range.setStart(node, index);
        range.setEnd(node, index + wanted.length);
        if (collapse) range.collapse(false);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        document.dispatchEvent(new Event("selectionchange"));
        return;
      }
      throw new Error("Text selection was not found: " + wanted);
    },
    { text, collapse },
  );
  await page.waitForTimeout(80);
};
const save = async () => {
  await menu("Файл", "Сохранить · ⌘S");
  await page.waitForFunction(() =>
    document
      .querySelector(".rich-save-state")
      ?.textContent.includes("Все изменения сохранены"),
  );
  assert.equal(await page.locator(".rich-document-error").count(), 0);
};
const camera = () =>
  page.locator(".rich-document-viewport").evaluate((element) => ({
    x: element.scrollLeft,
    y: element.scrollTop,
    zoom: document.querySelector('[aria-label="Масштаб документа в процентах"]')
      .value,
  }));
const geometry = () =>
  content.evaluate((element) => {
    const paper = element.closest(".rich-document-paper");
    const origin = paper.getBoundingClientRect();
    const scale =
      Number(
        document.querySelector('[aria-label="Масштаб документа в процентах"]')
          .value,
      ) / 100;
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const runs = [];
    let node;
    while ((node = walker.nextNode())) {
      if (!node.textContent.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      runs.push({
        text: node.textContent,
        rects: Array.from(range.getClientRects(), (rect) => ({
          x: (rect.x - origin.x) / scale,
          y: (rect.y - origin.y) / scale,
          width: rect.width / scale,
          height: rect.height / scale,
        })),
      });
    }
    return { width: origin.width / scale, runs };
  });

const runKey = Date.now().toString(36);
const headingText = "Проверка редактора Docs " + runKey;
const listText = "Элемент списка QA " + runKey;
const replacedText = "Исправленный элемент QA " + runKey;

try {
  const projects = await (await page.request.get(`${base}/api/cases`)).json();
  assert.equal(
    projects.length,
    1,
    "The browser test requires a single disposable case",
  );
  const caseId = projects[0].id;
  const caseUrl = `${base}/api/cases/${caseId}`;
  const initial = await (await page.request.get(caseUrl)).json();
  const source = initial.documents.find(
    (document) => document.name.startsWith("06_") && document.format === "docx",
  );
  const other = initial.documents.find(
    (document) => document.name.startsWith("11_") && document.format === "docx",
  );
  const workbook = initial.documents.find(
    (document) =>
      document.name.startsWith("02_01") && document.format === "xlsx",
  );
  assert.ok(
    source && other && workbook,
    "The fixture has DOCX06, DOCX11 and XLSX02_01",
  );
  const sourceUrl = `${caseUrl}/documents/${source.id}`;
  const sourceFacts = Object.values(initial.nodes).filter(
    (node) => node.kind === "fact" && node.evidence?.document_id === source.id,
  );
  const originalFact = sourceFacts.find(
    (node) => Number(node.value) >= 270000 && Number(node.value) < 280000,
  );
  assert.ok(originalFact, "Capture the source amount fact before editing");
  const oldAmount = Number(originalFact.value);
  const newAmount = oldAmount + 1;
  const money = (value) =>
    new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
      .format(value)
      .replaceAll("\u00a0", " ");
  const oldAmountText = money(oldAmount);
  const newAmountText = money(newAmount);
  const dependent = initial.checks.find((check) =>
    check.dependencies.includes(originalFact.id),
  );
  assert.ok(dependent, "The source amount has a dependent calculation");
  const open = async (document) => {
    await page
      .locator(`.unified-source-row[data-document-id="${document.id}"]`)
      .click();
    if (document.format === "docx") {
      await content.waitFor();
      await page
        .getByLabel("Разрешить редактирование документа")
        .waitFor({ state: "visible" });
      await page.waitForFunction(
        () =>
          !document.querySelector(
            '[aria-label="Разрешить редактирование документа"]',
          )?.disabled,
      );
      await page.waitForTimeout(120);
    } else {
      await page.locator(".sheet-engine canvas").first().waitFor();
    }
  };
  await page.goto(base);
  await page.getByLabel("Раскрыть первичные файлы").first().click();
  await open(source);
  assert.equal(await content.getAttribute("contenteditable"), "false");
  assert.ok(
    (await page.locator(".rich-source-number").allTextContents()).some((text) =>
      text.includes(oldAmountText),
    ),
  );
  const firstPage = await page.locator(".rich-document-paper").boundingBox();
  const firstViewport = await page
    .locator(".rich-document-viewport")
    .boundingBox();
  console.log("Initial geometry", { firstPage, firstViewport });
  if (firstPage.width < firstViewport.width - 32) {
    assert.ok(
      Math.abs(
        firstPage.x +
          firstPage.width / 2 -
          firstViewport.x -
          firstViewport.width / 2,
      ) < 3,
      "The initial paper is centered",
    );
  } else {
    assert.ok(
      Math.abs(firstPage.x - firstViewport.x - 16) < 3,
      "Wide paper begins with a small left inset",
    );
  }

  const baseline = await geometry();
  for (const zoom of [75, 100, 150, 200]) {
    await page.getByLabel("Масштаб документа в процентах").fill(String(zoom));
    await page.waitForTimeout(100);
    const current = await geometry();
    assert.ok(Math.abs(current.width - baseline.width) < 0.1);
    assert.equal(current.runs.length, baseline.runs.length);
    for (let index = 0; index < baseline.runs.length; index++) {
      const original = baseline.runs[index];
      const actual = current.runs[index];
      assert.equal(actual.text, original.text);
      assert.equal(
        actual.rects.length,
        original.rects.length,
        `No text reflow at ${zoom}% for ${original.text}`,
      );
      for (let rect = 0; rect < original.rects.length; rect++) {
        for (const key of ["x", "y", "width", "height"]) {
          assert.ok(
            Math.abs(actual.rects[rect][key] - original.rects[rect][key]) < 0.6,
            `Uniform ${key} at ${zoom}% for ${original.text}`,
          );
        }
      }
    }
  }
  await page.getByLabel("Масштаб документа в процентах").fill("123.4");
  const view = await page.locator(".rich-document-viewport").boundingBox();
  await page.mouse.move(view.x + view.width / 2, view.y + view.height / 2);
  await page.mouse.wheel(43, 57);
  await page.waitForTimeout(100);
  const remembered = await camera();
  await open(other);
  await open(source);
  await page.waitForTimeout(100);
  const restored = await camera();
  assert.equal(restored.zoom, remembered.zoom);
  assert.ok(
    Math.abs(restored.x - remembered.x) < 2 &&
      Math.abs(restored.y - remembered.y) < 2,
    "DOCX camera survives another DOCX",
  );
  await open(workbook);
  await open(source);
  await page.waitForTimeout(100);
  const afterWorkbook = await camera();
  assert.equal(afterWorkbook.zoom, remembered.zoom);
  assert.ok(
    Math.abs(afterWorkbook.x - remembered.x) < 2 &&
      Math.abs(afterWorkbook.y - remembered.y) < 2,
    "DOCX camera survives XLSX",
  );

  await page.getByLabel("Масштаб документа в процентах").fill("100");
  await page.getByLabel("Разрешить редактирование документа").click();
  assert.equal(await content.getAttribute("contenteditable"), "true");
  await selectText(oldAmountText);
  await page.keyboard.insertText(newAmountText);
  await save();
  const changed = await (await page.request.get(caseUrl)).json();
  assert.equal(
    Number(changed.nodes[originalFact.id].value),
    newAmount,
    "The same source fact updates",
  );
  assert.equal(
    Number(changed.nodes[dependent.id].expected),
    Number(dependent.expected) + 1,
    "The dependent formula recalculates",
  );

  await content.evaluate((element) => {
    element.focus();
    const range = document.createRange();
    range.selectNodeContents(element);
    range.collapse(false);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
  });
  await page.waitForTimeout(100);
  await page.keyboard.press("Enter");
  await page.keyboard.insertText(headingText);
  await selectText(headingText);
  await page.getByRole("button", { name: "Полужирный", exact: true }).click();
  await page.getByLabel("Размер шрифта документа").fill("16");
  await page.getByLabel("Размер шрифта документа").press("Enter");
  await page.getByLabel("Шрифт документа").selectOption("Georgia");
  await page.getByLabel("Стиль абзаца").selectOption("2");
  await page.waitForTimeout(100);
  assert.equal(
    await content.locator("h2").filter({ hasText: headingText }).count(),
    1,
  );
  assert.equal(
    await content.locator("h2 strong").filter({ hasText: headingText }).count(),
    1,
  );
  assert.ok(await content.locator('h2 span[style*="16pt"]').count());
  await selectText(headingText, true);
  await page.keyboard.press("Enter");
  await page.getByLabel("Стиль абзаца").selectOption("0");
  await page.waitForTimeout(100);
  await page
    .getByRole("button", { name: "Маркированный список", exact: true })
    .click();
  await page.waitForTimeout(100);
  await page.keyboard.insertText(listText);
  await content.locator("ul li").filter({ hasText: listText }).waitFor();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await menu("Вставка", "Таблица…");
  await page.getByLabel("Строки × столбцы").fill("2 × 2");
  await page.getByRole("button", { name: "Применить", exact: true }).click();
  const insertedTable = content.locator("table").last();
  assert.equal(await insertedTable.locator("tr").count(), 2);
  await insertedTable.locator("th,td").first().click();
  await page.keyboard.insertText("QA таблица");
  await menu("Таблица", "Строка ниже");
  assert.equal(await insertedTable.locator("tr").count(), 3);
  await menu("Файл", "Параметры страницы");
  await page.getByLabel("Ориентация страницы").selectOption("landscape");
  await page.getByLabel("Поле Сверху").fill("20");
  await page.getByLabel("Верхний колонтитул").fill("Колонтитул QA");
  await page.getByRole("button", { name: "Применить", exact: true }).click();
  await menu("Правка", "Найти и заменить · ⌘H");
  await page.getByLabel("Найти в документе", { exact: true }).fill(listText);
  await page.getByLabel("Заменить на", { exact: true }).fill(replacedText);
  await page.getByRole("button", { name: "Заменить все", exact: true }).click();
  assert.ok((await content.textContent()).includes(replacedText));
  await page.getByLabel("Закрыть поиск").click();
  await save();
  const snapshot = await (
    await page.request.get(`${sourceUrl}/rich-document`)
  ).json();
  assert.equal(snapshot.settings.orientation, "landscape");
  assert.equal(snapshot.settings.margins.top, 20);
  assert.equal(snapshot.settings.header, "Колонтитул QA");
  assert.ok(JSON.stringify(snapshot.content).includes(newAmountText));
  assert.ok(JSON.stringify(snapshot.content).includes(replacedText));

  const slowChanges = async (route) => {
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 650));
    await route.fulfill({ response });
  };
  await page.route("**/changes", slowChanges);
  const activeSave = page.waitForRequest((request) =>
    request.url().endsWith("/changes"),
  );
  await selectText(replacedText);
  await page.keyboard.insertText(replacedText + " экспорт A");
  await activeSave;
  await page.keyboard.insertText(" и B");
  const busyExport = page.waitForEvent("download");
  await menu("Файл", "Скачать DOCX");
  assert.ok((await busyExport).suggestedFilename().endsWith(".docx"));
  await page.unroute("**/changes", slowChanges);
  const exportedSnapshot = await (
    await page.request.get(`${sourceUrl}/rich-document`)
  ).json();
  assert.ok(
    JSON.stringify(exportedSnapshot.content).includes(
      replacedText + " экспорт A и B",
    ),
    "Export waits for an active save and persists newer edits",
  );

  for (const format of ["DOCX", "PDF", "HTML"]) {
    const promise = page.waitForEvent("download");
    await menu("Файл", `Скачать ${format}`);
    const download = await promise;
    assert.ok(
      download
        .suggestedFilename()
        .toLowerCase()
        .endsWith("." + format.toLowerCase()),
    );
    const bytes = await readFile(await download.path());
    if (format === "DOCX") assert.equal(bytes.subarray(0, 2).toString(), "PK");
    if (format === "PDF") assert.equal(bytes.subarray(0, 4).toString(), "%PDF");
    if (format === "HTML") assert.ok(bytes.toString().includes(newAmountText));
  }
  await open(other);
  await open(source);
  assert.ok((await content.textContent()).includes(newAmountText));
  assert.ok((await content.textContent()).includes(replacedText));
  assert.equal(
    await content.locator("h2 strong").filter({ hasText: headingText }).count(),
    1,
  );
  assert.ok(saves.length > 0);
  assert.ok(
    saves.every((save) => save.status === 200),
    JSON.stringify(saves),
  );
  assert.deepEqual(errors, []);
  await page.screenshot({
    path: process.env.AUDIT_BROWSER_SCREENSHOT || "/tmp/documents-final.png",
  });
  console.log(
    "DOCX UI passed readonly, number markers, invariant 75/100/150/200% zoom, DOCX/XLSX camera, source recalculation, rich formatting, list/table/page settings, search/replace, persistence and DOCX/PDF/HTML downloads.",
  );
} catch (error) {
  console.error("Browser errors", errors);
  console.error("Saves", saves);
  console.error("Content DOM", (await content.innerHTML()).slice(-4500));
  console.error((await page.locator("body").innerText()).slice(-3500));
  await page.screenshot({ path: "/tmp/documents-failure.png" });
  throw error;
} finally {
  await browser.close();
}
