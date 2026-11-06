import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;
const existingCarrier = '/AB12+-.';

async function fixture(t, channel) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = {
    posts: [], unknown: [], errors: [], consoleErrors: [], release: null,
    preferences: { invoice_buyer_type: 'personal', invoice_buyer_email: 'customer@example.test', invoice_carrier_type: 'mobile_barcode', invoice_carrier_value: existingCarrier },
    order: {
      id: 'isolated-invoice-order', order_number: 'ISOLATED-INVOICE', sales_channel: channel,
      fulfillment_method: channel === 'meal_preorder' ? 'event_pickup' : 'coop_pickup',
      payment_method: 'online', payment_status: 'paid', invoice_status: 'pending', invoice: null,
      invoice_handling: 'on_payment', invoice_issue_after: null,
      fulfillment_status: 'pending_confirmation', amount_total: 120, tax_amount: 6,
      created_at: '2030-01-02T01:00:00Z', available_actions: [], customer_name: '合成買家',
      contact_phone: null, contact_phone_status: 'missing', contact_email: 'customer@example.test',
      invoice_buyer_type: 'personal', invoice_buyer_email: 'customer@example.test', invoice_carrier_type: 'mobile_barcode',
      items: [{ id: 'item', product_name: '合成商品', quantity: 1, unit_label: '份', unit_price: 120, subtotal: 120, tax_type: 'taxable' }],
    },
  };
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
      id: 'isolated-admin', email: 'staff@example.test', display_name: '隔離管理者', user_role: 'admin', membership_type: 'nonmember', saved_mobile_barcode: '/STAFF12',
    } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '暫停接受新訂單' } });
    if (path === '/v1/notifications') return route.fulfill({ json: [] });
    if (path === '/v1/admin/order-search') return route.fulfill({ json: { items: [state.order], total: 1, limit: 12, offset: 0 } });
    if (request.method() === 'POST' && path === `/v1/admin/orders/${state.order.id}/invoice/issue`) {
      const body = request.postDataJSON();
      state.posts.push(body);
      if (body.preferences) state.preferences = body.preferences;
      state.order = { ...state.order, invoice_handling: body.handling,
        invoice_issue_after: body.handling === 'pickup_day' ? '2030-01-03T00:00:00+08:00' : null };
      return new Promise(resolve => {
        state.release = async () => {
          await route.fulfill({ json: { order_id: state.order.id, invoice_status: 'pending',
            invoice_handling: state.order.invoice_handling, invoice_issue_after: state.order.invoice_issue_after } });
          resolve();
        };
      });
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
  const region = page.getByRole('region', { name: '開票安排 ISOLATED-INVOICE', exact: true });
  try { await region.getByRole('button', { name: '手動安排開票', exact: true }).click(); }
  catch (error) {
    t.diagnostic(JSON.stringify({ url: page.url(), errors: state.errors, unknown: state.unknown,
      consoleErrors: state.consoleErrors.slice(0, 10), body: (await page.locator('body').innerText()).slice(0, 6000) }));
    throw error;
  }
  return { page, region, state };
}

async function confirmAndHold(page, region, state) {
  await region.getByLabel('處理原因', { exact: true }).fill('  顧客要求取餐日報帳  ');
  const confirm = region.getByRole('button', { name: '確認開票安排', exact: true });
  assert.equal(await confirm.isDisabled(), true, '未核對外部開票前不可送出');
  await region.getByLabel('已確認此筆未由其他系統開立發票', { exact: true }).check();
  const posted = page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/invoice/issue'));
  await confirm.click();
  await posted;
  const busy = region.getByRole('button', { name: '記錄中…', exact: true });
  await busy.waitFor();
  assert.equal(await busy.isDisabled(), true);
  await busy.evaluate(button => { button.click(); button.click(); });
  await region.getByLabel('處理原因', { exact: true }).press('Enter');
  assert.equal(state.posts.length, 1, '處理中不可再次送出開票安排');
  assert.equal(typeof state.release, 'function');
  await state.release();
}

test('已收款便當舊單安排取餐日開票，保留原載具且處理中不重送', async t => {
  const { page, region, state } = await fixture(t, 'meal_preorder');
  await region.getByLabel('開票時機', { exact: true }).selectOption('pickup_day');
  assert.equal(await region.getByLabel('修改發票資料', { exact: true }).isChecked(), false);
  await confirmAndHold(page, region, state);
  await region.getByText(/預定開票日/).waitFor();
  assert.deepEqual(state.posts, [{ handling: 'pickup_day', reason: '顧客要求取餐日報帳', confirm_not_invoiced_elsewhere: true }]);
  assert.equal(Object.hasOwn(state.posts[0], 'preferences'), false);
  assert.equal(state.preferences.invoice_carrier_value, existingCarrier);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

test('一般商品只提供現在開立，勾選修改後才傳發票資料', async t => {
  const { page, region, state } = await fixture(t, 'regular');
  const timing = region.getByLabel('開票時機', { exact: true });
  assert.deepEqual(await timing.locator('option').evaluateAll(options => options.map(option => option.value)), ['on_payment']);
  await region.getByLabel('修改發票資料', { exact: true }).check();
  await region.getByLabel('發票通知 Email', { exact: true }).fill('updated-customer@example.test');
  await confirmAndHold(page, region, state);
  await region.getByRole('button', { name: '手動安排開票', exact: true }).waitFor();
  assert.deepEqual(state.posts, [{ handling: 'on_payment', reason: '顧客要求取餐日報帳', confirm_not_invoiced_elsewhere: true,
    preferences: { invoice_buyer_type: 'personal', invoice_buyer_email: 'updated-customer@example.test', invoice_carrier_type: 'cloud' } }]);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});
