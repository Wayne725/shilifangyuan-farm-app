import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { extname, resolve } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const base = 'http://127.0.0.1:4265';
const root = resolve(process.env.BROWSER_DIST_ROOT || resolve(import.meta.dirname, '../../web/dist'));
const contentTypes = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp' };

async function fixture(t, { payment = 'pending', result = 'paid', missing = false, failure = false, attempt = null, logistics = null, payOnAttempt = false, payable = false, attemptFailure = false, message = '付款已成功確認金額99999元' } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: Number(process.env.BROWSER_WIDTH || 390), height: 1000 }, serviceWorkers: 'block' });
  const order = { id: 'return-order', order_number: 'RETURN-TRUTH', revision: 2, order_kind: 'regular', sales_channel: 'regular',
    fulfillment_method: logistics ? 'ecpay_logistics' : 'cooperative_pickup', membership_type_snapshot: 'nonmember', amount_total: 100, tax_amount: 5,
    payment_status: payment, invoice_status: 'not_eligible', fulfillment_status: 'pending_confirmation', available_actions: payable ? ['pay', 'amend_quantities'] : [],
    created_at: '2030-01-01T00:00:00Z', items: [{ id: 'truth-line', product_name: '核對商品', quantity: 1, unit_price: 100, subtotal: 100, unit_label: '份', tax_type: 'taxable' }],
    ...(logistics ? { shipment: { channel: 'home_delivery', temperature: 'ambient', status: logistics, shipping_fee: 0 } } : {}),
  };
  const state = { reads: [], unknown: [], errors: [] };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) {
      const file = extname(path) ? resolve(root, `.${path}`) : resolve(root, 'index.html');
      assert.ok(file.startsWith(`${root}/`));
      return route.fulfill({ contentType: contentTypes[extname(file)] || 'application/octet-stream', body: await readFile(file) });
    }
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'truth-fixture', user: { id: 'truth-buyer', email: 'buyer@example.test', display_name: '核對買家', membership_type: 'nonmember', user_role: 'customer' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/meal-orders') return route.fulfill({ json: [] });
    if (path === '/v1/orders') { state.reads.push(path); return route.fulfill({ status: failure ? 503 : 200, json: failure ? { detail: '隔離訂單讀取失敗' } : missing ? [] : [order] }); }
    if (path === '/v1/orders/return-order') { state.reads.push(path); return route.fulfill({ status: missing ? 404 : 200, json: missing ? { detail: '找不到本人訂單' } : order }); }
    if (path === '/v1/payment-attempts/attempt-a/refresh' && attempt) {
      if (attemptFailure) return route.fulfill({ status: 503, json: { detail: '隔離付款查詢暫時無法使用' } });
      if (payOnAttempt) order.payment_status = 'paid';
      return route.fulfill({ json: attempt });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated return route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  const params = new URLSearchParams({ order_id: order.id, message });
  if (attempt) { params.set('payment', 'confirming'); params.set('attempt_id', 'attempt-a'); }
  else if (logistics) params.set('logistics', 'selected');
  else params.set('result', result);
  const first = page.waitForResponse(response => new URL(response.url()).pathname === '/v1/orders');
  const lookup = missing ? page.waitForResponse(response => new URL(response.url()).pathname === '/v1/orders/return-order') : Promise.resolve();
  const checked = attempt ? page.waitForResponse(response => new URL(response.url()).pathname === '/v1/payment-attempts/attempt-a/refresh') : Promise.resolve();
  await page.goto(`${base}/orders?${params}`);
  await first; await lookup; await checked;
  await new Promise(resolve => setTimeout(resolve, 100));
  t.after(() => { assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []); });
  return { page, state, order };
}

async function assertNotPaid(page) {
  const banner = page.locator('.return-banner');
  assert.equal(await banner.getByText('線上付款已完成', { exact: true }).count(), 0);
  assert.equal(await banner.getByText('付款已確認', { exact: true }).count(), 0);
  assert.equal((await banner.innerText()).includes('這筆訂單目前已付款'), false);
  assert.equal(await page.getByText('付款已成功確認金額99999元', { exact: true }).count(), 0);
}

test('pending本人單不能用URL偽paid或message宣稱付款完成', async t => {
  const { page } = await fixture(t);
  await assertNotPaid(page);
});

test('不存在或非本人單的URL resume不得宣稱訂單已保存', async t => {
  const { page } = await fixture(t, { result: 'resume', missing: true });
  assert.equal(await page.getByText('訂單已保存，請從這裡繼續', { exact: true }).count(), 0);
});

for (const extra of [{ order_id: 'other-order', order_revision: 2, superseded: false }, { order_id: 'return-order', order_revision: 1, superseded: true }, { order_id: 'return-order' }]) {
  test(`另一單／舊版／缺證據attempt succeeded不能把pending原單稱為已付 ${JSON.stringify(extra)}`, async t => {
    const { page } = await fixture(t, { attempt: { id: 'attempt-a', status: 'succeeded', ...extra } });
    await assertNotPaid(page);
  });
}

for (const [payment, title] of [['paid','付款已確認'],['refunded','退款已完成'],['refund_pending','退款處理中'],['late_paid_refund_required','付款晚到，退款待處理'],['expired','訂單付款已逾期'],['failed','付款尚未完成']]) {
  test(`真正Order ${payment}保留正確目前狀態，不接受URL message改寫`, async t => {
    const { page } = await fixture(t, { payment });
    await page.locator('.return-banner').getByText(title, { exact: true }).waitFor();
    assert.equal(await page.getByText('付款已成功確認金額99999元', { exact: true }).count(), 0);
  });
}

test('Order API失敗不能用URL paid顯示成功，保留無法確認結果', async t => {
  const { page } = await fixture(t, { failure: true });
  await page.getByText('返回結果暫時無法確認', { exact: true }).waitFor();
  await assertNotPaid(page);
});

test('已存在本人原單resume可續辦，但不採用URL任意付款敘述', async t => {
  const { page } = await fixture(t, { result: 'resume' });
  await page.getByText('訂單已保存，請從這裡繼續', { exact: true }).waitFor();
  await assertNotPaid(page);
});

for (const [logistics, confirmed] of [['ready_to_create',true],['selection_pending',false]]) {
  test(`物流selected只依目前shipment實際資料 ${logistics}`, async t => {
    const { page } = await fixture(t, { logistics });
    await page.getByText(confirmed ? '物流資料已確認' : '物流資料尚未確認', { exact: true }).waitFor();
    await assertNotPaid(page);
  });
}

test('完全匹配的succeeded仍需Order入帳，不能直接把pending單稱成功', async t => {
  const { page, state } = await fixture(t, { attempt: { id: 'attempt-a', order_id: 'return-order', order_revision: 2, superseded: false, status: 'succeeded' } });
  await page.getByText('付款結果確認中', { exact: true }).waitFor();
  await assertNotPaid(page);
  assert.ok(state.reads.length >= 2);
});

test('正常匹配attempt完成後重讀Order，真正paid才顯示付款確認', async t => {
  const { page, state } = await fixture(t, { payOnAttempt: true, attempt: { id: 'attempt-a', order_id: 'return-order', order_revision: 2, superseded: false, status: 'succeeded' } });
  await page.getByText('付款已確認', { exact: true }).waitFor();
  assert.ok(state.reads.length >= 2);
});

test('正常confirming期間保留可pay原單的付款與改量等待鎖', async t => {
  const { page } = await fixture(t, { payable: true, attempt: { id: 'attempt-a', order_id: 'return-order', order_revision: 2, superseded: false, status: 'pending' } });
  await page.getByText('付款結果確認中', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '前往線上付款', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '修改數量', exact: true }).isEnabled(), false);
});

test('refresh503仍須保留confirming付款與改量等待鎖，不借未確認提示放行', async t => {
  const { page } = await fixture(t, { payable: true, attemptFailure: true, attempt: { id: 'attempt-a', order_id: 'return-order', order_revision: 2, superseded: false, status: 'pending' } });
  await page.getByText('無法確認這次付款結果', { exact: true }).waitFor();
  await assertNotPaid(page);
  assert.deepEqual({ payButtons: await page.getByRole('button', { name: '前往線上付款', exact: true }).count(),
    amendmentEnabled: await page.getByRole('button', { name: '修改數量', exact: true }).isEnabled() }, { payButtons: 0, amendmentEnabled: false });
});

for (const status of ['failed', 'expired']) test(`匹配${status}終態保留pending原單合法續付與改量`, async t => {
  const { page } = await fixture(t, { payable: true, attempt: { id: 'attempt-a', order_id: 'return-order', order_revision: 2, superseded: false, status } });
  await page.getByRole('button', { name: '前往線上付款', exact: true }).waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(node => node.textContent === '修改數量' && !node.disabled));
  await assertNotPaid(page);
  assert.equal(await page.getByRole('button', { name: '修改數量', exact: true }).isEnabled(), true);
});
