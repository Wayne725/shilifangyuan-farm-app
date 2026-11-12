import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test, { after, before } from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
let browser;
before(async () => {
  browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
});
after(() => browser?.close());
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;
const regular = {
  id: 'regular-order', order_number: 'ISOLATED-REGULAR', sales_channel: 'regular', fulfillment_method: 'coop_pickup',
  payment_method: 'online', payment_status: 'paid', invoice_status: 'issued', fulfillment_status: 'pending_confirmation',
  amount_total: 120, tax_amount: 6, created_at: '2030-01-02T01:00:00Z', available_actions: ['cancel', 'refund'],
  items: [{ id: 'item', product_name: '隔離白米', quantity: 1, unit_label: '袋', unit_price: 120, subtotal: 120, tax_type: 'taxable' }],
};
const meal = {
  ...regular, id: 'meal-order', order_number: 'ISOLATED-MEAL', sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup',
  payment_method: 'cash_on_pickup', invoice_status: 'external', fulfillment_status: 'pending',
  meal_event_id: 'event', meal_event_title: '隔離午餐', venue_name: '合作社', pickup_start: '2030-01-03T11:00:00+08:00',
  pickup_end: '2030-01-03T13:30:00+08:00', pickup_at: '2030-01-03T11:30:00+08:00',
  items: [{ offering_id: 'offering', meal_name: '隔離蔬食便當', quantity: 1, unit_price: 120, subtotal: 120, tax_type: 'taxable', selections: [] }],
};

for (const channel of ['regular', 'meal_preorder']) {
  for (const width of [390, 1280]) {
    test(`${channel} ${width}px 原因留空的退單紀錄顯示處理人與時間`, async t => {
      const { page, state } = await fixture(t, { channel, width });
      state.order = { ...state.order, available_actions: [], cancelled_at: '2030-01-02T02:30:00Z', payment_status: 'refund_pending', fulfillment_status: 'cancelled',
        cancellation_request: { id: 'approved-request', order_id: state.order.id, status: 'approved', reason: '', review_reason: '', requested_at: '2030-01-02T02:00:00Z', reviewed_by_id: 'admin', reviewed_by_name: '隔離核對員', reviewed_at: '2030-01-02T02:30:00Z' } };
      if (channel === 'regular') state.order.shipment = { id: 'shipment', channel: 'home_delivery', temperature: 'ambient', status: 'in_transit', shipping_fee: 0 };
      await page.goto(`${base}/admin?section=orders`);
      const record = page.getByRole('region', { name: '退單審核', exact: true });
      await record.getByRole('heading', { name: '退單已核准', exact: true }).waitFor();
      await record.getByText('處理人：隔離核對員', { exact: true }).waitFor();
      assert.match(await record.textContent(), /處理時間：/);
      assert.doesNotMatch(await record.textContent(), /未記錄/);
      assert.equal(await record.getByText('買家申請原因：', { exact: false }).count(), 0);
      if (channel === 'regular') {
        await page.locator('.system-state').getByText('宅配到府 · 配送中', { exact: true }).waitFor();
        assert.equal(await page.getByText('in_transit', { exact: true }).count(), 0);
      }
      assert.deepEqual(state.writes, []);
      await assertClean(page, state);
    });
  }
}

async function fixture(t, { role = 'admin', width = 1280, phone = '', channel = 'regular', status = 'paid', legacyCancellationApi = false } = {}) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block', reducedMotion: 'reduce' });
  t.after(() => context.close());
  const state = {
    order: { ...structuredClone(channel === 'meal_preorder' ? meal : regular), sales_channel: channel, payment_status: status },
    settings: { version: 1, orders_open: true, closed_message: '暫停接受新訂單', support_phone: phone, printer_brand: '', printer_model: '', printer_connection: 'unknown', kitchen_sound: false },
    writes: [], reads: [], unknown: [], errors: [], failure: false, phoneFailure: false,
    orderSearches: [], cancellationSearches: [], cancellationReportFilters: [],
    similarOrder: null,
  };
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({ status: role === 'guest' ? 401 : 200, json: role === 'guest' ? { detail: '隔離訪客' } : { access_token: 'isolated', user: { id: 'isolated-user', email: 'isolated@example.test', display_name: '隔離驗收帳號', user_role: role, membership_type: 'nonmember' } } });
    if (method === 'GET') state.reads.push(path);
    if (path === '/v1/platform') return route.fulfill(state.phoneFailure ? { status: 503, json: { detail: '隔離平台讀取失敗' } } : { json: state.settings });
    if (path === '/v1/admin/membership-education/settings') return route.fulfill({ json: { configured: false, version: 0, retry_limit: 0, retry_interval_minutes: 0, updated_at: null, updated_by_id: null } });
    if (path === '/v1/admin/platform') {
      if (method === 'PUT') { const body = request.postDataJSON(); state.writes.push({ path, body }); assert.equal(body.expected_version, state.settings.version); state.settings = { ...state.settings, ...body, version: state.settings.version + 1 }; }
      return route.fulfill({ json: state.settings });
    }
    if (path === '/v1/orders') return route.fulfill({ json: channel === 'meal_preorder' ? [] : [state.order] });
    if (path === '/v1/meal-orders') return route.fulfill({ json: channel === 'meal_preorder' ? [state.order] : [] });
    if (path === '/v1/admin/today') return route.fulfill({ json: {
      pending_admissions: 0, pending_order_cancellations: Number(state.order.cancellation_request?.status === 'pending'),
      unpaid_business_orders: 0, pending_activity_refunds: 0, active_activities: 0, active_activity_registrations: 0,
      active_campaigns: 0, pending_campaign_decisions: 0, voting_group_proposals: 0, voting_member_proposals: 0, invoice_followups: 0,
    } });
    if (path === '/v1/admin/order-search') {
      state.orderSearches.push(Object.fromEntries(url.searchParams));
      return route.fulfill({ json: { items: [state.order], total: 1, offset: 0, limit: 12 } });
    }
    if (path === '/v1/admin/meal-orders') return route.fulfill({ json: { items: [{ order: state.order, customer_name: '隔離買家', customer_email: 'isolated@example.test', meal_period: 'lunch' }], total: 1, offset: 0, limit: 20 } });
    if (path === '/v1/admin/meal-operations-summary') return route.fulfill({ json: { pending_cancellation: 0, refund_attention: state.order.payment_status === 'refund_pending' ? 1 : 0, invoice_attention: 0, today_pickup: 0 } });
    const exactReport = path.match(/^\/v1\/admin\/meal-cancellations\/orders\/([^/]+)\.csv$/);
    if (exactReport || ['/v1/admin/meal-cancellation-summary', '/v1/admin/meal-cancellations.csv', '/v1/admin/meal-cancellation-requests'].includes(path)) {
      const filters = Object.fromEntries(url.searchParams);
      const scope = !legacyCancellationApi && (exactReport?.[1] || filters.order_id);
      const matching = [state.order, state.similarOrder].filter(order => order?.cancellation_request &&
        (scope ? order.id === scope : order.order_number.includes(filters.search || '')));
      if (path === '/v1/admin/meal-cancellation-summary') {
        state.cancellationReportFilters.push({ path, ...filters });
        return route.fulfill({ json: { order_id: scope || undefined, pending: matching.filter(order => order.cancellation_request.status === 'pending').length, approved: 0, rejected: 0, approved_amount: 0, refund_pending: 0, refunded: 0 } });
      }
      const current = matching.filter(order => filters.review_status === 'all' || order.cancellation_request.status === filters.review_status);
      if (exactReport || path === '/v1/admin/meal-cancellations.csv') {
        state.cancellationReportFilters.push({ path, ...filters });
        if (exactReport && legacyCancellationApi) return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
        return route.fulfill({ contentType: 'text/csv; charset=utf-8', body: ['訂單編號', ...current.map(order => order.order_number)].join('\n') });
      }
      state.cancellationSearches.push(filters);
      const items = current.map(order => ({
        order, request: order.cancellation_request, customer_name: '隔離買家', customer_email: 'isolated@example.test',
        can_approve: !order.cancelled_at,
      }));
      return route.fulfill({ json: { items, total: items.length, offset: 0, limit: 30 } });
    }
    if (method === 'POST' && path === `/v1/admin/meal-cancellation-requests/${state.order.cancellation_request?.id}/review`) {
      state.writes.push({ path, body: request.postDataJSON() });
      state.order.cancellation_request = { ...state.order.cancellation_request, status: 'rejected' };
      return route.fulfill({ json: state.order.cancellation_request });
    }
    if (['/v1/admin/meals', '/v1/admin/meal-events', '/v1/products', '/v1/activities', '/v1/notifications'].includes(path)) return route.fulfill({ json: [] });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'nonmember', membership: null } });
    if (path === `/v1/orders/${state.order.id}/admin/cancel` || path === `/v1/admin/meal-orders/${state.order.id}/cancel`) {
      state.writes.push({ path, body: request.postDataJSON() });
      if (state.failure) return route.fulfill({ status: 409, json: { detail: '隔離訂單已取餐，無法退單' } });
      state.order = { ...state.order, payment_status: status === 'pending' ? 'expired' : 'refund_pending', fulfillment_status: 'cancelled', cancelled_at: '2030-01-02T02:00:00Z', invoice_status: channel === 'meal_preorder' ? 'external' : 'void_pending', available_actions: channel === 'meal_preorder' && status === 'paid' ? ['confirm_cash_refund'] : [] };
      return route.fulfill({ json: state.order });
    }
    if (path === `/v1/orders/${state.order.id}/admin/cash-refund`) {
      state.writes.push({ path, body: request.postDataJSON() });
      state.order = { ...state.order, payment_status: 'refunded', available_actions: [] };
      return route.fulfill({ json: state.order });
    }
    if ([`/v1/admin/orders/${state.order.id}/invoice/query`, `/v1/admin/orders/${state.order.id}/payment/query`].includes(path)) {
      state.writes.push({ path, body: request.postDataJSON() });
      return route.fulfill({ json: { message: '隔離查詢已完成', status: path.endsWith('/invoice/query') ? state.order.invoice_status : state.order.payment_status } });
    }
    state.unknown.push(`${method} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  return { page, state };
}

async function assertClean(page, state) {
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, '頁面不能橫向溢出');
}

for (const channel of ['regular', 'group', 'meal_preorder']) {
  test(`消費者 ${channel} 只提供電話退單且不使用過期取消 action`, async t => {
    const { page, state } = await fixture(t, { role: 'customer', width: 390, phone: '+886 (2) 2345-6789', channel });
    state.order.available_actions = ['cancel', 'request_cancellation'];
    await page.goto(`${base}/${channel === 'meal_preorder' ? 'meal-orders' : 'orders'}`);
    if (channel !== 'meal_preorder') await page.getByRole('tab', { name: '一般訂單', exact: true }).click();
    const contact = page.getByRole('region', { name: '電話聯絡退單', exact: true });
    await contact.getByRole('link').waitFor();
    assert.equal(await contact.getByRole('link').getAttribute('href'), 'tel:+886223456789');
    assert.equal(await page.getByText('申請退單', { exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: /申請退單|申請取消|辦理退單|取消訂單/ }).count(), 0);
    assert.deepEqual(state.writes, []);
    await assertClean(page, state);
  });
}

test('電話未設定與讀取失敗不提供假號，重試可恢復電話', async t => {
  const { page, state } = await fixture(t, { role: 'customer', width: 390 });
  await page.goto(`${base}/orders`);
  await page.getByRole('tab', { name: '一般訂單', exact: true }).click();
  const absent = page.getByRole('button', { name: '退單聯絡電話尚未設定', exact: true });
  await absent.waitFor(); assert.equal(await absent.isDisabled(), true);
  assert.equal(await page.locator('a[href^="tel:"]').count(), 0);
  if (process.env.BROWSER_ARTIFACT_DIR) {
    await absent.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/consumer-cancel-phone-unset-mobile.png` });
  }
  state.phoneFailure = true;
  await page.reload();
  await page.getByRole('tab', { name: '一般訂單', exact: true }).click();
  await page.getByRole('button', { name: '退單聯絡電話暫時無法讀取', exact: true }).waitFor();
  assert.equal(await page.locator('a[href^="tel:"]').count(), 0);
  state.phoneFailure = false; state.settings.support_phone = '02-2345-6789';
  await page.getByRole('button', { name: '重新讀取聯絡電話', exact: true }).click();
  await page.getByRole('link', { name: '電話聯絡退單 · 02-2345-6789', exact: true }).waitFor();
  await assertClean(page, state);
});

test('管理員可設定與清空客服電話，消費者訂單跟隨已存電話', async t => {
  const { page, state } = await fixture(t, { width: 390 });
  await page.goto(`${base}/admin/settings`);
  const input = page.getByLabel('客服電話（退單聯絡）', { exact: true });
  await input.fill('(02) 2345-6789');
  await page.getByRole('button', { name: '儲存平台設定', exact: true }).click();
  await page.getByText('設定已儲存', { exact: true }).waitFor();
  assert.equal(state.writes[0].body.support_phone, '(02) 2345-6789');
  await page.goto(`${base}/orders`);
  await page.getByRole('tab', { name: '一般訂單', exact: true }).click();
  await page.getByRole('link', { name: '電話聯絡退單 · (02) 2345-6789', exact: true }).waitFor();
  await page.goto(`${base}/admin/settings`);
  await input.fill('');
  await page.getByRole('button', { name: '儲存平台設定', exact: true }).click();
  await page.getByText('設定已儲存', { exact: true }).waitFor();
  assert.equal(state.writes.at(-1).body.support_phone, '');
  await page.goto(`${base}/orders`);
  await page.getByRole('tab', { name: '一般訂單', exact: true }).click();
  await page.getByRole('button', { name: '退單聯絡電話尚未設定', exact: true }).waitFor();
  await assertClean(page, state);
});

for (const status of ['pending', 'paid']) {
  test(`管理員商品 ${status} 原因可留空，確認後才辦理退單`, async t => {
    const { page, state } = await fixture(t, { status });
    await page.goto(`${base}/admin?section=orders`);
    await page.getByRole('button', { name: '辦理退單', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '確認辦理退單', exact: true });
    const confirm = dialog.getByRole('button', { name: '確認退單', exact: true });
    assert.equal(await confirm.isEnabled(), true);
    await dialog.getByLabel('退單原因', { exact: true }).fill('   ');
    assert.equal(await confirm.isEnabled(), true);
    if (status === 'paid') await dialog.getByLabel('退單原因', { exact: true }).fill('  顧客來電確認退單  ');
    if (status === 'paid' && process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/admin-cancel-desktop.png` });
    assert.deepEqual(state.writes, []);
    await confirm.click();
    await page.locator('.admin-cancel-order [role="status"]').waitFor();
    assert.deepEqual(state.writes, [{ path: '/v1/orders/regular-order/admin/cancel', body: { reason: status === 'paid' ? '顧客來電確認退單' : '' } }]);
    assert.match(await page.locator('.admin-cancel-order').innerText(), status === 'paid' ? /付款狀態：退款處理中/ : /付款狀態：已取消/);
    assert.equal(await page.getByRole('button', { name: '審核退款', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '辦理退單', exact: true }).count(), 0);
    assert.ok(state.reads.filter(path => path === '/v1/admin/order-search').length >= 2);
    await assertClean(page, state);
  });
}

for (const path of ['/admin', '/admin/meals']) {
  test(`${path} 便當退單先待現場退款，再核對實退款`, async t => {
    const { page, state } = await fixture(t, { width: 390, channel: 'meal_preorder' });
    await page.goto(`${base}${path === '/admin' ? '/admin?section=orders' : path}`);
    await page.getByRole('button', { name: '辦理退單', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '確認辦理退單', exact: true });
    await dialog.getByLabel('退單原因', { exact: true }).fill('顧客來電，尚未取餐');
    assert.equal(await dialog.evaluate(node => node.getBoundingClientRect().left >= 0 && node.getBoundingClientRect().right <= innerWidth), true);
    if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/admin-cancel-${path === '/admin' ? 'orders' : 'meals'}-mobile.png` });
    await dialog.getByRole('button', { name: '確認退單', exact: true }).click();
    await page.locator('.admin-cancel-order [role="status"]').waitFor();
    assert.match(await page.locator('.admin-cancel-order').innerText(), /付款狀態：待現場退款確認/);
    assert.doesNotMatch(await page.locator('.admin-cancel-order').innerText(), /已退款|退款成功/);
    assert.equal(state.writes[0].path, '/v1/admin/meal-orders/meal-order/cancel');
    await page.getByRole('button', { name: '確認現場退款', exact: true }).click();
    await page.getByLabel('實退金額', { exact: true }).fill('120');
    assert.equal(await page.getByRole('button', { name: '確認已退款', exact: true }).isDisabled(), true);
    await page.getByLabel('已核對款項實際退還給顧客', { exact: true }).check();
    await page.getByRole('button', { name: '確認已退款', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.admin-cash-payment'));
    assert.equal(state.writes[1].path, '/v1/orders/meal-order/admin/cash-refund');
    assert.equal(state.writes[1].body.reason, '');
    assert.equal(state.order.payment_status, 'refunded');
    assert.match(await page.locator('.admin-cancel-order').innerText(), /付款狀態：已退款/);
    await assertClean(page, state);
  });
}

for (const [label, override, expectedLink] of [
  ['已付款有效訂單', { payment_status: 'paid' }, true],
  ['待付款有效訂單', { payment_status: 'pending' }, true],
  ['已退款訂單', { payment_status: 'refunded' }, false],
  ['已取消履約訂單', { fulfillment_status: 'cancelled' }, false],
  ['已有取消時間的訂單', { cancelled_at: '2030-01-02T02:00:00Z' }, false],
]) {
  test(`後台便當 ${label} 正確顯示取餐退單入口並保留發票付款查詢`, async t => {
    const { page, state } = await fixture(t, { width: 390, channel: 'meal_preorder' });
    Object.assign(state.order, override, { payment_method: 'online', invoice_status: 'voided', available_actions: [] });
    await page.goto(`${base}/admin?section=orders`);
    await page.getByText(state.order.order_number, { exact: true }).waitFor();
    const link = page.getByRole('link', { name: '便當取餐與退單', exact: true });
    assert.equal(await link.count(), expectedLink ? 1 : 0);
    if (expectedLink) {
      const destination = new URL(await link.getAttribute('href'), base);
      assert.equal(destination.pathname, '/admin/meals');
      assert.equal(destination.searchParams.get('tab'), 'orders');
      assert.equal(destination.searchParams.get('q'), state.order.order_number);
    }
    for (const [summary, action, kind] of [['查詢發票', '查詢作廢狀態', 'invoice'], ['查詢付款', '查詢退款狀態', 'payment']]) {
      const control = page.locator('details.admin-extra-actions').filter({ has: page.locator('summary', { hasText: summary }) });
      await control.locator('summary').first().click();
      await control.getByRole('button', { name: action, exact: true }).click();
      await control.getByText('隔離查詢已完成', { exact: true }).waitFor();
      assert.equal(state.writes.at(-1).path, `/v1/admin/orders/meal-order/${kind}/query`);
    }
    assert.equal(state.writes.length, 2, '只查詢發票與付款，不更改訂單');
    await assertClean(page, state);
  });
}

for (const [stateName, status, normalized, label, canCancel] of [
  ['過去未交付', 'picked_up', 'no_show', '已結束', true],
  ['實際已交付', 'picked_up', 'picked_up', '已結束', false],
  ['已取消', 'cancelled', 'no_show', null, false],
]) {
  test(`後台訂單區分${stateName}便當，依原權限顯示退單動作`, async t => {
    const { page, state } = await fixture(t, { width: 390, channel: 'meal_preorder' });
    state.order.fulfillment_status = status;
    state.order.fulfillment = { status: normalized, pickup_ends_at: '2030-01-01T13:30:00+08:00' };
    state.order.available_actions = canCancel ? ['cancel'] : [];
    if (status === 'cancelled') state.order.payment_status = 'refunded';
    await page.goto(`${base}/admin?section=orders`);
    const card = page.locator('.operations-list article');
    await card.getByText(state.order.order_number, { exact: true }).waitFor();
    if (label) await card.getByText(label, { exact: true }).waitFor();
    else {
      await card.getByLabel('付款：已退款', { exact: true }).waitFor();
      assert.equal(await card.getByText('已結束', { exact: true }).count(), 0);
    }
    assert.doesNotMatch(await card.innerText(), /逾時未取|已完成取餐/);
    assert.equal(await card.getByRole('button', { name: '辦理退單', exact: true }).count(), Number(canCancel));
    assert.deepEqual(state.writes, []);
    await assertClean(page, state);
  });
}

for (const [status, label] of [['expired', '付款逾期'], ['failed', '付款失敗']]) {
  test(`390px 便當${label}時買家與後台都沒有退單動作`, async t => {
    const customer = await fixture(t, { role: 'customer', width: 390, phone: '02-2345-6789', channel: 'meal_preorder', status });
    const admin = await fixture(t, { width: 390, channel: 'meal_preorder', status });
    for (const { state } of [customer, admin]) {
      state.order.available_actions = ['cancel', 'request_cancellation'];
      state.order.cancellation_request = { id: 'historical', order_id: state.order.id, status: 'pending', reason: '歷史紀錄', requested_at: '2030-01-01T09:00:00+08:00' };
    }
    await customer.page.goto(`${base}/meal-orders`);
    const detail = customer.page.locator('.meal-order-detail');
    await detail.getByText(label, { exact: true }).waitFor();
    assert.doesNotMatch(await detail.innerText(), /申請退單|電話聯絡退單|退單申請待審核/);
    await admin.page.goto(`${base}/admin?section=orders`);
    const card = admin.page.locator('.operations-list article');
    await card.getByLabel(`付款：${label}`, { exact: true }).waitFor();
    assert.equal(await card.getByRole('button', { name: '辦理退單', exact: true }).count(), 0);
    assert.equal(await card.getByRole('link', { name: /退單/ }).count(), 0);
    await card.getByRole('link', { name: '查看便當訂單', exact: true }).click();
    const mealCard = admin.page.locator('.meal-operation-order');
    await mealCard.getByLabel(`付款：${label}`, { exact: true }).waitFor();
    assert.equal(await mealCard.getByRole('button', { name: '辦理退單', exact: true }).count(), 0);
    assert.equal(await mealCard.getByRole('link', { name: '審核這筆退單', exact: true }).count(), 0);
    if (process.env.BROWSER_ARTIFACT_DIR) {
      await mealCard.scrollIntoViewIfNeeded();
      await admin.page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/meal-no-cancellation-${status}-390.png` });
    }
    for (const { page, state } of [customer, admin]) {
      assert.deepEqual(state.writes, []);
      await assertClean(page, state);
    }
  });
}

test('390px 管理員可在退單審核找到代辦退單的買家姓名及電話', async t => {
  const { page, state } = await fixture(t, { width: 390, channel: 'meal_preorder', status: 'refunded' });
  state.order.invoice_status = 'voided';
  state.order.payment_method = 'online';
  state.order.contact_phone = '0912345678';
  state.order.cancellation_request = {
    id: 'admin-filed', order_id: state.order.id, status: 'approved', source: 'admin', initiated_by_admin: true,
    reason: '電話確認', requested_at: '2030-01-01T09:00:00+08:00', reviewed_by_name: '隔離管理員', reviewed_at: '2030-01-01T09:00:00+08:00',
  };
  await page.goto(`${base}/admin/meals?tab=cancellations`);
  await page.getByLabel('退單審核狀態').selectOption('approved');
  const card = page.locator('.meal-cancellation-card');
  await card.getByText('隔離買家・0912345678・合作社', { exact: true }).waitFor();
  assert.equal(await card.getByRole('button', { name: '核准退單', exact: true }).count(), 0);
  if (process.env.BROWSER_ARTIFACT_DIR) {
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/admin-filed-meal-cancellation-390.png` });
  }
  assert.deepEqual(state.writes, []);
  await assertClean(page, state);
});

for (const rejectionOnly of [false, true]) {
  test(rejectionOnly ? '訂單查詢直接開啟僅可拒絕的便當退單' : '今日待辦直接開啟指定便當退單', async t => {
    const { page, state } = await fixture(t, { width: rejectionOnly ? 390 : 1280, channel: 'meal_preorder' });
    const orderNumber = state.order.order_number = 'M2030010210';
    state.order.cancellation_request = {
      id: 'meal-request', order_id: state.order.id, status: 'pending', reason: '隔離退單申請', requested_at: '2030-01-02T01:00:00Z',
    };
    const similarNumber = 'M20300102100';
    state.similarOrder = { ...state.order, id: 'similar-order', order_number: similarNumber,
      cancellation_request: { ...state.order.cancellation_request, id: 'similar-request', order_id: 'similar-order' } };
    if (rejectionOnly) {
      Object.assign(state.order, { payment_status: 'refunded', fulfillment_status: 'cancelled', cancelled_at: '2030-01-02T02:00:00Z', available_actions: [] });
      await page.goto(`${base}/admin?section=orders&q=${orderNumber}`);
    } else {
      await page.goto(`${base}/admin`);
      await page.getByRole('link', { name: /待審退單/ }).click();
      assert.match(page.url(), /queue=refunds/);
    }
    const link = page.getByRole('link', { name: '審核便當退單', exact: true });
    await link.waitFor();
    assert.equal(await link.count(), 1);
    assert.equal(await page.getByRole('link', { name: '便當取餐與退單', exact: true }).count(), 0);
    assert.equal(state.orderSearches[0].q, rejectionOnly ? orderNumber : '');
    assert.equal(state.orderSearches[0].cancellation_pending, String(!rejectionOnly));
    await link.click();

    const reviewTab = page.getByRole('tab', { name: '退單審核', exact: true });
    const card = page.getByRole('article').filter({ has: page.getByRole('heading', { name: orderNumber, exact: true }) });
    const approve = card.getByRole('button', { name: '核准退單', exact: true });
    const reject = card.getByRole('button', { name: '拒絕退單', exact: true });
    await approve.waitFor();
    assert.equal(await reviewTab.getAttribute('aria-selected'), 'true');
    assert.equal(await page.getByLabel('訂單編號或 Email').inputValue(), orderNumber);
    assert.equal(new URL(page.url()).searchParams.get('q'), orderNumber);
    assert.equal(new URL(page.url()).searchParams.get('order_id'), state.order.id);
    assert.equal(state.cancellationSearches[0].search, orderNumber);
    assert.equal(state.cancellationSearches[0].order_id, state.order.id);
    assert.equal(state.cancellationSearches[0].review_status, 'pending');
    assert.equal(await page.getByRole('heading', { name: similarNumber, exact: true }).count(), 0);
    assert.equal(state.reads.includes('/v1/admin/meal-orders'), false);
    assert.equal(await approve.isDisabled(), rejectionOnly);
    assert.equal(await reject.isEnabled(), true);
    assert.deepEqual(state.writes, []);
    if (rejectionOnly) {
      await reject.click();
      const confirm = card.getByRole('button', { name: '確認拒絕', exact: true });
      assert.deepEqual(state.writes, []);
      await confirm.click();
      await page.getByText('沒有符合條件的退單申請', { exact: true }).waitFor();
      assert.deepEqual(state.writes, [{ path: '/v1/admin/meal-cancellation-requests/meal-request/review', body: { decision: 'reject', reason: '' } }]);
      assert.equal(state.order.payment_status, 'refunded');
      assert.equal(state.similarOrder.cancellation_request.status, 'pending');
    } else {
      const file = page.waitForEvent('download');
      await page.getByRole('button', { name: '匯出退單明細', exact: true }).click();
      assert.equal(await readFile(await (await file).path(), 'utf8'), `訂單編號\n${orderNumber}`);
      assert.deepEqual(state.cancellationReportFilters, [
        { path: '/v1/admin/meal-cancellation-summary', search: orderNumber, order_id: state.order.id },
        { path: `/v1/admin/meal-cancellations/orders/${state.order.id}.csv`, review_status: 'pending' },
      ]);
      await page.getByLabel('訂單編號或 Email').fill('M20300102');
      await page.getByRole('button', { name: '搜尋', exact: true }).click();
      await page.getByRole('heading', { name: similarNumber, exact: true }).waitFor();
      assert.equal(state.cancellationSearches.at(-1).order_id, undefined);
      await page.goto(`${base}/admin/meals?tab=cancellations&q=${orderNumber}`);
      await page.getByRole('heading', { name: similarNumber, exact: true }).waitFor();
      assert.equal(state.cancellationSearches.at(-1).order_id, undefined);
    }
    await assertClean(page, state);
  });
}

test('舊 API 的寬鬆清單與摘要不能擴大單筆退單操作或匯出', async t => {
  const { page, state } = await fixture(t, { width: 390, channel: 'meal_preorder', legacyCancellationApi: true });
  state.order.order_number = 'M2030010210';
  state.order.cancellation_request = {
    id: 'meal-request', order_id: state.order.id, status: 'pending', requested_at: '2030-01-02T01:00:00Z',
  };
  state.similarOrder = { ...state.order, id: 'similar-order', order_number: 'M20300102100',
    cancellation_request: { ...state.order.cancellation_request, id: 'similar-request', order_id: 'similar-order' } };
  await page.goto(`${base}/admin?section=orders`);
  await page.getByRole('link', { name: '審核便當退單', exact: true }).click();
  await page.getByRole('heading', { name: state.order.order_number, exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: state.similarOrder.order_number, exact: true }).count(), 0);
  const summary = page.getByRole('region', { name: '退單統整', exact: true });
  await summary.getByText('單筆退單統整暫時無法讀取。', { exact: true }).waitFor();
  assert.equal(await summary.locator('.admin-cancellation-metrics strong').count(), 0);
  const downloads = [];
  page.on('download', file => downloads.push(file));
  await summary.getByRole('button', { name: '匯出退單明細', exact: true }).click();
  await summary.getByText('單筆退單匯出暫時無法使用，請稍後重試。', { exact: true }).waitFor();
  assert.deepEqual(state.cancellationReportFilters.filter(item => item.path.endsWith('.csv')).map(item => item.path),
    [`/v1/admin/meal-cancellations/orders/${state.order.id}.csv`]);
  assert.deepEqual(downloads, []);
  assert.deepEqual(state.writes, []);
  await assertClean(page, state);
});

test('退單錯誤保留原因及對話，歷史待審入口仍可操作', async t => {
  const { page, state } = await fixture(t);
  state.failure = true;
  state.order.cancellation_request = { id: 'existing-request', status: 'pending', reason: '既有退單申請', requested_at: '2030-01-02T01:00:00Z' };
  await page.goto(`${base}/admin?section=orders`);
  await page.getByRole('button', { name: '拒絕退單申請', exact: true }).waitFor();
  assert.equal(await page.getByRole('link', { name: '審核便當退單', exact: true }).count(), 0);
  await page.getByRole('button', { name: '辦理退單', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '確認辦理退單', exact: true });
  await dialog.getByLabel('退單原因', { exact: true }).fill('電話確認');
  await dialog.getByRole('button', { name: '確認退單', exact: true }).click();
  await dialog.getByRole('alert').waitFor();
  assert.equal(await dialog.getByLabel('退單原因', { exact: true }).inputValue(), '電話確認');
  assert.equal(await page.locator('.admin-cancel-order [role="status"]').count(), 0);
  assert.equal(state.order.fulfillment_status, 'pending_confirmation');
  await assertClean(page, state);
});

test('消費者直接開後台沒有退單操作且不讀管理員 API', async t => {
  const { page, state } = await fixture(t, { role: 'customer' });
  for (const path of ['/admin', '/admin/meals', '/admin/settings']) {
    await page.goto(`${base}${path}`);
    await page.locator('.admin-gate').waitFor();
    assert.equal(await page.getByRole('button', { name: '辦理退單', exact: true }).count(), 0);
  }
  assert.deepEqual(state.reads.filter(path => path.startsWith('/v1/admin/')), []);
  assert.deepEqual(state.writes, []);
  await assertClean(page, state);
});

test('首頁生活消費旁保留永續活動與加入合作社，手機放大不溢出', async t => {
  const { page, state } = await fixture(t, { role: 'customer', width: 390 });
  await page.goto(`${base}/`);
  const actions = page.locator('.hero-actions');
  await actions.getByRole('link', { name: '進入生活消費', exact: true }).waitFor();
  assert.deepEqual((await actions.getByRole('link').allTextContents()).map(value => value.trim()), ['進入生活消費', '永續活動', '加入合作社']);
  assert.equal(await actions.getByRole('link', { name: '永續活動', exact: true }).getAttribute('href'), '/activities');
  await page.locator('.reading-menu > summary').click();
  await page.getByRole('group', { name: '文字大小', exact: true }).getByRole('button', { name: '放大', exact: true }).click();
  await page.waitForFunction(() => getComputedStyle(document.documentElement).fontSize === '20px');
  await assertClean(page, state);
});
