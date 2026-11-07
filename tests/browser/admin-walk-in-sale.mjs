import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;
const requestKey = 'slf_admin_walk_in_request_v1';
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const staffBarcode = '/STAFF12';
const customerBarcode = '/AB12+-.';

async function fixture(t, { width = 1280, firstResponse = 'success' } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { posts: [], recoveries: [], sales: new Map(), unknown: [], errors: [], consoleErrors: [] };
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) {
      const expectedFont = (url.hostname === 'fonts.googleapis.com' && request.resourceType() === 'stylesheet')
        || (url.hostname === 'fonts.gstatic.com' && request.resourceType() === 'font');
      if (!expectedFont) state.unknown.push(`EXTERNAL ${url.origin}`);
      return route.abort();
    }
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: {
      id: 'isolated-staff', email: 'staff@example.test', display_name: '隔離店員', user_role: 'admin',
      membership_type: 'nonmember', saved_mobile_barcode: staffBarcode,
    } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '暫停接受新訂單' } });
    if (path === '/v1/notifications') return route.fulfill({ json: [] });
    if (path === '/v1/admin/order-search') return route.fulfill({ json: { items: [], total: 0, limit: 12, offset: 0 } });
    if (request.method() === 'GET' && path.startsWith('/v1/admin/walk-in-sales/request/')) {
      const id = path.split('/').at(-1);
      state.recoveries.push(id);
      const existing = state.sales.get(id);
      return route.fulfill(existing ? { json: existing.sale } : { status: 404, json: { detail: '尚未找到此筆隔離交易' } });
    }
    if (request.method() === 'POST' && path === '/v1/admin/walk-in-sales') {
      const raw = request.postData(), body = request.postDataJSON();
      state.posts.push({ raw, body });
      const existing = state.sales.get(body.client_request_id);
      if (existing && existing.raw !== raw) return route.fulfill({ status: 409, json: { detail: '同一編號不得變更交易內容' } });
      const sale = existing?.sale || {
        order_id: 'isolated-walk-in-order', order_number: 'O2030010201', amount_total: body.unit_price * body.quantity,
        invoice_handling: body.invoice_handling,
        invoice_issue_after: body.invoice_handling === 'pickup_day' ? `${body.pickup_date}T00:00:00+08:00` : null,
      };
      state.sales.set(body.client_request_id, { raw, sale });
      if (state.posts.length === 1 && firstResponse === 'abort') return route.abort('failed');
      if (state.posts.length === 1 && firstResponse === '503') return route.fulfill({ status: 503, json: { detail: '隔離回應中斷，交易結果待確認' } });
      return route.fulfill({ status: existing ? 200 : 201, json: sale });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') state.consoleErrors.push(message.text()); });
  await page.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  await page.goto(`${base}/admin?section=orders`);
  const region = page.getByRole('region', { name: '現場收款與開票', exact: true });
  try {
    await region.getByRole('button', { name: '現場收款與開票', exact: true }).click();
    await region.getByLabel('含稅單價', { exact: true }).waitFor();
  } catch (error) {
    t.diagnostic(JSON.stringify({ url: page.url(), errors: state.errors, unknown: state.unknown,
      consoleErrors: state.consoleErrors.slice(0, 10), body: (await page.locator('body').innerText()).slice(0, 6000) }));
    throw error;
  }
  return { page, region, state };
}

async function fillSale(region, { delayed = false, barcode = false } = {}) {
  await region.getByLabel('品名', { exact: true }).fill('合成餐盒');
  await region.getByLabel('含稅單價', { exact: true }).fill('120');
  await region.getByLabel('數量', { exact: true }).fill('2');
  await region.getByLabel('顧客姓名（選填）', { exact: true }).fill('合成顧客');
  await region.getByLabel('發票通知 Email', { exact: true }).fill('customer@example.test');
  if (delayed) {
    await region.getByLabel('開票時機', { exact: true }).selectOption('pickup_day');
    await region.getByLabel('取餐日期', { exact: true }).fill('2030-01-03');
  }
  if (barcode) {
    await region.getByRole('radio', { name: '手機條碼載具', exact: true }).check();
    assert.equal(await region.getByLabel('手機條碼', { exact: true }).inputValue(), '', '不得帶入店員已儲存的載具');
    await region.getByLabel('手機條碼', { exact: true }).fill(customerBarcode);
  }
  await region.getByLabel('已確認收到現金', { exact: true }).check();
  await region.getByLabel('已確認此筆未由其他系統開立發票', { exact: true }).check();
}

async function assertRecorded(region) {
  await region.getByText(/已記錄收款.*O2030010201/).waitFor();
  assert.equal(await region.getByRole('button', { name: '確認收款與開票安排', exact: true }).count(), 0);
  assert.equal(await region.getByRole('button', { name: '重試同一筆交易', exact: true }).count(), 0);
}

async function assertClean(page, state) {
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, '頁面不能橫向溢出');
}

test('手機現場收款可送日期及顧客載具，店員載具與 Email 不帶入', async t => {
  const { page, region, state } = await fixture(t, { width: 390 });
  assert.equal(await region.getByLabel('發票通知 Email', { exact: true }).inputValue(), '');
  assert.equal(await region.getByRole('checkbox', { name: '結帳時儲存此手機條碼供下次使用' }).count(), 0);
  await fillSale(region, { delayed: true, barcode: true });
  await assertClean(page, state);
  await region.getByRole('button', { name: '確認收款與開票安排', exact: true }).click();
  await assertRecorded(region);
  assert.equal(state.posts.length, 1);
  const body = state.posts[0].body;
  assert.match(body.client_request_id, uuidPattern);
  assert.deepEqual(body, {
    client_request_id: body.client_request_id, product_name: '合成餐盒', unit_price: 120, quantity: 2,
    tax_type: 'taxable', customer_name: '合成顧客', contact_email: 'customer@example.test',
    invoice_handling: 'pickup_day', pickup_date: '2030-01-03',
    preferences: { invoice_buyer_type: 'personal', invoice_buyer_email: 'customer@example.test', invoice_carrier_type: 'mobile_barcode', invoice_carrier_value: customerBarcode },
    reason: '現場客人收款', confirm_received: true, confirm_not_invoiced_elsewhere: true,
  });
  assert.equal(state.posts[0].raw.includes(staffBarcode), false);
  await region.getByText(/預定開票日/).waitFor();
  await assertClean(page, state);
});

for (const firstResponse of ['abort', '503']) {
  test(`現場收款 ${firstResponse} 後以相同 UUID 與完整原文重試`, async t => {
    const { page, region, state } = await fixture(t, { firstResponse });
    await fillSale(region);
    await region.getByRole('button', { name: '確認收款與開票安排', exact: true }).click();
    const retry = region.getByRole('button', { name: '重試同一筆交易', exact: true });
    await retry.waitFor();
    await region.getByRole('alert').waitFor();
    assert.equal(await region.getByLabel('含稅單價', { exact: true }).isDisabled(), true);
    assert.equal(await region.getByLabel('發票通知 Email', { exact: true }).isDisabled(), true);
    assert.equal(state.posts.length, 1);
    assert.match(state.posts[0].body.client_request_id, uuidPattern);
    await retry.click();
    await assertRecorded(region);
    assert.equal(state.posts.length, 2);
    assert.equal(state.posts[1].raw, state.posts[0].raw);
    assert.equal(state.posts[1].body.client_request_id, state.posts[0].body.client_request_id);
    assert.equal(state.sales.size, 1);
    await assertClean(page, state);
  });
}

test('收款回應中斷後重新整理，只用保存的 UUID 查回交易、不重送 POST', async t => {
  const { page, region, state } = await fixture(t, { firstResponse: 'abort', width: 390 });
  await fillSale(region, { delayed: true, barcode: true });
  await region.getByRole('button', { name: '確認收款與開票安排', exact: true }).click();
  await region.getByRole('button', { name: '重試同一筆交易', exact: true }).waitFor();
  const requestId = state.posts[0].body.client_request_id;
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), requestKey), requestId);
  const storage = await page.evaluate(() => JSON.stringify(Object.fromEntries(Object.entries(sessionStorage))));
  assert.equal(storage.includes('customer@example.test'), false);
  assert.equal(storage.includes(customerBarcode), false);
  await page.reload();
  await region.getByRole('button', { name: '現場收款與開票', exact: true }).click();
  await assertRecorded(region);
  assert.equal(state.posts.length, 1);
  assert.ok(state.recoveries.filter(id => id === requestId).length >= 2);
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), requestKey), requestId);
  await assertClean(page, state);
});
