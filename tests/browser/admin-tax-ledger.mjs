import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const browserType = createRequire(import.meta.url)('playwright')[process.env.BROWSER_ENGINE || 'chromium'];
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4192');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) {
  throw new Error('Use an isolated local origin');
}
const base = target.origin;
const ledgerPath = '/v1/admin/finance/tax-ledger';
const membershipPath = '/v1/admin/finance/sales-by-membership';
const row = {
  tax_type: 'taxable', membership_type: 'trainee', sales_channel: 'meal_preorder',
  sales_amount: 545, tax_amount: 25, order_count: 2,
};
const csv = '\ufeff稅別,身分,銷售通路,銷售額,稅額,訂單筆數\r\ntaxable,trainee,meal_preorder,545,25,2\r\n';
const sales = {
  summary: { received: 0, refunded: 0, net: 0, receipt_count: 0, refund_count: 0, paid_meal_quantity: 0, pending_refund_amount: 0 },
  orders: { items: [], total: 0 }, daily: [], weekly: [], monthly: [], products: [],
};

async function fixture(t, { width = 1280, role = 'admin' } = {}) {
  const browser = await browserType.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({
    viewport: { width, height: 860 }, locale: 'zh-TW', timezoneId: 'Asia/Taipei',
    serviceWorkers: 'block', acceptDownloads: true, reducedMotion: 'reduce',
  });
  const state = { requests: [], membershipRequests: [], errors: [], unexpected: [], expired: false, forbidden: false };
  await context.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/') && request.method() === 'GET') return route.continue();
    if (path === '/v1/auth/refresh' && request.method() === 'POST') {
      if (!role || state.expired) return route.fulfill({ status: 401, json: { detail: '登入已過期' } });
      return route.fulfill({ json: { access_token: 'isolated', user: {
        id: 'isolated-id', email: 'isolated@example.test', display_name: '合成帳號', user_role: role,
      } } });
    }
    if (request.method() === 'GET') {
      if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '' } });
      if (path === '/v1/admin/finance/meal-sales') return route.fulfill({ json: sales });
      if (path === membershipPath) {
        const period = Object.fromEntries(url.searchParams);
        state.membershipRequests.push({ period, authorization: request.headers().authorization });
        if (state.forbidden) return route.fulfill({ status: 403, json: { detail: '需要管理員權限' } });
        const empty = period.starts_on === '2030-02-01';
        return route.fulfill({ json: {
          ...period, total_revenue: empty ? 0 : 600, monitoring_threshold_reached: empty ? null : 0.3,
          sales_breakdown: [
            { membership_type: 'nonmember', revenue: empty ? 0 : 200, ratio: empty ? 0 : 1 / 3 },
            { membership_type: 'trainee', revenue: empty ? 0 : 100, ratio: empty ? 0 : 1 / 6 },
            { membership_type: 'member', revenue: empty ? 0 : 300, ratio: empty ? 0 : 0.5 },
          ],
        } });
      }
      if ([ledgerPath, `${ledgerPath}.csv`].includes(path)) {
        const period = Object.fromEntries(url.searchParams);
        const exporting = path.endsWith('.csv');
        state.requests.push({ exporting, period, authorization: request.headers().authorization });
        if (state.expired) return route.fulfill({ status: 401, json: { detail: '登入已過期' } });
        if (state.forbidden) return route.fulfill({ status: 403, json: { detail: '需要管理員權限' } });
        if (exporting) return route.fulfill({ contentType: 'text/csv; charset=utf-8', body: csv });
        return route.fulfill({ json: { ...period, rows: period.starts_on === '2030-02-02' ? [] : [row] } });
      }
    }
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.clock.setFixedTime(new Date('2030-02-01T03:00:00+08:00'));
  await page.goto(`${base}/admin/finance`);
  return { page, state };
}

for (const width of [1280, 390]) {
  test(`${width}px 稅務彙總的已提交期間、顯示與 CSV 一致`, async (t) => {
    const { page, state } = await fixture(t, { width });
    await page.getByRole('heading', { name: '銷售明細與統整' }).waitFor();
    assert.deepEqual(state.requests, []);
    await page.getByRole('tab', { name: '稅務彙總', exact: true }).click();
    const table = page.getByRole('table', { name: '稅務彙總明細' });
    const downloadButton = page.getByRole('button', { name: '下載預估 CSV' });
    await table.getByRole('columnheader', { name: '預估稅額' }).waitFor();
    assert.deepEqual(await table.locator('tbody td').allTextContents(), ['應稅', '實習社員', '便當', '$545', '$25', '2']);
    assert.deepEqual(state.requests[0].period, { starts_on: '2030-01-01', ends_on: '2030-01-31' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);

    const start = page.getByLabel('付款日期起');
    const end = page.getByLabel('付款日期迄');
    await start.fill('2030-02-01');
    await page.getByRole('button', { name: '查詢', exact: true }).click();
    await page.getByText('結束日期不可早於開始日期。', { exact: true }).waitFor();
    assert.equal(state.requests.length, 1);
    await end.fill('2030-02-01');
    await page.getByRole('button', { name: '查詢', exact: true }).click();
    await page.getByText('2030-02-01 至 2030-02-01（UTC）', { exact: true }).waitFor();
    await end.fill('2030-02-02');
    const [download] = await Promise.all([page.waitForEvent('download'), downloadButton.click()]);
    assert.equal(download.suggestedFilename(), '稅務彙總（預估）-2030-02-01-2030-02-01.csv');
    assert.deepEqual(state.requests.at(-1), { ...state.requests.at(-2), exporting: true });

    await start.fill('2030-02-02');
    await page.getByRole('button', { name: '查詢', exact: true }).click();
    await page.getByText('這段期間沒有已付款品項', { exact: true }).waitFor();
    assert.equal(await table.count(), 0);
    assert.deepEqual(state.errors, []);
    assert.deepEqual(state.unexpected, []);
    assert.equal(state.requests.every((entry) => entry.authorization === 'Bearer isolated'), true);
  });
}

test('未登入與一般買家不能查詢或下載稅務彙總', async (t) => {
  for (const role of [null, 'customer']) {
    const { page, state } = await fixture(t, { role });
    await page.getByRole('heading', { name: '財務報表只向管理員開放。' }).waitFor();
    if (!role) await page.getByRole('button', { name: '管理員登入' }).waitFor();
    assert.equal(await page.getByRole('tab', { name: '稅務彙總' }).count(), 0);
    assert.equal(await page.getByRole('tab', { name: '身分銷售占比' }).count(), 0);
    assert.deepEqual(state.requests, []);
    assert.deepEqual(state.membershipRequests, []);
    assert.deepEqual(state.unexpected, []);
  }
});

test('已開啟彙總的管理員登入過期時停止下載並移除舊資料', async (t) => {
  const { page, state } = await fixture(t);
  await page.getByRole('tab', { name: '稅務彙總', exact: true }).click();
  const table = page.getByRole('table', { name: '稅務彙總明細' });
  await table.waitFor();
  const downloads = [];
  page.on('download', (download) => downloads.push(download));
  state.expired = true;
  await page.getByRole('button', { name: '下載預估 CSV' }).click();
  await page.getByRole('button', { name: '管理員登入' }).waitFor();
  assert.deepEqual(state.requests.map((request) => request.exporting), [false, true]);
  assert.equal(await table.count(), 0);
  assert.deepEqual(downloads, []);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unexpected, []);
});

test('管理員權限撤銷時隱藏報表與下載按鈕，重新查詢仍須通過授權', async (t) => {
  const { page, state } = await fixture(t);
  await page.getByRole('tab', { name: '稅務彙總', exact: true }).click();
  const table = page.getByRole('table', { name: '稅務彙總明細' });
  const downloadButton = page.getByRole('button', { name: '下載預估 CSV' });
  await table.waitFor();
  state.forbidden = true;
  await downloadButton.click();
  await page.getByText('需要管理員權限', { exact: true }).waitFor();
  assert.equal(await table.count(), 0);
  assert.equal(await downloadButton.count(), 0);
  await page.getByRole('button', { name: '查詢', exact: true }).click();
  await page.getByText('需要管理員權限', { exact: true }).waitFor();
  assert.deepEqual(state.requests.map((request) => request.exporting), [false, true, false]);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unexpected, []);
});

for (const width of [1280, 390]) {
  test(`${width}px 身分銷售占比用唯讀查詢，顯示三類身分與營運提醒`, async (t) => {
    const { page, state } = await fixture(t, { width });
    await page.getByRole('heading', { name: '銷售明細與統整' }).waitFor();
    assert.deepEqual(state.membershipRequests, []);
    await page.getByRole('tab', { name: '身分銷售占比', exact: true }).click();
    const table = page.getByRole('table', { name: '結帳身分銷售占比' });
    await table.waitFor();
    assert.deepEqual(await table.locator('tbody th').allTextContents(), ['一般買家', '實習社員', '社員']);
    assert.deepEqual(await table.locator('tbody td').allTextContents(), ['$200', '33.3%', '$100', '16.7%', '$300', '50%']);
    await page.getByText('一般買家占比達營運提醒值 30%', { exact: true }).waitFor();
    assert.deepEqual(state.membershipRequests, [{
      period: { starts_on: '2030-01-01', ends_on: '2030-01-31' }, authorization: 'Bearer isolated',
    }]);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.getByLabel('付款日期起').fill('2030-02-01');
    await page.getByRole('button', { name: '查詢', exact: true }).click();
    await page.getByText('結束日期不可早於開始日期。', { exact: true }).waitFor();
    assert.equal(state.membershipRequests.length, 1);
    await page.getByLabel('付款日期迄').fill('2030-02-01');
    await page.getByRole('button', { name: '查詢', exact: true }).click();
    await page.getByText('2030-02-01 至 2030-02-01（UTC）', { exact: true }).waitFor();
    assert.deepEqual(await table.locator('tbody td').allTextContents(), ['$0', '—', '$0', '—', '$0', '—']);
    assert.equal(await page.getByText('一般買家占比達營運提醒值 30%', { exact: true }).count(), 0);
    state.forbidden = true;
    await page.getByRole('button', { name: '查詢', exact: true }).click();
    await page.getByText('需要管理員權限', { exact: true }).waitFor();
    assert.equal(await table.count(), 0);
    assert.deepEqual(state.requests, []);
    assert.deepEqual(state.errors, []);
    assert.deepEqual(state.unexpected, []);
  });
}
