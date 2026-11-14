import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';

// Read-only regression: opening menus must not cover the workbook or Explorer.
const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const writes = [], errors = [];
  page.on('request', request => { if (!['GET', 'HEAD'].includes(request.method())) writes.push(request.url()); });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(process.env.AUDIT_BROWSER_TEST_URL || 'http://127.0.0.1:8765');
  await page.getByLabel('Раскрыть первичные файлы').first().click();
  await page.locator('.unified-source-row').filter({ hasText: '02_04' }).first().click();
  await page.locator('.sheet-engine canvas').first().waitFor();
  await page.mouse.move(1400, 750);
  const clips = [{ x: 30, y: 160, width: 140, height: 350 }, { x: 1250, y: 400, width: 300, height: 350 }];
  const before = await Promise.all(clips.map(clip => page.screenshot({ clip })));
  for (const name of ['Файл', 'Правка', 'Вид', 'Вставка', 'Формат', 'Данные', 'Инструменты']) {
    await page.locator('.sheet-menu > button').filter({ hasText: new RegExp('^' + name + '$') }).click();
    await page.getByRole('menu').waitFor();
    await page.getByRole('menuitem').first().hover();
    await page.mouse.move(1400, 750);
    assert.equal(await page.locator('.sheet-menu-backdrop').count(), 0);
    for (const [index, clip] of clips.entries()) {
      assert.deepEqual(await page.screenshot({ clip }), before[index], name + ': opening and hovering the menu must preserve the background');
    }
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('menu').count(), 0);
  }
  await page.locator('.sheet-menu > button').filter({ hasText: /^Файл$/ }).click();
  await page.mouse.click(1400, 750);
  assert.equal(await page.getByRole('menu').count(), 0, 'Clicking outside closes the menu');
  await page.locator('.sheet-menu > button').filter({ hasText: /^Файл$/ }).click();
  await page.locator('.sheet-menu > button').filter({ hasText: /^Формат$/ }).click();
  assert.equal(await page.getByRole('menu').count(), 1, 'Menus switch directly without an overlay intercepting the click');
  assert.ok((await page.getByRole('menu').innerText()).includes('Перенос текста'));
  await page.screenshot({ path: '/tmp/spreadsheet-menu-fixed.png' });
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  console.log('All seven menus preserve the workbook and Explorer backgrounds; outside click, Escape and menu switching work. No data writes.');
} finally { await browser.close(); }
