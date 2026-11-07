import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use isolated local fixtures only');
const base = target.origin;
const itemA = { id: 'item-a', product_id: 'product-a', product_name: '隔離白米', quantity: 1, unit_label: '袋', unit_price: 120, subtotal: 120, tax_type: 'taxable' };
const itemB = { id: 'item-b', product_id: 'product-b', product_name: '隔離紅豆', quantity: 2, unit_label: '袋', unit_price: 50, subtotal: 100, tax_type: 'taxable' };
const regular = { id: 'isolated-order', order_number: 'ISOLATED-QUANTITY', order_kind: 'regular', revision: 1, amendments: [], amendment_blocked_reason: null, sales_channel: 'regular', fulfillment_method: 'cooperative_pickup', payment_method: 'online', payment_status: 'pending', invoice_status: 'not_eligible', fulfillment_status: 'pending_confirmation', amount_total: 250, tax_amount: 12, created_at: '2030-01-02T01:00:00Z', available_actions: ['pay', 'amend_quantities'], items: [itemA, itemB], shipment: { channel: 'home_delivery', temperature: 'ambient', status: 'ready_to_create', shipping_fee: 30 }, fulfillment: { method: 'cooperative_pickup', status: 'pending_confirmation', pickup_location: '隔離取貨點' } };
const meal = { ...regular, sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup', shipment: null, amount_total: 200, meal_event_id: 'event', meal_event_title: '隔離午餐', venue_name: '合作社', pickup_start: '2030-01-03T11:00:00+08:00', pickup_end: '2030-01-03T13:30:00+08:00', pickup_at: '2030-01-03T11:30:00+08:00', needs_utensils: true, promotion: { name: '隔離滿額優惠', discount: 20, original_amount: 220 }, items: [itemA, itemB].map((item, index) => ({ order_item_id: item.id, offering_id: `offering-${index}`, meal_id: `meal-${index}`, meal_name: item.product_name, quantity: item.quantity, base_price: item.unit_price, option_price: 0, unit_price: item.unit_price, subtotal: item.subtotal, tax_type: 'taxable', selections: [{ group_name: '主食', option_name: '紫米飯' }] })) };

function itemId(item) { return item.id || item.order_item_id; }
function orderResponse(order) {
  return { ...order, items: order.items.map(item => ({ id: itemId(item), product_name: item.product_name || item.meal_name, quantity: item.quantity, unit_label: item.unit_label || '份', unit_price: item.unit_price, subtotal: item.subtotal, tax_type: item.tax_type })) };
}
function price(order, input) {
  const items = input.items.map(line => { const item = order.items.find(item => itemId(item) === line.order_item_id); return { order_item_id: line.order_item_id, product_name: item.product_name || item.meal_name, unit_label: item.unit_label || '份', previous_quantity: item.quantity, quantity: line.quantity, unit_price: item.unit_price, subtotal: item.unit_price * line.quantity }; });
  const subtotal = items.reduce((sum, item) => sum + item.subtotal, 0);
  const shipping = order.sales_channel === 'regular' && subtotal < 500 ? 30 : 0;
  const discount = order.sales_channel === 'meal_preorder' && subtotal >= 200 ? 20 : 0;
  return { order_id: order.id, expected_revision: input.expected_revision, previous_total: order.amount_total, amount_total: subtotal + shipping - discount, tax_amount: Math.round((subtotal - discount) / 21), shipping_fee: shipping, discount_amount: discount, items, promotion: discount ? { name: '隔離滿額優惠', discount, original_amount: subtotal } : null };
}

async function fixture(t, { channel = 'regular', role = 'customer', width = 1280, paymentMethod = 'online', path = '/orders' } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 950 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block', reducedMotion: 'reduce' });
  await context.addInitScript(() => localStorage.setItem('slf_cart', JSON.stringify([{ product: { id: 'separate-cart-product', name: '尚未下單商品', unit: '瓶', member_price: 10, nonmember_price: 10, stock_quantity: 10 }, quantity: 4 }])));
  const state = { order: { ...structuredClone(channel === 'meal_preorder' ? meal : regular), sales_channel: channel, payment_method: paymentMethod }, reads: [], quotes: [], writes: [], errors: [], unknown: [], quoteFailure: null, confirmFailure: null, savedResponses: new Map(), lostResponse: false, quoteDelay: 0, confirmDelay: 0 };
  if (channel === 'group') { state.order.amount_total = 220; state.order.shipment = null; }
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), endpoint = url.pathname, method = request.method();
    if (url.origin !== base) return route.abort();
    if (!endpoint.startsWith('/v1/')) return method === 'GET' ? route.continue() : route.abort();
    if (endpoint === '/v1/auth/refresh') return route.fulfill({ status: role === 'guest' ? 401 : 200, json: role === 'guest' ? { detail: '隔離訪客' } : { access_token: 'isolated', user: { id: 'isolated-user', email: 'isolated@example.test', display_name: '隔離驗收帳號', user_role: role, membership_type: 'nonmember' } } });
    if (method === 'GET') state.reads.push(endpoint);
    if (endpoint === '/v1/platform') return route.fulfill({ json: { orders_open: true, support_phone: '' } });
    if (endpoint === '/v1/orders') return route.fulfill({ json: channel === 'meal_preorder' ? [] : [orderResponse(state.order)] });
    if (endpoint === `/v1/orders/${state.order.id}`) return route.fulfill({ json: orderResponse(state.order) });
    if (endpoint === '/v1/meal-orders') return route.fulfill({ json: channel === 'meal_preorder' ? [state.order] : [] });
    if (endpoint === `/v1/meal-orders/${state.order.id}`) return route.fulfill({ json: state.order });
    if (endpoint === '/v1/admin/finance/meal-sales') {
      const detail = { ...state.order, customer_name: '隔離買家', customer_email: 'buyer@example.test', membership_type: 'nonmember', order_amount: state.order.amount_total,
        period_received: 490, period_refunded: 250, period_net: 240, paid_at: '2030-01-02T02:00:00Z', pickup_time_confirmed: false,
        items: orderResponse(state.order).items.map(item => ({ ...item, name: item.product_name, unit: item.unit_label, options: [] })),
        receipts: [
          { merchant_trade_no: 'ISOLATED-OLD-RECEIPT', order_revision: 1, amount: 250, status: 'refunded', received_at: null, obsolete_revision: true },
          { merchant_trade_no: 'ISOLATED-CURRENT-RECEIPT', order_revision: 2, amount: 490, status: 'paid', received_at: '2030-01-02T02:00:00Z', obsolete_revision: false },
        ], refunds: [{ amount: 250, status: 'completed', reason: '舊付款頁款項', requested_at: '2030-01-02T03:00:00Z', completed_at: '2030-01-02T03:10:00Z', purpose: 'obsolete_order_revision', payment_attempt_id: 'isolated-attempt' }],
      };
      return route.fulfill({ json: { starts_on: '2030-01-01', ends_on: '2030-01-02', summary: { received: 490, refunded: 250, net: 240, receipt_count: 1, refund_count: 1, pending_refund_amount: 0, paid_meal_quantity: 5 }, daily: [], weekly: [], monthly: [], products: [], orders: { items: [detail], total: 1, limit: 20, offset: 0 } } });
    }
    if (endpoint === '/v1/admin/order-search') return route.fulfill({ json: { items: [orderResponse(state.order)], total: 1, offset: 0, limit: 12 } });
    if (endpoint === '/v1/admin/meal-orders') return route.fulfill({ json: { items: [{ order: state.order, customer_name: '隔離買家', customer_email: 'isolated@example.test', meal_period: 'lunch' }], total: 1, offset: 0, limit: 20 } });
    if (endpoint === '/v1/admin/meal-operations-summary') return route.fulfill({ json: { pending_cancellation: 0, refund_attention: 0, invoice_attention: 0, today_pickup: 0 } });
    if (['/v1/admin/meals', '/v1/admin/meal-events', '/v1/products', '/v1/activities', '/v1/notifications'].includes(endpoint)) return route.fulfill({ json: [] });
    if (endpoint === '/v1/members/me') return route.fulfill({ json: { membership_type: 'nonmember', membership: null } });
    if (endpoint === `/v1/orders/${state.order.id}/quantity-amendments/quote`) {
      const body = request.postDataJSON(); state.quotes.push(body);
      if (state.quoteDelay) await new Promise(resolve => setTimeout(resolve, state.quoteDelay));
      if (state.quoteFailure) return route.fulfill({ status: state.quoteFailure.status, json: { detail: state.quoteFailure.message } });
      if (body.expected_revision !== state.order.revision) return route.fulfill({ status: 409, json: { detail: '隔離訂單版本已更新' } });
      return route.fulfill({ json: price(state.order, body) });
    }
    if (endpoint === `/v1/orders/${state.order.id}/quantity-amendments`) {
      const body = request.postDataJSON(); state.writes.push(body);
      if (state.confirmDelay) await new Promise(resolve => setTimeout(resolve, state.confirmDelay));
      if (state.savedResponses.has(body.client_request_id)) return route.fulfill({ json: state.savedResponses.get(body.client_request_id) });
      if (state.confirmFailure) return route.fulfill({ status: state.confirmFailure.status, json: { detail: state.confirmFailure.message } });
      if (body.expected_revision !== state.order.revision) return route.fulfill({ status: 409, json: { detail: '隔離訂單版本已更新' } });
      const quote = price(state.order, body); assert.equal(body.expected_total, quote.amount_total);
      const before = structuredClone(orderResponse(state.order));
      state.order = { ...state.order, revision: state.order.revision + 1, amount_total: quote.amount_total, tax_amount: quote.tax_amount, promotion: quote.promotion,
        items: state.order.items.flatMap(item => { const next = body.items.find(line => line.order_item_id === itemId(item)); return next.quantity === 0 ? [] : [{ ...item, quantity: next.quantity, subtotal: next.quantity * item.unit_price }]; }) };
      state.order.amendments.push({ id: `amendment-${state.order.revision}`, previous_revision: before.revision, revision: state.order.revision, created_at: '2030-01-02T02:00:00Z', before_snapshot: { amount_total: before.amount_total, items: before.items }, after_snapshot: { amount_total: state.order.amount_total, items: orderResponse(state.order).items } });
      const result = orderResponse(state.order); state.savedResponses.set(body.client_request_id, result);
      if (state.lostResponse) { state.lostResponse = false; return route.fulfill({ status: 503, json: { detail: '隔離回應逾時' } }); }
      return route.fulfill({ json: result });
    }
    state.unknown.push(`${method} ${endpoint}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.clock.install({ time: new Date('2030-01-02T10:00:00+08:00') });
  const visit = async () => { await page.goto(`${base}${path}`); if (path === '/orders' && channel !== 'meal_preorder') await page.getByRole('tab', { name: '一般訂單', exact: true }).click(); };
  return { page, state, visit };
}
async function openEditor(page) {
  await page.getByRole('button', { name: '修改數量', exact: true }).click();
  return page.getByRole('dialog');
}
async function preview(page, dialog) { await dialog.getByRole('button', { name: '查看修改後金額', exact: true }).click(); await dialog.getByRole('button', { name: '確認修改', exact: true }).waitFor(); }
async function assertClean(page, state) { assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, '不可橫向溢出'); }

for (const channel of ['regular', 'group', 'meal_preorder']) for (const width of [1280, 390]) {
  test(`${channel} ${width}px 草稿、預覽與確認分離，更新列表明細及修改紀錄`, async t => {
    const { page, state, visit } = await fixture(t, { channel, width }); await visit();
    const dialog = await openEditor(page);
    assert.equal(await dialog.getByRole('button', { name: '查看修改後金額' }).isDisabled(), true);
    await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('3');
    assert.deepEqual(state.quotes, []); assert.deepEqual(state.writes, []);
    await preview(page, dialog);
    assert.equal(state.quotes.length, 1); assert.deepEqual(state.writes, []);
    const expected = channel === 'regular' ? 490 : channel === 'group' ? 460 : 440;
    assert.match(await dialog.locator('.quantity-amendment-total').innerText(), new RegExp(String(expected)));
    assert.match(await dialog.locator('.quantity-amendment-preview-line').first().innerText(), /1 → 3/);
    assert.ok((await dialog.getByRole('button', { name: '確認修改', exact: true }).boundingBox()).height < 100, '手機按鈕不可被共用flex樣式拉成過高區塊');
    if (process.env.BROWSER_ARTIFACT_DIR && ((channel === 'regular' && width === 390) || (channel === 'meal_preorder' && width === 1280))) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/quantity-amendment-${channel}-${width}.png` });
    await dialog.getByRole('button', { name: '確認修改', exact: true }).click();
    await page.locator('.order-quantity-amendment [role="status"]').waitFor();
    assert.equal(await page.getByRole('dialog').count(), 0);
    assert.equal(state.writes.length, 1); assert.equal(state.writes[0].expected_revision, 1); assert.equal(state.writes[0].expected_total, expected);
    assert.match(state.writes[0].client_request_id, /^[0-9a-f-]{36}$/);
    assert.equal(state.order.id, 'isolated-order'); assert.equal(state.order.revision, 2);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))[0].quantity), 4, '已成立訂單改量不可改動未下單購物車');
    assert.match(await page.locator('.order-detail-head').innerText(), new RegExp(String(expected)));
    assert.match(await page.locator('.order-lines').innerText(), /3/);
    await page.getByText('數量修改紀錄（1 次）', { exact: true }).click();
    assert.match(await page.locator('.quantity-amendment-history').innerText(), /第 2 版/);
    if (channel === 'meal_preorder') assert.match(await page.locator('.order-lines').innerText(), /主食：紫米飯/);
    await assertClean(page, state);
  });
}

for (const route of ['/admin', '/admin/meals']) {
  test(`${route} 管理員現場便當以相同流程改量`, async t => {
    const { page, state, visit } = await fixture(t, { channel: 'meal_preorder', role: 'admin', width: 390, paymentMethod: 'cash_on_pickup', path: route }); await visit();
    const dialog = await openEditor(page); await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('2'); await preview(page, dialog);
    await dialog.getByRole('button', { name: '確認修改', exact: true }).click(); await page.locator('.order-quantity-amendment [role="status"]').waitFor();
    assert.equal(state.order.payment_method, 'cash_on_pickup'); assert.equal(state.order.amount_total, 320); assert.equal(state.writes.length, 1);
    await assertClean(page, state);
  });
}

test('已付款或未授權不可顯示修改入口，舊版回應缺少識別資料也不開放', async t => {
  const { page, state, visit } = await fixture(t);
  for (const update of [{ payment_status: 'paid', available_actions: [] }, { payment_status: 'pending', available_actions: ['pay'] }, { available_actions: ['amend_quantities'], revision: undefined }]) {
    Object.assign(state.order, update); await visit(); await page.locator('.order-detail-head').waitFor();
    assert.equal(await page.getByRole('button', { name: '修改數量', exact: true }).count(), 0);
  }
  assert.deepEqual(state.quotes, []); assert.deepEqual(state.writes, []); await assertClean(page, state);
});

test('取消或 Escape 只捨棄草稿，不能送出改量', async t => {
  const { page, state, visit } = await fixture(t); await visit(); let dialog = await openEditor(page);
  await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('3'); await dialog.getByRole('button', { name: '取消修改', exact: true }).click();
  dialog = await openEditor(page); assert.equal(await dialog.getByLabel('隔離白米數量', { exact: true }).inputValue(), '1');
  await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('3'); await preview(page, dialog); await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('dialog').count(), 0); assert.deepEqual(state.writes, []); assert.equal(state.order.items[0].quantity, 1); await assertClean(page, state);
});

for (const channel of ['regular', 'meal_preorder']) {
  test(`${channel} 自訂數量整數上限及全單移除驗證，不送出無效草稿`, async t => {
    const { page, state, visit } = await fixture(t, { channel, width: 390 }); await visit(); const dialog = await openEditor(page);
    await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('custom'); const custom = dialog.getByLabel('隔離白米自訂數量', { exact: true });
    for (const quantity of ['-1', '1.5', '', channel === 'regular' ? '1000' : '100']) { await custom.fill(quantity); assert.equal(await dialog.getByRole('button', { name: '查看修改後金額' }).isDisabled(), true); }
    await custom.fill(channel === 'regular' ? '999' : '99'); assert.equal(await dialog.getByRole('button', { name: '查看修改後金額' }).isEnabled(), true);
    await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('0'); await dialog.getByLabel('隔離紅豆數量', { exact: true }).selectOption('0');
    assert.equal(await dialog.getByRole('button', { name: '查看修改後金額' }).isDisabled(), true); assert.match(await dialog.getByRole('alert').innerText(), /至少保留一項/);
    await dialog.getByLabel('隔離紅豆數量', { exact: true }).selectOption('1'); await preview(page, dialog);
    assert.match(await dialog.locator('.quantity-amendment-preview-line').first().innerText(), /移除/);
    await dialog.getByRole('button', { name: '確認修改', exact: true }).click(); await page.locator('.order-quantity-amendment [role="status"]').waitFor();
    assert.equal(state.order.items.length, 1); assert.equal(itemId(state.order.items[0]), 'item-b'); await assertClean(page, state);
  });
}

test('報價失敗保留草稿；修改預覽返回後須重新計算', async t => {
  const { page, state, visit } = await fixture(t); state.quoteFailure = { status: 503, message: '隔離報價暫時失敗' }; await visit(); const dialog = await openEditor(page);
  await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('3'); await dialog.getByRole('button', { name: '查看修改後金額' }).click(); await dialog.getByRole('alert').waitFor();
  assert.equal(await dialog.getByLabel('隔離白米數量', { exact: true }).inputValue(), '3'); assert.deepEqual(state.writes, []);
  state.quoteFailure = null; await preview(page, dialog); await dialog.getByRole('button', { name: '返回調整', exact: true }).click();
  await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('4'); assert.equal(await dialog.getByRole('button', { name: '確認修改', exact: true }).count(), 0);
  await preview(page, dialog); assert.equal(state.quotes.length, 3); assert.equal(state.quotes[2].items[0].quantity, 4); await assertClean(page, state);
});

for (const channel of ['regular', 'meal_preorder']) {
  test(`${channel} 確認衝突保留草稿，重新查版本及預覽後才能確認`, async t => {
    const { page, state, visit } = await fixture(t, { channel }); await visit(); const dialog = await openEditor(page);
    await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('3'); await preview(page, dialog);
    state.order.revision = 2; state.order.items[0].quantity = 2;
    await dialog.getByRole('button', { name: '確認修改', exact: true }).click(); await dialog.getByRole('button', { name: '重新核對訂單', exact: true }).waitFor();
    assert.equal(await dialog.getByLabel('隔離白米數量', { exact: true }).inputValue(), '3'); assert.equal(await dialog.getByRole('button', { name: '查看修改後金額' }).isDisabled(), true);
    assert.equal(state.writes.length, 1); await dialog.getByRole('button', { name: '重新核對訂單', exact: true }).click();
    await page.waitForFunction(() => [...document.querySelectorAll('[role="dialog"] button')].some(button => button.textContent === '查看修改後金額' && !button.disabled));
    assert.equal(await dialog.getByLabel('隔離白米數量', { exact: true }).inputValue(), '3');
    if (channel === 'meal_preorder') assert.match(await dialog.innerText(), /主食：紫米飯/);
    await preview(page, dialog); assert.equal(state.quotes.at(-1).expected_revision, 2); assert.match(await dialog.locator('.quantity-amendment-preview-line').first().innerText(), /2 → 3/);
    await dialog.getByRole('button', { name: '確認修改', exact: true }).click(); await page.locator('.order-quantity-amendment [role="status"]').waitFor();
    assert.equal(state.order.revision, 3); assert.notEqual(state.writes[0].client_request_id, state.writes[1].client_request_id); await assertClean(page, state);
  });
}

test('確認回應遺失後重試沿用冪等識別，不重複修改訂單', async t => {
  const { page, state, visit } = await fixture(t); state.lostResponse = true; await visit(); const dialog = await openEditor(page);
  await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('2'); await preview(page, dialog);
  await dialog.getByRole('button', { name: '確認修改', exact: true }).click(); await dialog.getByRole('alert').waitFor();
  assert.match(await dialog.getByRole('alert').innerText(), /回應逾時/); assert.equal(state.order.revision, 2);
  await dialog.getByRole('button', { name: '確認修改', exact: true }).click(); await page.locator('.order-quantity-amendment [role="status"]').waitFor();
  assert.equal(state.writes.length, 2); assert.equal(state.writes[0].client_request_id, state.writes[1].client_request_id); assert.equal(state.order.revision, 2); assert.equal(state.order.amendments.length, 1); await assertClean(page, state);
});

test('送出中鎖住表單及關閉操作，快速重複確認只送一次', async t => {
  const { page, state, visit } = await fixture(t); state.quoteDelay = 350; state.confirmDelay = 500; await visit(); const dialog = await openEditor(page);
  await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('2'); await dialog.getByRole('button', { name: '查看修改後金額', exact: true }).click();
  await dialog.getByRole('button', { name: '重新計算中…', exact: true }).waitFor(); assert.equal(await dialog.getByLabel('隔離白米數量', { exact: true }).isDisabled(), true); await page.keyboard.press('Escape'); assert.equal(await page.getByRole('dialog').count(), 1);
  await dialog.getByRole('button', { name: '確認修改', exact: true }).waitFor();
  await dialog.getByRole('button', { name: '確認修改', exact: true }).evaluate(button => { button.click(); button.click(); button.click(); });
  await dialog.getByRole('button', { name: '儲存修改中…', exact: true }).waitFor(); assert.equal(await dialog.getByRole('button', { name: '返回調整', exact: true }).isDisabled(), true); await page.keyboard.press('Escape'); assert.equal(await page.getByRole('dialog').count(), 1);
  await page.locator('.order-quantity-amendment [role="status"]').waitFor(); assert.equal(state.writes.length, 1); await assertClean(page, state);
});

test('預覽中背景變更為已付款，禁止確認，重新核對顯示阻擋原因', async t => {
  const { page, state, visit } = await fixture(t); await visit(); const dialog = await openEditor(page);
  await dialog.getByLabel('隔離白米數量', { exact: true }).selectOption('3'); await preview(page, dialog);
  state.order.payment_status = 'paid'; state.order.available_actions = []; state.order.amendment_blocked_reason = '已付款訂單須由管理員整單退款後重新下單';
  await page.clock.fastForward(5500);
  await dialog.getByRole('button', { name: '重新核對訂單', exact: true }).waitFor(); assert.equal(await dialog.getByRole('button', { name: '確認修改', exact: true }).isDisabled(), true);
  await dialog.getByRole('button', { name: '重新核對訂單', exact: true }).click(); await dialog.getByRole('alert').waitFor();
  assert.match(await dialog.getByRole('alert').innerText(), /已付款/); assert.deepEqual(state.writes, []); await assertClean(page, state);
});

for (const width of [1280, 390]) {
  test(`${width}px 財務明細區分舊版款項及新版訂單，未知收款日期不借用新版日期`, async t => {
    const { page, state, visit } = await fixture(t, { width, role: 'admin', path: '/admin/finance' });
    state.order.revision = 2; state.order.payment_status = 'paid'; state.order.amount_total = 490;
    await visit(); await page.locator('.meal-finance-sale > summary').click();
    assert.match(await page.locator('.meal-finance-order-meta').innerText(), /目前訂單版本\s*第 2 版/);
    await page.getByText('查看收款交易（2 筆）', { exact: true }).click();
    const old = page.locator('.meal-finance-receipts li').filter({ hasText: 'ISOLATED-OLD-RECEIPT' });
    assert.match(await old.innerText(), /第 1 版 · 舊版付款/); assert.match(await old.innerText(), /\$250/); assert.match(await old.innerText(), /收款日期待核對/); assert.doesNotMatch(await old.innerText(), /2030/);
    const current = page.locator('.meal-finance-receipts li').filter({ hasText: 'ISOLATED-CURRENT-RECEIPT' });
    assert.match(await current.innerText(), /第 2 版/); assert.match(await current.innerText(), /\$490/); assert.match(await current.innerText(), /2030/); assert.doesNotMatch(await current.innerText(), /舊版付款/);
    await page.getByText('查看退款紀錄（1 筆）', { exact: true }).click(); assert.match(await page.locator('.meal-finance-refunds').innerText(), /舊版付款退款/);
    assert.deepEqual(state.writes, []); await assertClean(page, state);
  });
}

test('手機放大字級仍可鍵盤預覽與返回，不溢出也不提交訂單', async t => {
  const { page, state, visit } = await fixture(t, { width: 390 }); await visit();
  await page.locator('.reading-menu > summary').click(); await page.getByRole('group', { name: '文字大小', exact: true }).getByRole('button', { name: '放大', exact: true }).click(); await page.locator('.reading-menu > summary').click();
  const trigger = page.getByRole('button', { name: '修改數量', exact: true }); await trigger.focus(); await trigger.press('Enter');
  const dialog = page.getByRole('dialog', { name: '修改訂單數量', exact: true }); const select = dialog.getByLabel('隔離白米數量', { exact: true });
  await select.selectOption('3');
  const show = dialog.getByRole('button', { name: '查看修改後金額', exact: true }); await show.focus(); await show.press('Enter');
  await page.getByRole('dialog', { name: '確認數量與金額', exact: true }).waitFor();
  assert.equal(await page.getByRole('dialog').evaluate(node => { const rect = node.getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth && rect.height <= innerHeight; }), true);
  await page.keyboard.press('Escape'); await trigger.waitFor();
  await page.waitForFunction(node => document.activeElement === node, await trigger.elementHandle());
  assert.equal(await trigger.evaluate(node => document.activeElement === node), true);
  assert.deepEqual(state.writes, []); assert.equal(state.quotes.length, 1); await assertClean(page, state);
});


test('共同購買未付款訂單可依額滿時後端授權減量，保持付款版本流程', async t => {
  const {page,state,visit}=await fixture(t,{channel:'group',width:390});state.order.group_campaign_deadline='2030-01-03T02:00:00Z';await visit();const dialog=await openEditor(page);await dialog.getByLabel('隔離紅豆數量',{exact:true}).selectOption('1');await preview(page,dialog);assert.deepEqual(state.writes,[]);await dialog.getByRole('button',{name:'確認修改',exact:true}).click();await page.locator('.order-quantity-amendment [role="status"]').waitFor();assert.equal(state.order.items[1].quantity,1);assert.equal(state.order.revision,2);assert.equal(state.writes[0].expected_revision,1);await assertClean(page,state);
});
test('共同購買預覽開著到截止時禁止確認改量及新付款，不沿用快取action', async t => {
  const {page,state,visit}=await fixture(t,{channel:'group',width:390});state.order.group_campaign_deadline='2030-01-02T02:00:15Z';await visit();const dialog=await openEditor(page);await dialog.getByLabel('隔離紅豆數量',{exact:true}).selectOption('1');await preview(page,dialog);await page.clock.runFor(16000);assert.equal(await dialog.getByRole('button',{name:'確認修改',exact:true}).isDisabled(),true);assert.deepEqual(state.writes,[]);await page.keyboard.press('Escape');assert.equal(await page.getByRole('button',{name:'修改數量',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'前往線上付款',exact:true}).count(),0);await assertClean(page,state);
});
