import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use an isolated local origin');
const base = target.origin;
const profile = { legal_name: '隔離申請人', birth_date: '1995-01-01', gender: '自訂舊值', occupation: '自訂舊職業', place_of_origin: '自訂舊籍貫', phone: '0900000000', address: '隔離測試地址', emergency_contact: '隔離聯絡人', consent_version: 'membership-data-v1' };
const order = { id: 'order-review', order_number: 'SLF-REVIEW-001', sales_channel: 'regular', order_kind: 'regular', fulfillment_method: 'cooperative_pickup', amount_total: 100, tax_amount: 5, payment_status: 'paid', fulfillment_status: 'pending_confirmation', invoice_status: 'pending', created_at: '2026-10-16T01:00:00Z', contact_email: 'buyer@example.com', available_actions: [], items: [{ product_name: '隔離商品', unit_label: '份', quantity: 1, unit_price: 100, subtotal: 100, tax_type: 'taxable' }], cancellation_request: { id: 'request-review', order_id: 'order-review', status: 'pending', reason: '行程變更', requested_at: '2026-10-16T02:00:00Z' } };
async function fixture(t, width) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { writes: [], unknown: [], errors: [], profile: { ...profile }, applicationStatus: 'approved', order: structuredClone(order) };
  await context.route('**/*', (route) => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (url.origin !== base) { if (!['https://fonts.googleapis.com', 'https://fonts.gstatic.com'].includes(url.origin)) state.unknown.push(url.origin); return route.abort(); }
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'buyer', user_role: 'admin', display_name: '隔離測試', email: 'buyer@example.com', membership_type: 'nonmember' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '' } });
    if (path === '/v1/notifications') return route.fulfill({ json: [] });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'nonmember', membership: { id: 'removed', status: 'suspended', member_number: 'ORIGINAL-001' } } });
    if (path === '/v1/membership/application') {
      if (req.method() === 'PUT') { state.profile = req.postDataJSON(); state.applicationStatus = 'draft'; state.writes.push({ path, body: state.profile }); }
      return route.fulfill({ json: { id: 'application', profile: state.profile, status: state.applicationStatus } });
    }
    if (path === '/v1/membership/application/submit') { state.applicationStatus = 'submitted'; state.writes.push({ path }); return route.fulfill({ json: { id: 'application', profile: state.profile, status: state.applicationStatus } }); }
    if (path === '/v1/admin/order-search') return route.fulfill({ json: { items: [state.order], total: 1, offset: 0, limit: 12 } });
    if (path === '/v1/orders/order-review/admin/cancellation-requests/request-review/review') {
      const body = req.postDataJSON(); state.writes.push({ path, body });
      state.order.cancellation_request.status = body.decision === 'approve' ? 'approved' : 'rejected';
      state.order.cancellation_request.review_reason = body.reason;
      if (body.decision === 'approve') { state.order.payment_status = 'refund_pending'; state.order.fulfillment_status = 'cancelled'; }
      return route.fulfill({ json: state.order });
    }
    state.unknown.push(`${req.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '隔離測試未定義請求' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  return { page, state };
}
async function layout(page, state) {
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}
for (const width of [1440, 816, 390]) {
  test(`${width}px 移除社員重新申請、其他固定選項與下拉箭頭邊界`, async t => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/membership`);
    await page.getByText('重新申請', { exact: true }).waitFor();
    const fields = page.locator('.membership-form .field').filter({ has: page.locator('select') });
    assert.equal(await fields.count(), 3);
    for (const field of await fields.all()) {
      const select = field.locator('select');
      await select.selectOption('__other');
      assert.equal(await field.locator('input').count(), 0);
      assert.equal(await select.locator('option:checked').textContent().then(s => s.includes('請填寫')), false);
      const inset = await select.evaluate(el => { const s = getComputedStyle(el); return { appearance: s.appearance, position: s.backgroundPosition, padding: parseFloat(s.paddingRight), width: el.getBoundingClientRect().width }; });
      assert.equal(inset.appearance, 'none'); assert.ok(inset.padding >= 44); assert.ok(inset.position.includes('16px')); assert.ok(inset.width > 44);
    }
    await page.getByRole('button', { name: '儲存基本資料', exact: true }).click();
    await page.getByText('基本資料已儲存。', { exact: true }).waitFor();
    assert.equal(state.writes[0].body.gender, '其他'); assert.equal(state.writes[0].body.place_of_origin, '其他／國外'); assert.equal(state.writes[0].body.occupation, '其他');
    await page.getByRole('button', { name: '放大', exact: true }).click(); await layout(page, state);
    await page.getByRole('button', { name: '送出申請', exact: true }).click();
    await page.getByText('合作社正在審核', { exact: true }).waitFor();
    assert.equal(state.writes.length, 2); await layout(page, state);
  });
  test(`${width}px 後台顯示待審理由與核准退款，只有明確操作才送出審核`, async t => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/admin?section=orders`);
    const review = page.getByRole('region', { name: '退單審核', exact: true });
    await review.getByRole('heading', { name: '退單待審核', exact: true }).waitFor();
    assert.deepEqual(state.writes, []);
    assert.equal(await page.getByRole('checkbox', { name: '只看退單待審', exact: true }).count(), 0);
    await page.locator('summary[aria-label="文字大小"]').click();
    await page.getByRole('group', { name: '文字大小', exact: true }).getByRole('button', { name: '放大', exact: true }).click();
    await layout(page, state);
    await review.getByRole('button', { name: /核准退單並退款/ }).click();
    await review.getByRole('heading', { name: '退單已核准', exact: true }).waitFor();
    assert.equal(state.writes.length, 1); assert.equal(state.writes[0].body.decision, 'approve');
    await page.locator('.operation-state').getByText('退款處理中', { exact: true }).waitFor();
    await layout(page, state);
  });
}
