import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import test from 'node:test';
const {chromium} = createRequire(import.meta.url)('playwright');
const base = process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use an isolated origin');

for (const width of [1440, 390]) {
  test(`${width}px 發票分類查詢與CSV條件一致`, async t => {
    const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE || chromium.executablePath()});
    t.after(() => browser.close());
    const context = await browser.newContext({viewport: {width, height: 1000}, serviceWorkers: 'block'});
    const reads = [], downloads = [], errors = [], unknown = [];
    const rows = ['regular', 'group', 'meal_preorder'].map((sales_channel, index) => ({order_id: `${index}`, order_number: `INVOICE-${sales_channel}`, sales_channel, email: 'buyer@example.test', status: 'issued', amount: 200, tax_amount: 0, invoice_number: `AB1234567${index}`, invoice_date: '2030-01-02T04:00:00Z'}));
    await context.route('**/*', async route => {
      const url = new URL(route.request().url()), path = url.pathname;
      if (url.origin !== base) return route.abort();
      if (!path.startsWith('/v1/')) return route.continue();
      if (path === '/v1/auth/refresh') return route.fulfill({json: {access_token: 'isolated', user: {id: 'admin', user_role: 'admin', display_name: '管理員', email: 'admin@example.test', membership_type: 'member'}}});
      if (['/v1/products', '/v1/meal-events', '/v1/group-campaigns'].includes(path)) return route.fulfill({json: []});
      if (path === '/v1/platform') return route.fulfill({json: {orders_open: true}});
      if (path === '/v1/auth/line/status') return route.fulfill({json: {ready: true}});
      if (path === '/v1/admin/invoice-register') {
        const params = Object.fromEntries(url.searchParams); reads.push(params);
        const items = rows.filter(row => (params.order_category === 'all' || (params.order_category === 'meal' ? row.sales_channel === 'meal_preorder' : row.sales_channel !== 'meal_preorder')) && (!params.q || row.order_number.includes(params.q)));
        return route.fulfill({json: {items, total: items.length}});
      }
      if (path === '/v1/admin/invoice-register.csv') {downloads.push(Object.fromEntries(url.searchParams)); return route.fulfill({body: '訂單編號,訂單分類', contentType: 'text/csv'});}
      unknown.push(path); return route.fulfill({status: 404, json: {detail: '未定義隔離請求'}});
    });
    const page = await context.newPage(); page.setDefaultTimeout(7000); page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${base}/admin/invoices`);
    await page.getByText('全部訂單 · 共 3 筆', {exact: true}).waitFor();
    await page.getByRole('combobox', {name: '訂單分類', exact: true}).selectOption('general');
    await page.getByText('一般訂單 · 共 2 筆', {exact: true}).waitFor();
    await page.getByLabel('訂單編號、發票號碼或 Email').fill('group');
    await page.getByRole('button', {name: '查詢', exact: true}).click();
    await page.getByText('一般訂單 · 共 1 筆', {exact: true}).waitFor();
    const download = page.waitForEvent('download');
    await page.getByRole('button', {name: '匯出網站核對 CSV', exact: true}).click(); await download;
    assert.equal(downloads.at(-1).order_category, 'general'); assert.equal(downloads.at(-1).q, 'group');
    await page.getByLabel('訂單編號、發票號碼或 Email').fill('');
    await page.getByRole('button', {name: '查詢', exact: true}).click();
    await page.getByRole('combobox', {name: '訂單分類', exact: true}).selectOption('meal');
    await page.getByText('便當訂單 · 共 1 筆', {exact: true}).waitFor();
    assert.equal(reads.at(-1).order_category, 'meal');
    await page.locator('.reading-menu > summary').click();
    await page.getByRole('button', {name: '放大', exact: true}).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(unknown, []); assert.deepEqual(errors, []);
  });
}
