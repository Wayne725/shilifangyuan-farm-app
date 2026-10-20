import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import test from 'node:test';
const {chromium} = createRequire(import.meta.url)('playwright');
const base = process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use an isolated origin');
const batch = {id: 'fixture', source: 'newebpay', filename: '測試明細.csv', row_count: 3, skipped_rows: 0, created_at: '2030-01-02T04:00:00Z'};
const rows = [
  {row_number: 2, external_id: '0000123456789012345678', reference: 'SLF-TEST-1', kind: 'receipt', amount: '200', fee: '2.1234567', payout: null, occurred_at: '2030-01-02 12:00:00', order_id: 'order1', order_number: 'SLF-TEST-1', expected_amount: '200', difference: '0', result: 'matched', reasons: [], manual_order_id: null},
  {row_number: 3, external_id: '002', reference: 'SLF-TEST-2', kind: 'receipt', amount: '199', fee: null, payout: null, occurred_at: '2030-01-02 12:00:00', order_id: 'order2', order_number: 'SLF-TEST-2', expected_amount: '200', difference: '-1', result: 'difference', reasons: ['交易金額不符'], manual_order_id: null},
  {row_number: 4, external_id: '003', reference: 'UNKNOWN', kind: 'receipt', amount: '200', fee: null, payout: null, occurred_at: '2030-01-02 12:00:00', order_id: null, order_number: null, expected_amount: null, difference: null, result: 'unmatched', reasons: ['尚未連結網站訂單'], manual_order_id: null},
];
async function fixture(t, width, admin = true) {
  const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE || chromium.executablePath()}); t.after(() => browser.close());
  const context = await browser.newContext({viewport: {width, height: 1000}, timezoneId: 'Asia/Taipei', serviceWorkers: 'block'});
  const state = {unknown: [], errors: [], writes: []};
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill(admin ? {json: {access_token: 'isolated', user: {id: 'admin', user_role: 'admin', display_name: '管理員', email: 'admin@example.test', membership_type: 'member'}}} : {status: 401, json: {detail: '未登入'}});
    if (['/v1/products', '/v1/meal-events', '/v1/group-campaigns'].includes(path)) return route.fulfill({json: []});
    if (path === '/v1/platform') return route.fulfill({json: {orders_open: true}});
    if (path === '/v1/auth/line/status') return route.fulfill({json: {ready: true, linked: false}});
    if (path === '/v1/admin/financial-statements' && req.method() === 'GET') return route.fulfill({json: {items: [batch], total: 1}});
    if (path === '/v1/admin/financial-statements' && req.method() === 'POST') {state.writes.push({type: req.headers()['content-type'], body: req.postData()}); return route.fulfill({status: 201, json: {...batch, already_imported: false}});}
    if (path === '/v1/admin/financial-statements/fixture') return route.fulfill({json: {...batch, summary: {matched: 1, difference: 1, unmatched: 1}, items: rows.filter(row => !url.searchParams.get('result') || url.searchParams.get('result') === 'all' || row.result === url.searchParams.get('result')), total: url.searchParams.get('result') === 'all' ? 3 : 1}});
    if (path === '/v1/admin/financial-statements/order-options') return route.fulfill({json: [{id: 'order1', order_number: 'SLF-TEST-1', amount: 200, created_at: '2030-01-02T04:00:00Z'}]});
    if (path.endsWith('/rows/4/match') && req.method() === 'PUT') {state.writes.push(req.postDataJSON()); return route.fulfill({json: {row_number: 4, order_id: 'order1'}});}
    if (path.endsWith('/original')) return route.fulfill({body: 'source-data', contentType: 'text/csv', headers: {'Content-Disposition': 'attachment; filename=original.csv'}});
    if (path.endsWith('/reconciliation.csv')) return route.fulfill({body: '來源,對帳結果\n藍新金流,吻合', contentType: 'text/csv', headers: {'Content-Disposition': 'attachment; filename=reconciliation.csv'}});
    state.unknown.push(`${req.method()} ${path}`); return route.fulfill({status: 404, json: {detail: '未定義的隔離請求'}});
  });
  const page = await context.newPage(); page.setDefaultTimeout(7000); page.on('pageerror', error => state.errors.push(error.message));
  return {page, state};
}
for (const width of [1440, 816, 390]) {
  test(`${width}px 匯入、篩選、保留完整數字、下載與連結訂單`, async t => {
    const {page, state} = await fixture(t, width);
    await page.goto(`${base}/admin/statements`);
    await page.getByRole('cell', {name: /0000123456789012345678/}).waitFor();
    assert.equal(await page.getByRole('cell', {name: /2\.1234567/}).count(), 1);
    await page.getByRole('button', {name: '放大', exact: true}).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (width === 390) assert.equal(await page.getByRole('region', {name: '對帳明細', exact: true}).evaluate(element => element.scrollWidth <= element.clientWidth + 1), true);
    await page.getByRole('button', {name: '有差異', exact: true}).click();
    await page.getByRole('cell', {name: /交易金額不符/}).waitFor();
    assert.equal(await page.getByRole('cell', {name: /0000123456789012345678/}).count(), 0);
    await page.getByRole('button', {name: '待連結', exact: true}).click();
    await page.getByRole('button', {name: '連結訂單', exact: true}).click();
    await page.getByLabel('網站訂單', {exact: true}).selectOption('order1');
    await page.getByRole('button', {name: '儲存連結並核對', exact: true}).click();
    await page.getByRole('status').filter({hasText: '訂單連結已更新'}).waitFor();
    assert.equal(state.writes[0].order_id, 'order1');
    for (const label of ['下載上傳原檔', '匯出對帳結果 CSV']) {
      const download = page.waitForEvent('download'); await page.getByRole('button', {name: label, exact: true}).click(); await download;
    }
    await page.getByLabel('原始明細檔', {exact: false}).setInputFiles({name: 'sample.csv', mimeType: 'text/csv', buffer: Buffer.from('header\ndata')});
    await page.getByRole('button', {name: '匯入並核對', exact: true}).click();
    await page.getByRole('status').filter({hasText: '已匯入 3 筆明細'}).waitFor();
    assert.match(state.writes[1].type, /^multipart\/form-data; boundary=/);
    assert.match(state.writes[1].body, /name="source"/);
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
    if (process.env.STATEMENT_SCREENSHOT_DIR) await page.screenshot({path: `${process.env.STATEMENT_SCREENSHOT_DIR}/financial-statements-${width}.png`, fullPage: true});
  });
  test(`${width}px 非管理員無法顯示匯入與下載操作`, async t => {
    const {page, state} = await fixture(t, width, false); await page.goto(`${base}/admin/statements`);
    await page.getByRole('heading', {name: '金流與發票對帳', exact: true}).waitFor();
    assert.equal(await page.getByRole('button', {name: '匯入並核對', exact: true}).count(), 0);
    assert.deepEqual(state.writes, []); assert.deepEqual(state.unknown, []);
  });
}
