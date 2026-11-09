import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4189');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use an isolated local origin');
const base = target.origin;
const date = '2030-01-03';
function order(id, status = 'pending', payment = 'paid') {
  return { id, order_number: `QUICK-${id}`, sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup',
    meal_event_id: 'event', meal_event_title: '每日供餐', venue_name: '合作社', pickup_start: `${date}T11:00:00+08:00`,
    pickup_end: `${date}T13:30:00+08:00`, pickup_at: `${date}T11:30:00+08:00`, fulfillment_status: status, payment_status: payment,
    invoice_status: 'issued', amount_total: 120, tax_amount: 0, created_at: '2030-01-02T01:00:00Z', available_actions: [],
    items: [{ offering_id: 'dish', meal_id: 'dish', meal_name: '蔬食便當', quantity: 1, subtotal: 120, tax_type: 'tax_exempt', selections: [] }] };
}
function request(id, canApprove = true) {
  const meal = order(id);
  const cancellation = { id: `request-${id}`, order_id: id, reason: '行程更改', status: 'pending', requested_at: '2030-01-02T02:00:00Z' };
  return { request: cancellation, order: { ...meal, cancellation_request: cancellation }, customer_name: '測試使用者', customer_email: 'test@example.com', can_approve: canApprove };
}
async function fixture(t, width) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const state = { writes: [], reads: [], errors: [], unknown: [], events: [], conflict: false, holdReview: false, holdCancel: false, holdQuery: false, queryStatus: 200, refunded: false,
    requests: [request('A'), request('B', false)], orders: [order('A'), order('C', 'pending', 'pending'), { ...order('D'), cancelled_at: '2030-01-02T02:00:00Z' }] };
  state.orders[0].cancellation_request = state.requests[0].request;
  const retail = { id: 'retail', order_number: 'RETAIL-001', amount_total: 240, payment_status: 'paid', fulfillment_status: 'pending_confirmation', fulfillment_method: 'cooperative_pickup', sales_channel: 'regular', created_at: '2030-01-02T01:00:00Z', invoice_status: 'issued', items: [{ tax_type: 'tax_exempt', quantity: 2, subtotal: 240 }], available_actions: ['cancel'], payment_method: 'online', invoice: { provider: 'fanyu', status: 'issued' } };
  await context.route('**/*', async (route) => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return req.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'admin', user_role: 'admin', display_name: '測試管理員', email: 'admin@example.com' } } });
    if (req.method() === 'GET') {
      if (path === '/v1/admin/printers/status') return route.fulfill({json: {enabled: false, dry_run: true, status: 'disabled', error: null}});
      if (path === '/v1/admin/printers/jobs') return route.fulfill({json: {items: [], total: 0}});
      if (path === '/v1/admin/platform') return route.fulfill({json:{orders_open:true,closed_message:'',printer_brand:'',printer_model:'',printer_connection:'unknown',kitchen_sound:false,invoice_provider:'fanyu'}});
      if (path === '/v1/admin/meal-cancellation-summary') return route.fulfill({json:{requests:2,orders:2,pending:2,approved:0,rejected:0,pending_amount:240,approved_amount:0,refund_pending:0,refunded:0,refund_failed:0}});
      state.reads.push({ path, ...Object.fromEntries(url.searchParams) });
      if (path === '/v1/admin/meal-events') return route.fulfill({ json: state.events });
      if (path === '/v1/admin/meal-calendar') return route.fulfill({ json: { service_date: date, is_workday: true, previous_workday: '2030-01-02', next_workday: '2030-01-04', ordering_starts_at: '2030-01-01T16:00:00Z', ordering_ends_at: '2030-01-02T12:00:00Z' } });
      if (['/v1/products', '/v1/group-campaigns', '/v1/meal-events', '/v1/admin/meals'].includes(path)) return route.fulfill({ json: [] });
      if (path === '/v1/admin/meal-operations-summary') return route.fulfill({ json: { today: '2030-01-02', pending_cancellation: 2, refund_attention: 0, invoice_attention: 0, today_pickup: 0 } });
      if (path === '/v1/admin/meal-orders') {
        const rows = state.orders.filter((o) => !url.searchParams.get('search') || o.order_number.includes(url.searchParams.get('search')));
        return route.fulfill({ json: { items: rows.map((o) => ({ order: o, customer_name: '測試使用者', customer_email: 'test@example.com', meal_period: 'lunch' })), total: rows.length, limit: 20, offset: 0 } });
      }
      if (path === '/v1/admin/meal-cancellation-requests') {
        const rows = state.requests.filter((row) => (!url.searchParams.get('search') || row.order.order_number.includes(url.searchParams.get('search'))) && (url.searchParams.get('review_status') === 'all' || row.request.status === url.searchParams.get('review_status')));
        return route.fulfill({ json: { items: rows, total: rows.length, limit: 30, offset: 0 } });
      }
      if (path === '/v1/admin/meal-kitchen') return route.fulfill({ json: { items: [{ id: 'A', order_number: 'QUICK-A', customer_name: '測試使用者', location: '合作社', pickup_at: `${date}T11:30:00+08:00`, pickup_time_confirmed: true, fulfillment_status: 'pending_confirmation', pending_cancellation: { reason: '行程更改' }, items: [{ name: '蔬食便當', quantity: 1, unit: '份', options: [] }] }], total: 1, meal_quantity: 1, products: [] } });
      if (path === '/v1/admin/order-search') { if (state.refunded) retail.payment_status = 'refunded'; return route.fulfill({ json: { items: [retail], total: 1, limit: 12, offset: 0 } }); }
    }
    const review = /^\/v1\/admin\/meal-cancellation-requests\/(request-[AB])\/review$/.exec(path);
    if (req.method() === 'POST' && review) {
      const body = req.postDataJSON(); state.writes.push({ path, body });
      if (state.holdReview) await new Promise((resolve) => { state.releaseReview = resolve; });
      if (state.conflict) return route.fulfill({ status: 409, json: { detail: '申請已由另一位管理員處理，請更新清單' } });
      const row = state.requests.find((r) => r.request.id === review[1]);
      row.request.status = body.decision === 'approve' ? 'approved' : 'rejected'; row.request.review_reason = body.reason;
      return route.fulfill({ json: row.request });
    }
    if (req.method() === 'PATCH' && path === '/v1/admin/meal-kitchen/orders/A/status') {
      const body = req.postDataJSON(); state.writes.push({ path, body });
      state.orders[0].fulfillment_status = body.status === 'ready_for_pickup' ? 'ready' : body.status;
      return route.fulfill({ json: {} });
    }
    if (req.method() === 'POST' && path === '/v1/orders/retail/admin/cancel') {
      state.writes.push({path, body: req.postDataJSON()});
      if (state.holdCancel) await new Promise(resolve => {state.releaseCancel = resolve;});
      Object.assign(retail, {payment_status: 'refund_pending', fulfillment_status: 'cancelled', cancelled_at: '2030-01-02T02:00:00Z', available_actions: []});
      return route.fulfill({json: retail});
    }
    if (req.method() === 'POST' && /^\/v1\/(admin\/orders\/retail\/(invoice\/query|invoice\/void|payment\/query)|orders\/retail\/admin\/refund)$/.test(path)) {
      state.writes.push({ path, body: req.postDataJSON() });
      if (path.endsWith('/refund')) { retail.payment_status = 'refund_pending'; return route.fulfill({ json: retail }); }
      if (path.endsWith('/query') && state.holdQuery) await new Promise((resolve) => { state.releaseQuery = resolve; });
      if (path.endsWith('/query') && state.queryStatus !== 200) return route.fulfill({ status: state.queryStatus, json: { detail: '供應商暫時無回應，請稍後重查' } });
      return route.fulfill({ json: { message: '查詢完成' } });
    }
    state.unknown.push(`${req.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', (e) => state.errors.push(e.message));
  await page.clock.install({ time: new Date('2030-01-02T09:00:00+08:00') });
  return { page, state };
}
async function responseAfter(page, match, action) {
  await Promise.all([page.waitForResponse(match), action()]);
}
async function verify(page, state) {
  assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}
for (const width of [1440, 390]) {
  test(`${width}px 手動場次提醒同地點重疊，鄰接及其他地點不誤報`, async (t) => {
    const { page, state } = await fixture(t, width);
    state.events = [{ id: 'scheduled', title: '每日供餐 2030-01-03', location: '合作社', status: 'published', can_edit: false, schedule_template_id: 'template', offerings: [],
      pickup_starts_at: `${date}T11:00:00+08:00`, pickup_ends_at: `${date}T13:30:00+08:00` }];
    await page.goto(`${base}/admin/meals?tab=events`);
    const form = page.locator('form').filter({ has: page.getByRole('heading', { name: '建立便當場次', exact: true }) });
    await form.getByLabel('取餐地點', { exact: true }).fill('合作社');
    await form.getByRole('status').filter({ hasText: '相同地點已有重疊場次' }).waitFor();
    assert.match(await form.getByRole('status').innerText(), /循環排程/);
    await form.getByLabel('取餐開始（台灣時間）', { exact: true }).fill(`${date}T13:30`);
    assert.equal(await form.getByRole('status').count(), 0);
    await form.getByLabel('取餐開始（台灣時間）', { exact: true }).fill(`${date}T11:00`);
    await form.getByLabel('取餐地點', { exact: true }).fill('其他地點');
    assert.equal(await form.getByRole('status').count(), 0);
    assert.equal(state.writes.length, 0);
    await verify(page, state);
  });
  test(`${width}px 核對按鈕直接查詢並在區塊顯示結果，內容保留內距`, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/admin`);
    await page.getByText('查詢發票', { exact: true }).click();
    const invoice = page.locator('.admin-extra-actions').filter({ has: page.getByText('查詢發票', { exact: true }) });
    await Promise.all([
      page.waitForResponse((r) => r.url().endsWith('/invoice/query'), { timeout: 1500 }),
      invoice.getByRole('button', { name: /^(核對開立|查詢開立狀態)$/ }).click(),
    ]);
    await invoice.getByRole('status').filter({ hasText: '查詢完成' }).waitFor();
    assert.equal(state.writes.length, 1);
    const inset = await invoice.locator('.operation-invoice-query').evaluate((element) => Number.parseFloat(getComputedStyle(element).paddingLeft));
    assert.ok(inset >= 16, `查詢框左內距 ${inset}px`);
    await page.getByText('查詢付款', { exact: true }).click();
    const payment = page.locator('.admin-extra-actions').filter({ has: page.getByText('查詢付款', { exact: true }) });
    await Promise.all([
      page.waitForResponse((r) => r.url().endsWith('/payment/query'), { timeout: 1500 }),
      payment.getByRole('button', { name: /^(核對退款|查詢退款狀態)$/ }).click(),
    ]);
    await payment.getByRole('status').filter({ hasText: '查詢完成' }).waitFor();
    assert.deepEqual(state.writes.map((r) => r.body.reason), ['客服核對發票狀態', '客服核對退款狀態']);
    await verify(page, state);
  });
  test(`${width}px 快捷日期與履約下拉查詢、清單製作操作不換頁，歷史／未付款不顯示製作鍵`, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/admin/meals`);
    const row = page.locator('.meal-operation-order').filter({ hasText: 'QUICK-A' });
    await row.getByRole('button', { name: '開始製作', exact: true }).waitFor();
    assert.equal(await page.getByLabel('取餐日期（起）', { exact: true }).isVisible(), false);
    await responseAfter(page, (r) => r.url().includes('date_from=2030-01-03'), () => page.getByRole('group', { name: '快速取餐日期', exact: true }).getByRole('button', { name: '明天', exact: true }).click());
    assert.equal(await page.getByRole('group', { name: '快速製作狀態', exact: true }).count(), 0);
    await page.getByRole('combobox', {name: '履約狀態', exact: true}).selectOption('pending_confirmation');
    assert.equal(state.reads.filter((r) => r.path === '/v1/admin/meal-orders').at(-1).fulfillment_status, undefined, '選擇後由查詢按鈕套用');
    await responseAfter(page, (r) => r.url().includes('fulfillment_status=pending_confirmation'), () => page.getByRole('button', {name: '查詢訂單', exact: true}).click());
    const read = state.reads.filter((r) => r.path === '/v1/admin/meal-orders').at(-1);
    assert.equal(read.date_to, date); assert.equal(read.fulfillment_status, 'pending_confirmation');
    assert.equal(state.writes.length, 0);
    await row.getByRole('button', { name: '開始製作', exact: true }).click();
    await row.getByRole('button', { name: '標記待取餐', exact: true }).waitFor();
    await responseAfter(page, (r) => r.url().includes('/meal-orders?'), () => row.getByRole('button', { name: '標記待取餐', exact: true }).click());
    assert.deepEqual(state.writes.map((r) => r.body.status), ['preparing', 'ready_for_pickup']);
    for (const id of ['C', 'D']) assert.equal(await page.locator('.meal-operation-order').filter({ hasText: `QUICK-${id}` }).getByRole('button', { name: '開始製作', exact: true }).count(), 0);
    await page.locator('.reading-menu > summary').click();
    await page.getByRole('button', { name: '放大', exact: true }).click();
    await page.waitForFunction(() => getComputedStyle(document.documentElement).fontSize === '20px');
    await verify(page, state);
  });
  test(`${width}px 退單審核下拉保留關鍵字並查詢全部與單一狀態，不變更訂單`, async (t) => {
    const {page, state} = await fixture(t, width);
    await page.goto(`${base}/admin/meals?tab=cancellations&q=QUICK-A`);
    const status = page.getByRole('combobox', {name: '退單審核狀態', exact: true});
    await status.waitFor();
    assert.equal(await status.inputValue(), 'pending');
    for (const value of ['approved', 'rejected', 'all']) {
      await responseAfter(page, response => response.url().includes('/meal-cancellation-requests?') && new URL(response.url()).searchParams.get('review_status') === value, () => status.selectOption(value));
      const read = state.reads.filter(row => row.path === '/v1/admin/meal-cancellation-requests').at(-1);
      assert.equal(read.search, 'QUICK-A');
      assert.equal(read.offset, '0');
      if (value === 'approved' || value === 'rejected') await page.getByText('沒有符合條件的退單申請', {exact: true}).waitFor();
      else await page.getByRole('heading', {name: 'QUICK-A', exact: true}).waitFor();
    }
    await status.selectOption('pending');
    await page.getByRole('heading', {name: 'QUICK-A', exact: true}).waitFor();
    assert.equal(await status.inputValue(), 'pending');
    assert.deepEqual(state.writes, []);
    await page.locator('.reading-menu > summary').click();
    await page.getByRole('button', {name: '放大', exact: true}).click();
    await page.waitForFunction(() => getComputedStyle(document.documentElement).fontSize === '20px');
    await verify(page, state);
  });
  test(`${width}px 廚房直達單筆退單，重整保留目標，核准兩次點擊零輸入`, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/admin/kitchen`);
    await page.getByRole('link', { name: '審核這筆退單', exact: true }).click();
    await page.getByRole('button', { name: '核准退單', exact: true }).waitFor();
    assert.match(page.url(), /tab=cancellations/); assert.match(page.url(), /q=QUICK-A/);
    await page.reload(); await page.getByRole('button', { name: '核准退單', exact: true }).click();
    assert.equal(state.writes.length, 0);
    assert.equal(await page.locator('.meal-cancellation-review textarea').count(), 0);
    assert.equal(await page.getByRole('combobox', { name: '審核理由（買家會看到）', exact: true }).inputValue(), '依買家申請核准退單。');
    await page.locator('.admin-selected-reason').filter({ hasText: '依買家申請核准退單。' }).waitFor();
    if (process.env.ADMIN_QUICK_SCREENSHOTS && width === 1440) await page.screenshot({ path: '/Users/liuweiwei/Documents/farm/outputs/admin-quick-review-verified.png', fullPage: true });
    state.holdReview = true;
    await page.getByRole('button', { name: '確認核准', exact: true }).click();
    const pending = page.getByRole('button', { name: '處理中…', exact: true });
    await pending.waitFor();
    assert.equal(await pending.isEnabled(), false);
    assert.equal(await page.getByRole('combobox', { name: '審核理由（買家會看到）', exact: true }).isEnabled(), false);
    assert.equal(await page.getByRole('button', { name: '返回', exact: true }).isEnabled(), false);
    await pending.dblclick({ force: true });
    assert.equal(state.writes.length, 1);
    state.releaseReview();
    await page.getByText('沒有符合條件的退單申請', { exact: true }).waitFor();
    assert.deepEqual(state.writes.map((r) => r.body), [{ decision: 'approve', reason: '依買家申請核准退單。' }]);
    assert.match(state.writes[0].path, /request-A\/review$/);
    await verify(page, state);
  });
  test(`${width}px 拒絕須選理由，其他原因仍可輸入，衝突不顯示成功或自動重送`, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/admin/meals?tab=cancellations&q=QUICK-B`);
    await page.getByRole('button', { name: '拒絕退單', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: '確認拒絕', exact: true }).isEnabled(), false);
    const reason = page.getByRole('combobox', { name: '審核理由（買家會看到）', exact: true });
    assert.equal(await reason.inputValue(), '');
    await reason.selectOption({ label: '已完成取餐' });
    await page.locator('.admin-selected-reason').filter({ hasText: '訂單已完成取餐，無法取消。' }).waitFor();
    assert.equal(state.writes.length, 0);
    await reason.selectOption({ label: '其他原因' });
    assert.equal(await page.getByRole('button', { name: '確認拒絕', exact: true }).isEnabled(), false);
    await page.getByLabel('自訂審核理由（買家會看到）', { exact: true }).fill('需要補充確認');
    state.conflict = true;
    await page.getByRole('button', { name: '確認拒絕', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: '另一位管理員' }).waitFor();
    assert.equal(state.writes.length, 1); assert.equal(state.requests[1].request.status, 'pending');
    assert.equal(await page.getByLabel('自訂審核理由（買家會看到）', { exact: true }).inputValue(), '需要補充確認');
    await verify(page, state);
  });
  test(`${width}px 付款發票查詢免輸入，退單須填原因並確認才申請退款`, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/admin`);
    await page.getByText('查詢發票', { exact: true }).click();
    await responseAfter(page, (r) => r.url().endsWith('/invoice/query'), () => page.getByRole('button', { name: '查詢開立狀態', exact: true }).click());
    await page.getByText('查詢付款', { exact: true }).click();
    await responseAfter(page, (r) => r.url().endsWith('/payment/query'), () => page.getByRole('button', { name: '查詢付款狀態', exact: true }).click());
    await page.getByRole('button', { name: '辦理退單', exact: true }).click();
    const dialog = page.getByRole('dialog', {name: '確認辦理退單', exact: true});
    await dialog.waitFor();
    assert.match(await dialog.innerText(), /取消整筆訂單並申請退款.*240/);
    assert.equal(await dialog.getByRole('button', {name: '確認退單', exact: true}).isEnabled(), false);
    assert.equal(state.writes.length, 2);
    await dialog.getByLabel('退單原因', {exact: true}).fill('商品無法供應，申請退款');
    assert.equal(state.writes.filter(r => r.path.endsWith('/admin/cancel')).length, 0);
    state.holdCancel = true;
    await dialog.getByRole('button', {name: '確認退單', exact: true}).click();
    const pending = dialog.getByRole('button', {name: '退單處理中…', exact: true});
    await pending.waitFor();
    assert.equal(await pending.isEnabled(), false);
    assert.equal(await dialog.getByRole('button', {name: '返回', exact: true}).isEnabled(), false);
    assert.equal(await dialog.getByLabel('退單原因', {exact: true}).isEnabled(), false);
    await pending.dblclick({force: true});
    assert.equal(state.writes.filter(r => r.path.endsWith('/admin/cancel')).length, 1);
    await responseAfter(page, r => r.url().endsWith('/admin/cancel'), () => state.releaseCancel());
    await page.getByRole('status').filter({hasText: '訂單已取消。付款狀態：'}).waitFor();
    assert.equal(await dialog.count(), 0);
    assert.deepEqual(state.writes.map(r => r.body.reason), ['客服核對發票狀態', '客服核對付款狀態', '商品無法供應，申請退款']);
    assert.equal(state.writes.filter(r => r.path.endsWith('/admin/refund')).length, 0);
    await page.locator('.reading-menu > summary').click();
    await page.getByRole('button', { name: '放大', exact: true }).click();
    await page.waitForFunction(() => getComputedStyle(document.documentElement).fontSize === '20px');
    await verify(page, state);
  });
}

test('查詢中停用按鈕避免重送，失敗顯示在原區塊且不保留舊成功提示', async (t) => {
  const { page, state } = await fixture(t, 390);
  await page.goto(`${base}/admin`);
  await page.getByText('查詢發票', { exact: true }).click();
  const invoice = page.locator('.admin-extra-actions').filter({ has: page.getByText('查詢發票', { exact: true }) });
  state.holdQuery = true;
  await invoice.getByRole('button', { name: '查詢開立狀態', exact: true }).click();
  await invoice.getByRole('status').filter({ hasText: '正在查詢汎宇' }).waitFor();
  assert.equal(await invoice.getByRole('button', { name: '查詢作廢狀態', exact: true }).isEnabled(), false);
  assert.equal(state.writes.length, 1);
  state.holdQuery = false; state.releaseQuery();
  await invoice.getByRole('status').filter({ hasText: '查詢完成' }).waitFor();
  state.queryStatus = 502;
  await invoice.getByRole('button', { name: '查詢作廢狀態', exact: true }).click();
  await invoice.getByRole('alert').filter({ hasText: '供應商暫時無回應' }).waitFor();
  assert.equal(await invoice.getByRole('status').filter({ hasText: '查詢完成' }).count(), 0);
  assert.equal(state.writes.length, 2);
  await page.locator('.reading-menu > summary').click();
  await page.getByRole('button', { name: '放大', exact: true }).click();
  await verify(page, state);
  if (process.env.ADMIN_CLARITY_SCREENSHOTS) await page.screenshot({ path: '/Users/liuweiwei/Documents/farm/outputs/admin-status-query-verified.png', fullPage: true });
});

test('自訂查詢原因保留輸入，填寫時不查詢，明確送出一次', async (t) => {
  const { page, state } = await fixture(t, 390);
  await page.goto(`${base}/admin`);
  await page.getByText('查詢付款', { exact: true }).click();
  const payment = page.locator('.admin-extra-actions').filter({ has: page.getByText('查詢付款', { exact: true }) });
  await payment.getByText('其他查詢原因', { exact: true }).click();
  const submit = payment.getByRole('button', { name: '重新查詢雷門付款', exact: true });
  assert.equal(await submit.isEnabled(), false);
  await payment.getByLabel('金流查詢原因', { exact: true }).fill('核對跨日退款');
  assert.equal(state.writes.length, 0);
  await responseAfter(page, (r) => r.url().endsWith('/payment/query'), () => submit.click());
  await payment.getByRole('status').filter({ hasText: '查詢完成' }).waitFor();
  assert.deepEqual(state.writes.map((r) => r.body.reason), ['核對跨日退款']);
  assert.equal(await payment.getByLabel('金流查詢原因', { exact: true }).inputValue(), '核對跨日退款');
  await verify(page, state);
});

test('人工作廢只對已退款訂單顯示，原因選擇不送出，最後按鈕才核對作廢', async (t) => {
  const { page, state } = await fixture(t, 390);
  state.refunded = true;
  await page.goto(`${base}/admin`);
  await page.getByText('發票作廢處理', { exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '人工作廢／核對汎宇結果', exact: true }).isEnabled(), false);
  await page.getByRole('combobox', { name: '人工處理原因', exact: true }).selectOption({ label: '全額退款' });
  assert.equal(state.writes.length, 0);
  await responseAfter(page, (r) => r.url().endsWith('/invoice/void'), () => page.getByRole('button', { name: '人工作廢／核對汎宇結果', exact: true }).click());
  assert.deepEqual(state.writes.map((r) => r.body.reason), ['全額退款，核對作廢']);
  await verify(page, state);
});
