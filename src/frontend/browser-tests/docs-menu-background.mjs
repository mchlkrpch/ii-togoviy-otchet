import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';

// Read-only regression: opening menus must not cover the document or Explorer.
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
  await page.locator('.unified-source-row').filter({ hasText: '11_' }).first().click();
  await page.locator('.rich-document-content').waitFor();
  await page.locator('.rich-document-content td').first().click();
  await page.mouse.move(1400, 750);
  const clips = [{ x: 30, y: 160, width: 140, height: 350 }, { x: 1300, y: 650, width: 250, height: 250 }];
  const before = await Promise.all(clips.map(clip => page.screenshot({ clip })));
  for (const name of ['Файл', 'Правка', 'Вид', 'Вставка', 'Формат', 'Таблица', 'Инструменты']) {
    await page.locator('.rich-menu > button').filter({ hasText: new RegExp('^' + name + '$') }).click();
    await page.getByRole('menu').waitFor();
    await page.getByRole('menuitem').first().hover();
    await page.mouse.move(1400, 750);
    assert.equal(await page.locator('.rich-menu-backdrop').count(), 0);
    for (const [index, clip] of clips.entries()) {
      assert.deepEqual(await page.screenshot({ clip }), before[index], name + ': opening and hovering the menu must preserve the background');
    }
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('menu').count(), 0);
  }
  await page.locator('.rich-menu > button').filter({ hasText: /^Файл$/ }).click();
  await page.mouse.click(1400, 750);
  assert.equal(await page.getByRole('menu').count(), 0, 'Clicking outside closes the menu');
  await page.locator('.rich-menu > button').filter({ hasText: /^Файл$/ }).click();
  await page.locator('.rich-menu > button').filter({ hasText: /^Формат$/ }).click();
  assert.equal(await page.getByRole('menu').count(), 1, 'Menus switch directly without an overlay intercepting the click');
  assert.ok((await page.getByRole('menu').innerText()).includes('Полужирный'));
  await page.screenshot({ path: '/tmp/docs-menu-fixed.png' });
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  console.log('All seven menus preserve the document and Explorer backgrounds; outside click, Escape and menu switching work. No data writes.');
} finally { await browser.close(); }
