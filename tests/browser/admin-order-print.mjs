import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || !['http:', 'https:'].includes(target.protocol)
  || target.pathname !== '/' || target.username || target.password || target.search || target.hash) {
  throw new Error('Order-print acceptance must use an isolated local origin');
}
const base = target.origin;
const orderId = 'isolated-meal-order';
const printPath = `/v1/admin/printers/orders/${orderId}`;
const previewToken = 'a'.repeat(64);
const paymentLabels = { paid: '已付款', pending: '未付款', refunded: '已退款', refund_pending: '退款待處理', expired: '已逾期／取消' };
const orderFixture = {
  id: orderId, order_number: 'ISOLATED-MEAL-001', sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup',
  payment_method: 'online', payment_status: 'paid', fulfillment_status: 'pending', invoice_status: 'issued',
  paid_at: '2030-01-02T01:10:00Z', cancelled_at: null, created_at: '2030-01-02T01:00:00Z', amount_total: 259, tax_amount: 12,
  revision: 1, available_actions: [], meal_event_title: '隔離午餐', pickup_at: '2030-01-03T11:30:00+08:00',
  items: [{ id: 'isolated-item', product_name: '隔離蔬食便當', quantity: 2, unit_label: '份', unit_price: 129, subtotal: 258, tax_type: 'taxable' }],
};

async function fixture(t, { width = 390, role = 'admin', payment = 'paid', cancelled = false, channel = 'meal_preorder' } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const state = {
    order: { ...structuredClone(orderFixture), sales_channel: channel, payment_status: payment, paid_at: payment === 'pending' ? null : orderFixture.paid_at,
      fulfillment_status: cancelled ? 'cancelled' : 'pending', cancelled_at: cancelled ? '2030-01-02T02:00:00Z' : null,
      invoice_status: payment === 'refunded' ? 'voided' : 'issued' },
    previews: [], reads: [], writes: [], jobs: new Map(), lookups: [], unknown: [], external: [], errors: [],
    duplicateRisk: false, canPrint: true, blockedReason: null, mode: 'live', lostResponse: '', rejectPost: 0,
    holdSend: false, releaseSend: null, printedStatus: 'pending', previewToken,
  };
  const preview = () => {
    const order = state.order;
    const kind = order.payment_status === 'paid' && !order.cancelled_at && order.fulfillment_status !== 'cancelled' ? 'order' : 'reference';
    return { order_id: order.id, order_number: order.order_number, kind, revision: order.revision,
      payment_status: order.payment_status, fulfillment_status: order.fulfillment_status,
      preview: `十里方圓 合作社\n${kind === 'reference' ? '訂單紀錄單\n＊＊ 請勿製作 ＊＊' : '便當訂餐單'}\n＊＊ 補印 ＊＊\n訂單：${order.order_number}\n顧客：隔離測試同學\n電話：0900000000\n取餐：2030/01/03 11:30\n2 × 隔離蔬食便當\n  主食：紫米飯\n付款狀態：${paymentLabels[order.payment_status]}\n訂單狀態：${order.fulfillment_status === 'cancelled' ? '已取消' : '待確認'}\n總金額：NT$259`,
      preview_token: state.previewToken, mode: state.mode, duplicate_risk: state.duplicateRisk,
      can_print: state.canPrint, blocked_reason: state.blockedReason, jobs: [],
    };
  };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (url.origin === 'https://fonts.googleapis.com') return route.fulfill({ contentType: 'text/css', body: '' });
    if (url.origin !== base) { state.external.push(url.origin); return route.abort(); }
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({ status: role === 'guest' ? 401 : 200, json: role === 'guest' ? { detail: '隔離訪客' } : {
      access_token: 'order-print-isolated', user: { id: `isolated-${role}`, display_name: '隔離管理員', email: 'admin@example.test', user_role: role, membership_type: 'nonmember' },
    } });
    if (path === '/v1/platform' && method === 'GET') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/members/me' && method === 'GET') return route.fulfill({ json: { membership_type: 'nonmember', membership: null } });
    if (path === '/v1/notifications' && method === 'GET') return route.fulfill({ json: [] });
    if (path === '/v1/admin/order-search' && method === 'GET') {
      state.reads.push(path); return route.fulfill({ json: { items: [state.order], total: 1, offset: 0, limit: 12 } });
    }
    if (path === `${printPath}/preview` && method === 'GET') { state.previews.push(preview()); return route.fulfill({ json: preview() }); }
    if (path === `${printPath}/print` && method === 'POST') {
      const body = request.postDataJSON(); state.writes.push({ path, body });
      if (state.holdSend) await new Promise(resolve => { state.releaseSend = resolve; });
      if (state.rejectPost) return route.fulfill({ status: state.rejectPost, json: { detail: state.rejectPost === 409 ? '訂單或出單狀態已變更，請重新預覽後列印' : '登入或管理員權限已失效' } });
      if (state.duplicateRisk && !body.acknowledge_duplicate) return route.fulfill({ status: 409, json: { detail: '請先確認可重複列印' } });
      const lost = state.lostResponse; state.lostResponse = '';
      if (lost === 'before') return route.abort('failed');
      if (!state.jobs.has(body.request_id)) state.jobs.set(body.request_id, {
        id: `job-${body.request_id}`, order_id: orderId, order_number: state.order.order_number, kind: preview().kind,
        status: state.printedStatus, mode: state.mode, reprint_of_id: null, revision: state.order.revision,
        attempts: 0, last_error: null, can_query: false, pickup_at: '2030-01-03 11:30', created_at: '2030-01-02T02:00:00Z',
      });
      if (lost === 'after') return route.abort('failed');
      return route.fulfill({ status: 201, json: state.jobs.get(body.request_id) });
    }
    if (path.startsWith(`${printPath}/print-requests/`) && method === 'GET') {
      const id = path.slice(`${printPath}/print-requests/`.length); state.lookups.push(id);
      const job = state.jobs.get(id);
      return route.fulfill(job ? { json: job } : { status: 404, json: { detail: '找不到這筆送印' } });
    }
    state.unknown.push(`${method} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated order-print API' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  return { page, state };
}

async function openPreview(page) {
  await page.getByRole('button', { name: '再次出單', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '訂單出單／補印', exact: true });
  await dialog.locator('pre').waitFor();
  return dialog;
}

async function verifyIsolated(page, state) {
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.external, []); assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, '頁面不得橫向溢出');
  const dialog = page.getByRole('dialog', { name: '訂單出單／補印', exact: true });
  if (await dialog.count()) assert.equal(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1), true, '補印對話框不得橫向溢出');
}

for (const width of [390, 1280]) {
  for (const payment of ['paid', 'pending', 'refunded']) {
    test(`${width}px ${payment} 便當可預覽目前狀態並確認送印`, { timeout: 25000 }, async t => {
      const { page, state } = await fixture(t, { width, payment, cancelled: payment === 'refunded' });
      await page.goto(`${base}/admin?section=orders`);
      await page.getByRole('button', { name: '再次出單', exact: true }).waitFor();
      assert.deepEqual(state.previews, []); assert.deepEqual(state.writes, []);
      assert.equal(await page.getByRole('link', { name: '便當取餐與退單', exact: true }).count(), payment === 'refunded' ? 0 : 1);
      assert.equal(await page.locator('summary').filter({ hasText: /^查詢發票$/ }).count(), 1);
      assert.equal(await page.locator('summary').filter({ hasText: /^查詢付款$/ }).count(), 1);
      const dialog = await openPreview(page);
      const text = await dialog.locator('pre').innerText();
      assert.match(text, new RegExp(paymentLabels[payment]));
      assert.match(text, /隔離蔬食便當[\s\S]*主食：紫米飯/);
      if (payment === 'paid') assert.match(text, /便當訂餐單/);
      else assert.match(text, /訂單紀錄單[\s\S]*請勿製作/);
      const send = dialog.getByRole('button', { name: '確認送至出單機', exact: true });
      assert.equal(await send.isDisabled(), true);
      await dialog.getByLabel('出單原因', { exact: true }).fill('  顧客要求核對訂單  ');
      assert.equal(await send.isEnabled(), true);
      assert.deepEqual(state.writes, []);
      if (payment === 'refunded' && process.env.BROWSER_SCREENSHOT_DIR) {
        await mkdir(process.env.BROWSER_SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: join(process.env.BROWSER_SCREENSHOT_DIR, `admin-order-reference-${width}.png`) });
      }
      await send.click();
      await dialog.getByText('已建立出單工作：等待發送', { exact: true }).waitFor();
      assert.equal(state.writes.length, 1);
      assert.equal(state.writes[0].body.reason, '顧客要求核對訂單');
      assert.equal(state.writes[0].body.preview_token, previewToken);
      assert.match(state.writes[0].body.request_id, /^[0-9a-f-]{36}$/);
      assert.equal(state.writes[0].body.acknowledge_duplicate, false);
      assert.equal(await dialog.getByRole('button', { name: '再印一份', exact: true }).count(), 0);
      await verifyIsolated(page, state);
    });
  }
}

test('未知結果須確認紙本，正在送出的同一訂單不能另開送印', async t => {
  const { page, state } = await fixture(t);
  state.duplicateRisk = true;
  await page.goto(`${base}/admin?section=orders`);
  const dialog = await openPreview(page);
  await dialog.getByLabel('出單原因', { exact: true }).fill('核對後需要補印');
  const send = dialog.getByRole('button', { name: '確認送至出單機', exact: true });
  assert.equal(await send.isDisabled(), true);
  await dialog.getByLabel('已核對紙本，接受可能重複列印', { exact: true }).check();
  state.holdSend = true;
  await send.click();
  await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).isDisabled(), true);
  assert.equal(state.writes.length, 1); assert.equal(state.writes[0].body.acknowledge_duplicate, true);
  state.releaseSend();
  await dialog.getByText('已建立出單工作：等待發送', { exact: true }).waitFor();
  assert.equal(state.writes.length, 1);
  await verifyIsolated(page, state);
});

test('已存在待送工作顯示阻擋原因，不送出新的列印', async t => {
  const { page, state } = await fixture(t);
  state.canPrint = false; state.blockedReason = '這筆訂單已有等待發送或正在發送的出單工作';
  await page.goto(`${base}/admin?section=orders`);
  const dialog = await openPreview(page);
  await dialog.getByText(state.blockedReason, { exact: true }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: '確認送至出單機', exact: true }).isDisabled(), true);
  assert.deepEqual(state.writes, []);
  await verifyIsolated(page, state);
});

test('模擬模式明確區分，不顯示正式送印按鈕', async t => {
  const { page, state } = await fixture(t);
  state.mode = 'dry_run'; state.printedStatus = 'simulated';
  await page.goto(`${base}/admin?section=orders`);
  const dialog = await openPreview(page);
  await dialog.getByText('目前為模擬模式，不會送到出單機。', { exact: true }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: '確認送至出單機', exact: true }).count(), 0);
  await dialog.getByLabel('出單原因', { exact: true }).fill('隔離模擬驗收');
  await dialog.getByRole('button', { name: '確認模擬出單', exact: true }).click();
  await dialog.getByText('已建立出單工作：模擬完成', { exact: true }).waitFor();
  assert.equal(state.writes.length, 1);
  await verifyIsolated(page, state);
});

for (const lost of ['before', 'after']) {
  test(`送印回應遺失 ${lost} 後重整仍沿用同一識別碼`, { timeout: 25000 }, async t => {
    const { page, state } = await fixture(t);
    state.lostResponse = lost;
    await page.goto(`${base}/admin?section=orders`);
    let dialog = await openPreview(page);
    await dialog.getByLabel('出單原因', { exact: true }).fill('紙本重新核對');
    await dialog.getByRole('button', { name: '確認送至出單機', exact: true }).click();
    await dialog.getByRole('alert').waitFor();
    const first = structuredClone(state.writes[0].body);
    assert.equal(state.writes.length, 1);
    await page.reload();
    await page.getByRole('button', { name: /再次出單|查看送印結果/ }).click();
    dialog = page.getByRole('dialog', { name: '訂單出單／補印', exact: true });
    if (lost === 'after') {
      await dialog.getByText('已建立出單工作：等待發送', { exact: true }).waitFor();
      assert.equal(state.writes.length, 1, '重新開啟必須查詢既有工作，不可自動補送');
      assert.equal(state.jobs.size, 1);
    } else {
      await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).click();
      await dialog.getByText('已建立出單工作：等待發送', { exact: true }).waitFor();
      assert.equal(state.writes.length, 2);
      assert.deepEqual(state.writes[1].body, first);
      assert.equal(state.jobs.size, 1);
    }
    assert.equal(state.lookups.at(-1), first.request_id);
    await verifyIsolated(page, state);
  });
}

test('伺服器拒絕過期預覽後不得沿用舊快照送印', async t => {
  const { page, state } = await fixture(t);
  state.rejectPost = 409;
  await page.goto(`${base}/admin?section=orders`);
  const dialog = await openPreview(page);
  await dialog.getByLabel('出單原因', { exact: true }).fill('核對最新訂單');
  await dialog.getByRole('button', { name: '確認送至出單機', exact: true }).click();
  await dialog.getByRole('alert').getByText('訂單或出單狀態已變更，請重新預覽後列印', { exact: true }).waitFor();
  assert.equal(state.jobs.size, 0);
  assert.equal(await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).count(), 0);
  assert.equal(await dialog.getByRole('button', { name: '確認送至出單機', exact: true }).count(), 0);
  await verifyIsolated(page, state);
});

test('送出時權限失效不自動改識別碼重送，保留原工作供核對', async t => {
  const { page, state } = await fixture(t);
  state.rejectPost = 403;
  await page.goto(`${base}/admin?section=orders`);
  let dialog = await openPreview(page);
  await dialog.getByLabel('出單原因', { exact: true }).fill('權限到期前預覽');
  await dialog.getByRole('button', { name: '確認送至出單機', exact: true }).click();
  await dialog.getByRole('alert').getByText('登入或管理員權限已失效', { exact: true }).waitFor();
  const first = structuredClone(state.writes[0].body);
  await page.reload();
  await page.getByRole('button', { name: '再次出單', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '訂單出單／補印', exact: true });
  await dialog.getByRole('alert').waitFor();
  assert.equal(state.writes.length, 1);
  assert.equal(state.lookups.at(-1), first.request_id);
  state.rejectPost = 0;
  await dialog.getByRole('button', { name: '重試同一筆送印', exact: true }).click();
  await dialog.getByText('已建立出單工作：等待發送', { exact: true }).waitFor();
  assert.deepEqual(state.writes[1].body, first);
  assert.equal(state.jobs.size, 1);
  await verifyIsolated(page, state);
});

test('已確認紙本後才能主動再印一份，使用新的識別碼', async t => {
  const { page, state } = await fixture(t);
  state.printedStatus = 'printed';
  await page.goto(`${base}/admin?section=orders`);
  const dialog = await openPreview(page);
  await dialog.getByLabel('出單原因', { exact: true }).fill('第一份補印');
  await dialog.getByRole('button', { name: '確認送至出單機', exact: true }).click();
  await dialog.getByText('已建立出單工作：已確認列印', { exact: true }).waitFor();
  const another = dialog.getByRole('button', { name: '再印一份', exact: true });
  assert.equal(await another.isDisabled(), true);
  await dialog.getByLabel('已核對紙本，需要再印一份', { exact: true }).check();
  await another.click();
  await dialog.locator('pre').waitFor();
  await dialog.getByLabel('出單原因', { exact: true }).fill('另存一份紀錄');
  await dialog.getByRole('button', { name: '確認送至出單機', exact: true }).click();
  await dialog.getByText('已建立出單工作：已確認列印', { exact: true }).waitFor();
  assert.equal(state.jobs.size, 2);
  assert.notEqual(state.writes[0].body.request_id, state.writes[1].body.request_id);
  await verifyIsolated(page, state);
});

for (const role of ['customer', 'guest']) {
  test(`${role} 不能看到出單入口或讀取訂單列印資料`, async t => {
    const { page, state } = await fixture(t, { role });
    await page.goto(`${base}/admin?section=orders`);
    await page.getByRole('heading', { name: '管理工作台', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '再次出單', exact: true }).count(), 0);
    assert.deepEqual(state.reads, []); assert.deepEqual(state.previews, []); assert.deepEqual(state.writes, []);
    await verifyIsolated(page, state);
  });
}

test('一般商品訂單不顯示便當補印入口', async t => {
  const { page, state } = await fixture(t, { channel: 'regular' });
  await page.goto(`${base}/admin?section=orders`);
  await page.getByText(orderFixture.order_number, { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '再次出單', exact: true }).count(), 0);
  assert.deepEqual(state.previews, []); assert.deepEqual(state.writes, []);
  await verifyIsolated(page, state);
});
