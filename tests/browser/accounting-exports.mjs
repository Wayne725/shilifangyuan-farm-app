import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import test from 'node:test';
const {chromium} = createRequire(import.meta.url)('playwright');
const base = process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use an isolated origin');

for (const width of [1440, 816, 390]) {
  test(`${width}px 會計可直接選期間與五種格式匯出`, async t => {
    const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE || chromium.executablePath()});
    t.after(() => browser.close());
    const context = await browser.newContext({viewport: {width, height: 1000}, timezoneId: 'Asia/Taipei', serviceWorkers: 'block'});
    const requests = [], unknown = [], errors = [];
    await context.route('**/*', async route => {
      const url = new URL(route.request().url()), path = url.pathname;
      if (url.origin !== base) return route.abort();
      if (!path.startsWith('/v1/')) return route.continue();
      if (path === '/v1/auth/refresh') return route.fulfill({json: {access_token: 'isolated', user: {id: 'admin', user_role: 'admin', display_name: '管理員', email: 'admin@example.test', membership_type: 'member'}}});
      if (['/v1/products', '/v1/meal-events', '/v1/group-campaigns'].includes(path)) return route.fulfill({json: []});
      if (path === '/v1/platform') return route.fulfill({json: {orders_open: true}});
      if (path === '/v1/auth/line/status') return route.fulfill({json: {ready: true, linked: false}});
      if (path === '/v1/admin/invoice-register') return route.fulfill({json: {items: [], total: 0}});
      if (path === '/v1/admin/financial-statements') return route.fulfill({json: {items: [{id: 'sample', filename: `${url.searchParams.get('source')}明細檔`, created_at: '2030-01-02T04:00:00Z'}], total: 1}});
      if (path === '/v1/admin/financial-statements/accounting-export') {
        requests.push(Object.fromEntries(url.searchParams));
        if (url.searchParams.get('starts_on') === '2030-03-01') return route.fulfill({status: 404, json: {detail: '期間內沒有已取得發票號碼的發票'}});
        return route.fulfill({body: 'isolated-download', contentType: 'application/octet-stream'});
      }
      unknown.push(path); return route.fulfill({status: 404, json: {detail: '未定義的隔離請求'}});
    });
    const page = await context.newPage(); page.setDefaultTimeout(7000); page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/admin/accounting`);
    await page.getByRole('heading', {name: '會計格式匯出', exact: true}).waitFor();
    await page.locator('.reading-menu > summary').click();
    await page.getByRole('button', {name: '放大', exact: true}).click();
    const form = page.locator('.accounting-export');
    await form.getByRole('combobox', {name: '訂單分類', exact: true}).selectOption('general');
    await form.getByLabel('發票日期起', {exact: true}).fill('2030-01-01');
    await form.getByLabel('發票日期迄', {exact: true}).fill('2030-01-31');
    for (const [source, extension] of [['invoice', '.xlsx'], ['newebpay', '.csv'], ['jkopay', '.xlsx'], ['linepay', '.xlsx'], ['esun', '.xls']]) {
      await form.getByLabel('匯出格式', {exact: true}).selectOption(source);
      if (source !== 'invoice') await form.getByLabel('資料來源', {exact: true}).selectOption('sample');
      const waiting = page.waitForEvent('download');
      await form.getByRole('button', {name: '匯出會計檔案', exact: true}).click();
      const file = await waiting;
      assert.ok(file.suggestedFilename().endsWith(extension));
      await form.getByRole('status').filter({hasText: '匯出完成'}).waitFor();
      assert.equal(requests.at(-1).source, source);
      assert.equal(requests.at(-1).order_category, 'general');
      assert.equal(requests.at(-1).starts_on, '2030-01-01');
      assert.equal(requests.at(-1).ends_on, '2030-01-31');
      assert.equal(requests.at(-1).batch_id, source === 'invoice' ? undefined : 'sample');
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (process.env.STATEMENT_SCREENSHOT_DIR) await form.screenshot({path: `${process.env.STATEMENT_SCREENSHOT_DIR}/accounting-exports-${width}.png`});
    await form.getByLabel('匯出格式', {exact: true}).selectOption('invoice');
    await form.getByLabel('發票日期迄', {exact: true}).fill('2030-03-31');
    await form.getByLabel('發票日期起', {exact: true}).fill('2030-03-01');
    await form.getByRole('button', {name: '匯出會計檔案', exact: true}).click();
    await form.getByRole('alert').filter({hasText: '期間內沒有已取得發票號碼的發票'}).waitFor();
    assert.deepEqual(unknown, []); assert.deepEqual(errors, []);
  });
}
