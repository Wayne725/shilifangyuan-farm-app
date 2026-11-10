import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
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

async function fixture(t, { role = 'admin', width = 1280, phone = '', channel = 'regular', status = 'paid' } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const state = {
    order: { ...structuredClone(channel === 'meal_preorder' ? meal : regular), sales_channel: channel, payment_status: status },
    settings: { version: 1, orders_open: true, closed_message: '暫停接受新訂單', support_phone: phone, printer_brand: '', printer_model: '', printer_connection: 'unknown', kitchen_sound: false },
    writes: [], reads: [], unknown: [], errors: [], failure: false, phoneFailure: false,
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
    if (path === '/v1/admin/order-search') return route.fulfill({ json: { items: [state.order], total: 1, offset: 0, limit: 12 } });
    if (path === '/v1/admin/meal-orders') return route.fulfill({ json: { items: [{ order: state.order, customer_name: '隔離買家', customer_email: 'isolated@example.test', meal_period: 'lunch' }], total: 1, offset: 0, limit: 20 } });
    if (path === '/v1/admin/meal-operations-summary') return route.fulfill({ json: { pending_cancellation: 0, refund_attention: state.order.payment_status === 'refund_pending' ? 1 : 0, invoice_attention: 0, today_pickup: 0 } });
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
    await page.goto(`${base}/admin`);
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
    await page.goto(`${base}${path}`);
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
    if (expectedLink) assert.match(await link.getAttribute('href'), /\/admin\/meals\?.*q=ISOLATED-MEAL/);
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

test('退單錯誤保留原因及對話，歷史待審入口仍可操作', async t => {
  const { page, state } = await fixture(t);
  state.failure = true;
  state.order.cancellation_request = { id: 'existing-request', status: 'pending', reason: '既有退單申請', requested_at: '2030-01-02T01:00:00Z' };
  await page.goto(`${base}/admin`);
  await page.getByRole('button', { name: '拒絕退單申請', exact: true }).waitFor();
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
