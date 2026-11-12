import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4189');
if (!['localhost', '127.0.0.1'].includes(testUrl.hostname) || testUrl.pathname !== '/' || testUrl.username || testUrl.password) throw new Error('Use an isolated local origin');
const base = testUrl.origin;
const entry = { member_number: 'CSV-001', legal_name: '隔離社員', email: 'member@example.com', phone: '0912345678', share_capital_amount: 1000, share_count: 1 };
const preview = { rows: [{ row_number: 2, member_number: entry.member_number, legal_name: entry.legal_name, entry, errors: [] }], valid_count: 1, error_count: 0 };
const invoiceQueue = [
  { id: 'meal-date', order_number: 'MEAL-DATE', sales_channel: 'meal_preorder', meal_booking_type: 'business',
    payment_status: 'paid', invoice_status: 'issued', invoice_followup_required: true, invoice_followup_reason: 'business_date' },
  { id: 'issue-check', order_number: 'ISSUE-CHECK', sales_channel: 'regular', payment_status: 'paid',
    invoice_status: 'pending', invoice_followup_reason: 'issuance' },
  { id: 'legacy-void', order_number: 'LEGACY-VOID', sales_channel: 'regular', payment_status: 'refunded',
    invoice_status: 'void_pending', invoice_followup_reason: 'void',
    invoice: { provider: 'ecpay', status: 'void_pending', invoice_number: 'CC12345678' } },
  { id: 'fanyu-void', order_number: 'FANYU-VOID', sales_channel: 'regular', payment_status: 'refunded',
    invoice_status: 'void_pending', invoice_followup_reason: 'void',
    invoice: { provider: 'fanyu', status: 'void_pending', relate_number: 'FANYU-ORIGINAL' } },
].map(row => ({
  ...row, customer_name: '隔離客人', created_at: '2030-01-01T00:00:00Z',
  amount_total: 120, tax_amount: 0, items: [], available_actions: [],
  fulfillment_method: 'cooperative_pickup', fulfillment_status: 'pending_confirmation',
}));
const pointsUrl = '/v1/admin/points-ledger';
const pointEntries = [
  { user_id: 'member-user', member_number: 'CSV-001', name: '測試社員', email: 'member@example.com', balance: 9 },
  { user_id: 'other-user', member_number: 'CSV-001A', name: '相似編號社員', email: 'other@example.com', balance: 15 },
];

async function fixture(t, width, { invalid = false, role = 'admin', legacyPoints = false } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block' });
  const state = { writes: [], reads: [], unknown: [], errors: [], conflict: false, denyRoster: false, denyExport: false, exportGate: null };
  state.demandRequests = [];
  const adjustments = new Map();
  state.pointBalance = 9;
  state.pointAdjustments = adjustments;
  const ledgerMember = { id: 'membership-one', source: 'membership', member_number: entry.member_number, legal_name: entry.legal_name, email: entry.email, status: 'active' };
  let receipt;
  const emptyLists = ['/v1/products', '/v1/group-campaigns', '/v1/meal-events/calendar', '/v1/admin/suppliers', '/v1/admin/pickup-locations', '/v1/admin/activities', '/v1/admin/member-proposals', '/v1/admin/membership-applications', '/v1/admin/activity-refund-requests', '/v1/wishes', '/v1/meetings', '/v1/admin/meetings', '/v1/notifications'];
  await context.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== base) return route.abort();
    if (!url.pathname.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    const path = url.pathname;
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated-csv-test', user: { id: 'test-user', email: 'user@example.com', display_name: '隔離測試帳號', user_role: role } } });
    if (path === '/v1/admin/reconciliation-status') return route.fulfill({ json: { current: null, last_success: null, long_running: false, stale_after_seconds: 300, backlog: [] } });
    const adjustmentPath = `${pointsUrl}/member-user/adjustments`;
    if (state.allowPointAdjustments && path === adjustmentPath && request.method() === 'POST') {
      const body = request.postDataJSON();
      state.writes.push({ path, body });
      const known = adjustments.get(body.request_id);
      if (known) return route.fulfill({ status: 201, json: known });
      if (body.expected_balance !== state.pointBalance) {
        return route.fulfill({ status: 409, headers: { 'X-Error-Code': 'point_balance_changed' }, json: { detail: '點數餘額已變更，請重新預覽' } });
      }
      const record = { id: body.request_id, user_id: 'member-user', member_number: 'CSV-001', amount: body.amount,
        balance_before: state.pointBalance, balance_after: state.pointBalance + body.amount };
      state.pointBalance = record.balance_after;
      adjustments.set(body.request_id, record);
      if (state.dropAdjustmentResponse) {
        state.dropAdjustmentResponse = false;
        return route.abort('failed');
      }
      return route.fulfill({ status: 201, json: record });
    }
    if (state.allowPointAdjustments && path.startsWith(`${adjustmentPath}/`) && request.method() === 'GET') {
      state.reads.push(path);
      const record = adjustments.get(path.split('/').at(-1));
      return route.fulfill(record ? { json: record } : { status: 404, json: { detail: '尚未找到這筆點數調整' } });
    }
    if (request.method() === 'GET') {
      state.reads.push(path);
      if (path === '/v1/admin/suppliers' && state.showSuppliers) return route.fulfill({ json: [
        { id: 'supplier-1', business_name: '隔離供應者', is_active: true, accreditations: [], documents: [] },
      ] });
      if (path === '/v1/admin/supplier-order-demand.csv') {
        state.demandRequests.push(Object.fromEntries(url.searchParams));
        if (state.denyExport) return route.fulfill({ status: 403, json: { detail: '需要管理員權限' } });
        return route.fulfill({ contentType: 'text/csv', body: '\ufeff商品,數量\n白米,2' });
      }
      if (emptyLists.includes(path)) return route.fulfill({ json: [] });
      if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, closed_message: '', sales_scope: 'all' } });
      if (path === '/v1/admin/membership/admissions') return route.fulfill({ json: { items: [], total: 0, limit: 25, offset: 0 } });
      if (path === '/v1/admin/users') return route.fulfill({ json: { items: [{
        id: 'member-user', membership_id: 'membership-one', display_name: '測試社員', email: 'member@example.com',
        is_active: true, membership_status: 'active', member_number: 'CSV-001', created_at: '2030-01-01T00:00:00Z',
      }, {
        id: 'trainee-user', membership_id: 'membership-trainee', display_name: '測試實習社員', email: 'trainee@example.com',
        is_active: true, membership_status: 'trainee', created_at: '2030-01-01T00:00:00Z',
      }], total: 2 } });
      if (path === '/v1/admin/members') return route.fulfill({ json: [
        { id: 'membership-one', user_id: 'member-user', member_number: 'CSV-001', display_name: '測試社員', status: 'active', share_capital_amount: 1000, share_count: 1, updated_at: '2030-01-01T00:00:00Z' },
        { id: 'membership-one-more', user_id: 'other-user', member_number: 'CSV-001A', display_name: '相似編號社員', status: 'active', share_capital_amount: 1000, share_count: 1, updated_at: '2030-01-01T00:00:00Z' },
        { id: 'membership-trainee', user_id: 'trainee-user', trainee_number: 'T-001', display_name: '測試實習社員', status: 'trainee', share_capital_amount: 0, share_count: 0, updated_at: '2030-01-01T00:00:00Z' },
      ] });
      if (path === '/v1/admin/member-roster' && state.denyRoster) return route.fulfill({ status: 403, json: { detail: '需要管理員權限' } });
      if (path === '/v1/admin/member-roster') return route.fulfill({ json: [
        { id: 'roster-one', member_number: 'CSV-001', legal_name: '測試社員', email_masked: 'm***@example.com', phone_masked: '******5678', claimed: true, is_active: true, share_capital_amount: 1000, share_count: 1, updated_at: '2030-01-01T00:00:00Z' },
        { id: 'roster-two', member_number: 'CSV-001A', legal_name: '相似編號社員', email_masked: 'o***@example.com', claimed: false, is_active: true, share_capital_amount: 1000, share_count: 1, updated_at: '2030-01-01T00:00:00Z' },
      ] });
      if (path === '/v1/admin/member-register') return route.fulfill({ json: { items: [ledgerMember], total: 1 } });
      if ([pointsUrl, `${pointsUrl}.csv`, `${pointsUrl}/filtered.csv`].includes(path)) {
        if (path === `${pointsUrl}/filtered.csv` && legacyPoints) return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
        const member = url.searchParams.get('member_number');
        const q = path === `${pointsUrl}.csv` || legacyPoints ? '' : url.searchParams.get('q') || '';
        const items = pointEntries.filter(row => (!member || row.member_number === member) &&
          `${row.member_number} ${row.name} ${row.email}`.toLowerCase().includes(q.toLowerCase()))
          .map(row => state.allowPointAdjustments && row.user_id === 'member-user'
            ? { ...row, can_adjust: true, balance: state.pointBalance } : row);
        if (path === pointsUrl) return route.fulfill({ json: { items, total: items.length, ...(legacyPoints ? {} : { applied_query: q }) } });
        if (state.denyExport) return route.fulfill({ status: 403, json: { detail: '需要管理員權限' } });
        const csv = ['社員編號,姓名,Email,累積點數', ...items.map(row => [row.member_number, row.name, row.email, row.balance].join(','))].join('\n');
        return route.fulfill({ contentType: 'text/csv; charset=utf-8', body: csv });
      }
      if (path === '/v1/admin/membership-payments') {
        const received = receipt?.status === 'paid' ? receipt.received : 0;
        return route.fulfill({ json: { items: receipt ? [receipt] : [], total: receipt ? 1 : 0, summary: { received, refunded: 0, net: received } } });
      }
      if (path === '/v1/admin/membership-payments.csv') {
        if (state.denyExport) return route.fulfill({ status: 403, json: { detail: '需要管理員權限' } });
        const sendCsv = () => route.fulfill({ contentType: 'text/csv; charset=utf-8', body: '社員編號\nCSV-001\n' });
        return state.exportGate ? state.exportGate.then(sendCsv) : sendCsv();
      }
      if (path === '/v1/admin/printers/status') return route.fulfill({ json: { enabled: false, dry_run: true, status: 'disabled', error: null } });
      if (path === '/v1/admin/printers/jobs') return route.fulfill({ json: { items: [], total: 0, limit: 30, offset: 0 } });
      if (path === '/v1/admin/platform') return route.fulfill({ json: { kitchen_sound: false } });
      if (path === '/v1/admin/today') return route.fulfill({ json: {
        pending_admissions: 2, pending_order_cancellations: 3, unpaid_business_orders: 4,
        pending_activity_refunds: 1, active_activities: 2, active_activity_registrations: 7,
        active_campaigns: 3, pending_campaign_decisions: 1, voting_group_proposals: 2,
        voting_member_proposals: 2, invoice_followups: invoiceQueue.length,
      } });
      if (path === '/v1/admin/order-search') {
        state.orderFilters = Object.fromEntries(url.searchParams);
        const items = state.orderFilters.invoice_followup === 'true'
          ? invoiceQueue.filter(row => state.orderFilters.order_category !== 'general' || row.sales_channel !== 'meal_preorder')
          : [];
        return route.fulfill({ json: { items, total: items.length, limit: 12, offset: 0 } });
      }
      if (path === '/v1/admin/failed-jobs') return route.fulfill({ json: { items: [], total: 0, limit: 12, offset: 0 } });
      if (path === '/v1/admin/meal-kitchen') return route.fulfill({ json: { items: [], total: 0, meal_quantity: 0, products: [] } });
      if (path === '/v1/admin/finance/meal-sales') return route.fulfill({ json: { summary: { received: 0, refunded: 0, net: 0, receipt_count: 0, refund_count: 0, paid_meal_quantity: 0, pending_refund_amount: 0 }, orders: { items: [], total: 0 }, daily: [], weekly: [], monthly: [], products: [] } });
    }
    if (request.method() === 'POST' && path === '/v1/admin/member-roster/import/preview') {
      state.writes.push({ path, contentType: request.headers()['content-type'], content: request.postData() });
      return route.fulfill({ json: invalid ? { rows: [{ ...preview.rows[0], entry: null, errors: ['社員編號已存在於名冊或會籍'] }], valid_count: 0, error_count: 1 } : preview });
    }
    if (request.method() === 'POST' && path === '/v1/admin/member-roster/import') {
      state.writes.push({ path, body: request.postDataJSON() });
      return state.conflict ? route.fulfill({ status: 409, json: { detail: '社員編號已存在，請重新選擇 CSV 檢查' } }) : route.fulfill({ status: 201, json: { imported_count: 1 } });
    }
    if (request.method() === 'POST' && path === '/v1/admin/membership-offline-receipts') {
      const body = request.postDataJSON();
      state.writes.push({ path, body });
      receipt = { ...body, id: 'receipt-one', source: 'offline', member_number: entry.member_number, name: entry.legal_name, received: body.amount, refunded: 0, net: body.amount, status: 'paid', receipt_number: 'MRC-ISOLATED' };
      return route.fulfill({ status: 201, json: receipt });
    }
    if (request.method() === 'POST' && path === '/v1/admin/membership-offline-receipts/receipt-one/void' && receipt) {
      state.writes.push({ path, body: request.postDataJSON() });
      receipt = { ...receipt, status: 'voided', received: 0, net: 0 };
      return route.fulfill({ json: receipt });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => state.errors.push(error.message));
  page.setDefaultTimeout(10000);
  return { page, state };
}

for (const recover of ['lookup', 'retry']) test(`點數調整遺失回應後重整，依原社員與識別恢復單筆入帳 ${recover}`, async t => {
  const { page, state } = await fixture(t, 390);
  state.allowPointAdjustments = true;
  state.dropAdjustmentResponse = true;
  await page.goto(`${base}/admin/users?tab=records&ledger=points`);
  const open = page.getByRole('button', { name: '調整點數', exact: true });
  await open.click();
  const panel = page.getByRole('region', { name: '調整社員點數' });
  await panel.getByText('測試社員 · 社員編號 CSV-001', { exact: true }).waitFor();
  await panel.getByLabel('增減點數').fill('+7');
  await panel.getByLabel('調整原因').fill('僅合成驗證原因');
  await panel.getByRole('button', { name: '預覽調整' }).click();
  await panel.getByText('+7', { exact: true }).waitFor();
  assert.deepEqual(state.writes, []);
  await panel.getByRole('button', { name: '確認調整' }).click();
  await panel.getByText('尚未確認入帳，請查詢入帳結果或重試原調整。', { exact: true }).waitFor();
  assert.equal(state.pointBalance, 16);
  assert.equal(await page.evaluate(() => Object.values(sessionStorage).some(value => typeof value === 'string' && value.includes('僅合成驗證原因'))), false);
  await page.reload();
  await open.click();
  await panel.getByText('這筆調整的入帳結果待確認。', { exact: true }).waitFor();
  assert.equal(await panel.getByRole('button', { name: '重試原調整' }).isDisabled(), true);
  if (recover === 'lookup') {
    await panel.getByRole('button', { name: '查詢入帳結果' }).click();
  } else {
    await panel.getByLabel('原調整原因').fill('不同的內容');
    await panel.getByRole('button', { name: '重試原調整' }).click();
    await panel.getByText('請輸入與原提交相同的調整原因。', { exact: true }).waitFor();
    assert.equal(state.writes.length, 1);
    await panel.getByLabel('原調整原因').fill('僅合成驗證原因');
    await panel.getByRole('button', { name: '重試原調整' }).click();
  }
  await page.getByText('社員 CSV-001 已調整 +7 點。', { exact: true }).waitFor();
  assert.equal(state.writes.length, recover === 'lookup' ? 1 : 2);
  assert.equal(state.pointAdjustments.size, 1);
  if (recover === 'lookup') assert.ok(state.reads.includes(`${pointsUrl}/member-user/adjustments/${state.writes[0].body.request_id}`));
  else assert.deepEqual(state.writes[0].body, state.writes[1].body);
  assert.equal(await page.getByRole('row', { name: /測試社員/ }).getByText('16', { exact: true }).count(), 1);
  await noOverflow(page);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

test('點數扣減遇到餘額變更時需重新預覽後才可確認', async t => {
  const { page, state } = await fixture(t, 1280);
  state.allowPointAdjustments = true;
  await page.goto(`${base}/admin/users?tab=records&ledger=points&member=CSV-001`);
  await page.getByRole('button', { name: '調整點數', exact: true }).click();
  const panel = page.getByRole('region', { name: '調整社員點數' });
  await panel.getByLabel('增減點數').fill('-15');
  await panel.getByRole('button', { name: '預覽調整' }).click();
  state.pointBalance = 10;
  await panel.getByRole('button', { name: '確認調整' }).click();
  await panel.getByText('點數餘額已變更，請重新預覽', { exact: true }).waitFor();
  await page.getByRole('row', { name: /測試社員/ }).getByText('10', { exact: true }).waitFor();
  await panel.getByRole('button', { name: '預覽調整' }).click();
  await panel.getByText('-5', { exact: true }).waitFor();
  await panel.getByRole('button', { name: '確認調整' }).click();
  await page.getByText('社員 CSV-001 已調整 -15 點。', { exact: true }).waitFor();
  assert.deepEqual(state.writes.map(row => row.body.expected_balance), [9, 10]);
  assert.notEqual(state.writes[0].body.request_id, state.writes[1].body.request_id);
  assert.equal(state.pointBalance, -5);
  assert.equal(state.pointAdjustments.size, 1);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

test('願望管理舊連結轉到商品，會員管理開啟指定社員的會籍、名冊與帳冊', async t => {
  const { page, state } = await fixture(t, 390);
  await page.goto(`${base}/admin/social?tab=wishes`);
  await page.waitForURL('**/admin/groups?tab=wishes');
  await page.getByRole('heading', { name: '社員願望', exact: true }).waitFor();
  assert.equal(state.reads.includes('/v1/wishes'), true);
  await page.goto(`${base}/admin/users`);
  const memberLinks = page.getByRole('navigation', { name: '測試社員的社員資料', exact: true });
  await memberLinks.waitFor();
  const nav = page.getByRole('navigation', { name: '會員社務功能', exact: true });
  assert.equal(await nav.getByRole('link', { name: '會員管理', exact: true }).count(), 1);
  assert.equal(await nav.getByRole('link', { name: '社員帳冊', exact: true }).count(), 0);
  assert.equal(state.reads.some(path => ['/v1/admin/members', '/v1/admin/member-roster', '/v1/admin/member-register'].includes(path)), false);
  const lookup = page.getByLabel('姓名、Email、會員或社員編號', { exact: true });
  await lookup.fill('CSV-001');
  const filtered = page.waitForRequest(req => {
    const url = new URL(req.url());
    return url.pathname === '/v1/admin/users' && url.searchParams.get('search') === 'CSV-001';
  });
  await page.getByRole('button', { name: '查詢會員', exact: true }).click();
  await filtered;
  await memberLinks.getByRole('link', { name: '會籍異動', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/admin/users' && url.searchParams.get('tab') === 'members' && url.searchParams.get('membership') === 'membership-one');
  await page.getByRole('heading', { name: '測試社員 · CSV-001', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: /相似編號社員/ }).count(), 0);
  await page.goBack();
  assert.equal(await lookup.inputValue(), 'CSV-001');
  await memberLinks.getByRole('link', { name: '社員名冊', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/admin/users' && url.searchParams.get('tab') === 'roster' && url.searchParams.get('member') === 'CSV-001');
  await page.getByRole('heading', { name: 'CSV-001 · 測試社員', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: /相似編號社員/ }).count(), 0);
  const workspace = page.getByRole('navigation', { name: '會員管理分類', exact: true });
  await workspace.getByRole('link', { name: '會籍管理', exact: true }).click();
  await page.waitForURL(url => url.searchParams.get('tab') === 'members' && url.searchParams.get('membership') === 'membership-one' && url.searchParams.get('member') === 'CSV-001');
  await page.getByRole('heading', { name: '測試社員 · CSV-001', exact: true }).waitFor();
  await workspace.getByRole('link', { name: '註冊帳號', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/admin/users' && !url.searchParams.has('member') && !url.searchParams.has('membership'));
  const register = page.waitForRequest(req => new URL(req.url()).pathname === '/v1/admin/member-register');
  await memberLinks.getByRole('link', { name: '社員帳冊', exact: true }).click();
  await page.getByRole('heading', { name: '社員與收款紀錄', exact: true }).waitFor();
  await page.getByText('社員編號 CSV-001', { exact: true }).waitFor();
  assert.equal(await workspace.getByRole('link', { name: '社員帳冊', exact: true }).getAttribute('aria-current'), 'page');
  assert.equal(new URL((await register).url()).searchParams.get('member_number'), 'CSV-001');
  for (const [name, path] of [
    ['入社費與股金', '/v1/admin/membership-payments'], ['累積點數', '/v1/admin/points-ledger'],
  ]) {
    const request = page.waitForRequest(req => new URL(req.url()).pathname === path);
    await page.getByRole('button', { name, exact: true }).click();
    assert.equal(new URL((await request).url()).searchParams.get('member_number'), 'CSV-001');
  }
  const shares = page.getByRole('button', { name: '社員股籍', exact: true });
  await shares.click();
  await page.getByText('社員編號 CSV-001', { exact: true }).waitFor();
  assert.equal(await shares.getAttribute('aria-pressed'), 'true');
  await noOverflow(page);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

test('供應者叫貨CSV採明確期間與供應者，歷史資料另行核對', async t => {
  const { page, state } = await fixture(t, 390);
  state.showSuppliers = true;
  await page.goto(`${base}/admin/catalog?tab=suppliers`);
  await page.getByText('供應者叫貨單', { exact: true }).click();
  await page.getByLabel('付款日期起').fill('2030-01-03');
  await page.getByLabel('付款日期迄').fill('2030-01-03');
  const supplierExport = page.getByRole('button', { name: '下載叫貨參考 CSV', exact: true });
  assert.equal(await supplierExport.isDisabled(), true);
  const [review] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '下載待核對 CSV', exact: true }).click(),
  ]);
  assert.equal(review.suggestedFilename(), '叫貨待核對-2030-01-03-2030-01-03.csv');
  await page.getByRole('combobox', { name: '供應者', exact: true }).selectOption('supplier-1');
  await page.getByLabel('預計到貨日').fill('2030-01-05');
  const [download] = await Promise.all([page.waitForEvent('download'), supplierExport.click()]);
  assert.equal(download.suggestedFilename(), '叫貨參考-2030-01-03-2030-01-03.csv');
  state.denyExport = true;
  await supplierExport.click();
  await page.getByText('需要管理員權限', { exact: true }).waitFor();
  assert.equal(await supplierExport.count(), 0);
  assert.deepEqual(state.demandRequests, [
    { starts_on: '2030-01-03', ends_on: '2030-01-03', scope: 'review' },
    { starts_on: '2030-01-03', ends_on: '2030-01-03', scope: 'supplier', supplier_id: 'supplier-1', expected_arrival_on: '2030-01-05' },
    { starts_on: '2030-01-03', ends_on: '2030-01-03', scope: 'supplier', supplier_id: 'supplier-1', expected_arrival_on: '2030-01-05' },
  ]);
  await noOverflow(page);
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

test('社員帳冊舊連結保留款項分類、社員與匯出範圍', async t => {
  const { page, state } = await fixture(t, 390);
  const payments = page.waitForRequest(req => new URL(req.url()).pathname === '/v1/admin/membership-payments');
  await page.goto(`${base}/admin/records?tab=payments&member=CSV-001`);
  await page.waitForURL(url => url.pathname === '/admin/users' && url.searchParams.get('tab') === 'records' && url.searchParams.get('ledger') === 'payments' && url.searchParams.get('member') === 'CSV-001');
  assert.equal(new URL((await payments).url()).searchParams.get('member_number'), 'CSV-001');
  const exportRequest = page.waitForRequest(req => new URL(req.url()).pathname === '/v1/admin/membership-payments.csv');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '匯出 CSV', exact: true }).click();
  assert.equal(new URL((await exportRequest).url()).searchParams.get('member_number'), 'CSV-001');
  assert.equal((await download).suggestedFilename(), '入社收款紀錄.csv');
  assert.equal(state.reads.some(path => ['/v1/admin/users', '/v1/admin/members', '/v1/admin/member-roster'].includes(path)), false);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

test('點數清單與 CSV 共用查詢和精確社員範圍，匯出失權時停止', async t => {
  const { page, state } = await fixture(t, 390);
  const ledgerPage = `${base}/admin/users?tab=records&ledger=points`;
  const search = page.getByLabel('姓名、Email 或社員編號', { exact: true });
  const submit = page.getByRole('button', { name: '查詢', exact: true });
  const exportCsv = page.getByRole('button', { name: '匯出 CSV', exact: true });
  const first = page.getByRole('cell', { name: 'member@example.com', exact: true });
  const second = page.getByRole('cell', { name: 'other@example.com', exact: true });
  const requestFilters = (pathname, q) => page.waitForRequest(req => {
    const url = new URL(req.url());
    return url.pathname === pathname && url.searchParams.get('q') === q;
  });

  await page.goto(ledgerPage);
  await second.waitFor();
  await search.fill('  MEMBER@EXAMPLE.COM ');
  const filteredList = requestFilters(pointsUrl, 'MEMBER@EXAMPLE.COM');
  await submit.click();
  await filteredList;
  await page.getByText('共 1 筆紀錄', { exact: true }).waitFor();
  await first.waitFor();
  assert.equal(await second.count(), 0);
  const filteredDownload = requestFilters(`${pointsUrl}/filtered.csv`, 'MEMBER@EXAMPLE.COM');
  const download = page.waitForEvent('download');
  await exportCsv.click();
  await filteredDownload;
  const result = await download;
  assert.equal(result.suggestedFilename(), '社員點數.csv');
  const contents = await readFile(await result.path(), 'utf8');
  assert.match(contents, /member@example\.com/);
  assert.doesNotMatch(contents, /other@example\.com/);
  assert.equal(state.reads.includes(`${pointsUrl}.csv`), false);

  await page.goto(`${ledgerPage}&member=CSV-001`);
  await first.waitFor();
  assert.equal(await second.count(), 0);
  await search.fill('other@example.com');
  const scopedList = requestFilters(pointsUrl, 'other@example.com');
  await submit.click();
  assert.equal(new URL((await scopedList).url()).searchParams.get('member_number'), 'CSV-001');
  await page.getByText('共 0 筆紀錄', { exact: true }).waitFor();
  const scopedDownload = requestFilters(`${pointsUrl}/filtered.csv`, 'other@example.com');
  const emptyDownload = page.waitForEvent('download');
  await exportCsv.click();
  assert.equal(new URL((await scopedDownload).url()).searchParams.get('member_number'), 'CSV-001');
  assert.equal(await readFile(await (await emptyDownload).path(), 'utf8'), '社員編號,姓名,Email,累積點數');

  state.denyExport = true;
  await exportCsv.click();
  await page.getByText('管理權限已失效，請重新驗證。', { exact: true }).waitFor();
  assert.equal(await exportCsv.count(), 0);
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
  await noOverflow(page);
});

test('舊 API 仍能搜尋點數，搜尋匯出失敗時不會下載全部社員', async t => {
  const { page, state } = await fixture(t, 1280, { legacyPoints: true });
  await page.goto(`${base}/admin/users?tab=records&ledger=points`);
  await page.getByText('共 2 筆紀錄', { exact: true }).waitFor();
  const search = page.getByLabel('姓名、Email 或社員編號', { exact: true });
  const submit = page.getByRole('button', { name: '查詢', exact: true });
  const exportCsv = page.getByRole('button', { name: '匯出 CSV', exact: true });
  await search.fill('other@example.com');
  await submit.click();
  await page.getByText('共 1 筆紀錄', { exact: true }).waitFor();
  assert.equal(await page.getByRole('cell', { name: 'member@example.com', exact: true }).count(), 0);
  const downloads = [];
  page.on('download', file => downloads.push(file));
  await exportCsv.click();
  await page.getByRole('alert').getByText('點數搜尋匯出暫時無法使用，請稍後重試。', { exact: true }).waitFor();
  assert.deepEqual(state.reads.filter(path => path.includes('.csv')), [`${pointsUrl}/filtered.csv`]);
  assert.deepEqual(downloads, []);
  await search.clear();
  await submit.click();
  await page.getByText('共 2 筆紀錄', { exact: true }).waitFor();
  const explicitlyUnfiltered = page.waitForEvent('download');
  await exportCsv.click();
  assert.equal((await explicitlyUnfiltered).suggestedFilename(), '社員點數.csv');
  assert.deepEqual(state.reads.filter(path => path.includes('.csv')), [`${pointsUrl}/filtered.csv`, `${pointsUrl}.csv`]);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

test('會籍舊網址精確解析 ID 並保留社員範圍，原有文字搜尋仍保留', async t => {
  const { page, state } = await fixture(t, 390);
  await page.goto(`${base}/admin/social?tab=members&q=membership-one`);
  const urlHasLegacyQuery = url => url.pathname === '/admin/users' && url.searchParams.get('tab') === 'members' && url.searchParams.get('q') === 'membership-one';
  await page.waitForURL(urlHasLegacyQuery);
  await page.getByRole('heading', { name: '測試社員 · CSV-001', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: /相似編號社員/ }).count(), 0);
  const workspace = page.getByRole('navigation', { name: '會員管理分類', exact: true });
  const scopedRegister = page.waitForRequest(req => new URL(req.url()).pathname === '/v1/admin/member-register');
  await workspace.getByRole('link', { name: '社員帳冊', exact: true }).click();
  assert.equal(new URL((await scopedRegister).url()).searchParams.get('member_number'), 'CSV-001');
  await page.waitForURL(url => url.searchParams.get('membership') === 'membership-one' && url.searchParams.get('member') === 'CSV-001');
  await page.goBack();
  await page.waitForURL(urlHasLegacyQuery);
  await workspace.getByRole('link', { name: '社員名冊', exact: true }).click();
  await page.getByRole('heading', { name: 'CSV-001 · 測試社員', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: /相似編號社員/ }).count(), 0);
  await page.goto(`${base}/admin/social?tab=members&q=CSV-001`);
  await page.getByRole('heading', { name: '測試社員 · CSV-001', exact: true }).waitFor();
  await page.getByRole('heading', { name: '相似編號社員 · CSV-001A', exact: true }).waitFor();
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

test('沒有社員編號的會籍跨頁與重整不讀取全部名冊、帳冊或 CSV', async t => {
  const { page, state } = await fixture(t, 390);
  await page.goto(`${base}/admin/users`);
  const links = page.getByRole('navigation', { name: '測試實習社員的社員資料', exact: true });
  await links.getByRole('link', { name: '會籍異動', exact: true }).click();
  const scopeUrl = (url, tab) => url.pathname === '/admin/users' && url.searchParams.get('tab') === tab && url.searchParams.get('membership') === 'membership-trainee' && !url.searchParams.has('member');
  await page.waitForURL(url => scopeUrl(url, 'members'));
  await page.getByRole('heading', { name: '測試實習社員 · T-001', exact: true }).waitFor();
  const workspace = page.getByRole('navigation', { name: '會員管理分類', exact: true });
  await workspace.getByRole('link', { name: '社員帳冊', exact: true }).click();
  await page.waitForURL(url => scopeUrl(url, 'records'));
  await page.getByRole('heading', { name: '尚未選定社員編號', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '匯出 CSV', exact: true }).count(), 0);
  assert.equal(state.reads.some(path => ['/v1/admin/member-register', '/v1/admin/membership-payments', '/v1/admin/points-ledger'].includes(path)), false);
  await page.goBack();
  await page.waitForURL(url => scopeUrl(url, 'members'));
  await page.getByRole('heading', { name: '測試實習社員 · T-001', exact: true }).waitFor();
  assert.equal(state.reads.includes('/v1/admin/member-roster'), false);
  await workspace.getByRole('link', { name: '社員名冊', exact: true }).click();
  await page.waitForURL(url => scopeUrl(url, 'roster'));
  await page.getByRole('heading', { name: '尚未選定社員編號', exact: true }).waitFor();
  await page.reload();
  await page.getByRole('heading', { name: '尚未選定社員編號', exact: true }).waitFor();
  assert.equal(state.reads.includes('/v1/admin/member-roster'), false);
  assert.equal(await page.getByRole('heading', { name: '匯入社員名冊', exact: true }).count(), 0);
  await page.getByRole('link', { name: '查看全部名冊', exact: true }).click();
  await page.getByRole('heading', { name: '匯入社員名冊', exact: true }).waitFor();
  assert.equal(new URL(page.url()).searchParams.has('membership'), false);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

for (const deniedWhileWaiting of [false, true]) {
  test(deniedWhileWaiting ? '帳冊匯出等待時失去權限，已啟動的回應不下載' : '帳冊匯出403隱藏會員工作區並停止操作', async t => {
    const { page, state } = await fixture(t, 390);
    await page.goto(`${base}/admin/users?tab=records&ledger=payments&member=CSV-001`);
    const downloads = [];
    page.on('download', download => downloads.push(download));
    let releaseExport;
    if (deniedWhileWaiting) state.exportGate = new Promise(resolve => { releaseExport = resolve; });
    else state.denyExport = true;
    const csvPath = '/v1/admin/membership-payments.csv';
    const exportRequest = page.waitForRequest(req => new URL(req.url()).pathname === csvPath);
    const exportResponse = page.waitForResponse(response => new URL(response.url()).pathname === csvPath);
    await page.getByRole('button', { name: '匯出 CSV', exact: true }).click();
    assert.equal(new URL((await exportRequest).url()).searchParams.get('member_number'), 'CSV-001');
    if (deniedWhileWaiting) {
      state.denyRoster = true;
      await page.getByRole('navigation', { name: '會員管理分類', exact: true }).getByRole('link', { name: '社員名冊', exact: true }).click();
    }
    await page.getByRole('alert').getByText('管理權限已失效，請重新驗證。').waitFor();
    releaseExport?.();
    const response = await exportResponse;
    assert.equal(response.status(), deniedWhileWaiting ? 200 : 403);
    await response.finished();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(downloads, []);
    assert.equal(await page.getByRole('navigation', { name: '會員管理分類' }).count(), 0);
    assert.deepEqual(state.unknown, []);
    assert.deepEqual(state.errors, []);
  });
}

test('指定社員帳冊登錄和作廢線下收款後仍維持社員範圍', async t => {
  const { page, state } = await fixture(t, 390);
  await page.goto(`${base}/admin/records?member=CSV-001`);
  await page.getByRole('button', { name: '登錄線下收款', exact: true }).click();
  await page.getByLabel('實收金額', { exact: true }).fill('500');
  await page.getByLabel('收款日期（台灣時間）', { exact: true }).fill('2026-11-11T13:30');
  await page.getByLabel('已核對款項確實收到，登錄此筆收款', { exact: true }).check();
  await page.getByRole('button', { name: '儲存收款紀錄', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/admin/users' && url.searchParams.get('ledger') === 'payments' && url.searchParams.get('member') === 'CSV-001');
  await page.getByRole('cell', { name: /MRC-ISOLATED/ }).waitFor();
  const [{ path, body: { idempotency_key, ...body } }] = state.writes;
  assert.equal(path, '/v1/admin/membership-offline-receipts');
  assert.match(idempotency_key, /^[\da-f-]{36}$/);
  assert.deepEqual(body, { target_type: 'membership', target_id: 'membership-one', kind: 'admission_fee', amount: 500, payment_method: 'bank_transfer', paid_at: '2026-11-11T13:30:00+08:00', reference: '', confirmed_received: true });
  await page.getByRole('button', { name: '作廢紀錄', exact: true }).click();
  await page.getByRole('button', { name: '確認作廢紀錄', exact: true }).click();
  await page.getByRole('cell', { name: '已作廢', exact: true }).first().waitFor();
  assert.equal(new URL(page.url()).searchParams.get('member'), 'CSV-001');
  assert.deepEqual(state.writes[1], { path: '/v1/admin/membership-offline-receipts/receipt-one/void', body: { reason: '' } });
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
});

test('非管理員從舊網址進入會員管理，不讀取私人清單', async t => {
  const { page, state } = await fixture(t, 390, { role: 'customer' });
  for (const [oldPath, tab] of [['/admin/social?tab=roster', 'roster'], ['/admin/records?tab=points&member=CSV-001', 'records']]) {
    await page.goto(`${base}${oldPath}`);
    await page.waitForURL(url => url.pathname === '/admin/users' && url.searchParams.get('tab') === tab);
    await page.getByText('請使用管理員帳號登入。', { exact: true }).waitFor();
    assert.equal(await page.getByText(/CSV-001|隔離社員/).count(), 0);
  }
  assert.equal(state.reads.some(path => path.startsWith('/v1/admin/')), false);
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.errors, []);
});

test('管理員名冊讀取失權後隱藏工作區，停止查詢及操作', async t => {
  const { page, state } = await fixture(t, 390);
  await page.goto(`${base}/admin/users`);
  const memberLinks = page.getByRole('navigation', { name: '測試社員的社員資料', exact: true });
  await memberLinks.waitFor();
  state.denyRoster = true;
  await memberLinks.getByRole('link', { name: '社員名冊', exact: true }).click();
  await page.getByRole('alert').getByText('管理權限已失效，請重新驗證。').waitFor();
  assert.equal(await page.getByRole('navigation', { name: '會員管理分類' }).count(), 0);
  assert.equal(await page.getByText(/CSV-001|測試社員/).count(), 0);
  assert.equal(state.reads.filter(path => path === '/v1/admin/member-roster').length, 1);
  assert.equal(state.reads.some(path => ['/v1/admin/members', '/v1/admin/member-register'].includes(path)), false);
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
});

async function noOverflow(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, '頁面不能水平溢出');
}

async function screenshot(page, name) {
  if (process.env.BROWSER_SCREENSHOT_DIR) {
    await page.evaluate(() => Promise.all(document.getAnimations().map((animation) => animation.finished.catch(() => {}))));
    await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/${name}.png`, fullPage: true });
  }
}

for (const width of [1440, 390]) {
  test(`${width}px 今日待辦一次讀取數量，退單及發票捷徑打開跨分類篩選`, async t => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/admin`);
    await page.getByRole('heading', { name: '今日待辦', exact: true }).waitFor();
    const list = page.getByRole('list', { name: '今日待辦清單' });
    await list.getByRole('link', { name: /進行中的活動.*共 7 人次報名/ }).waitFor();
    assert.equal(await list.getByRole('link').count(), 10);
    assert.equal(state.reads.filter(path => path.startsWith('/v1/admin/')).join(','), '/v1/admin/today');
    await noOverflow(page);
    await list.getByRole('link', { name: /待審退單.*3 筆/ }).click();
    await page.getByText('沒有符合條件的訂單。').waitFor();
    assert.equal(state.orderFilters.cancellation_pending, 'true');
    assert.equal(state.orderFilters.order_category, 'all');
    await page.getByRole('link', { name: '今日待辦', exact: true }).click();
    await list.getByRole('link', { name: /發票待合作社處理.*4 筆/ }).click();
    await page.getByText('會餐發票日期待核對', { exact: true }).waitFor();
    assert.equal(state.orderFilters.invoice_followup, 'true');
    assert.equal(state.orderFilters.order_category, 'all');
    const generalInvoices = page.waitForRequest(request => {
      const url = new URL(request.url());
      return url.pathname === '/v1/admin/order-search' && url.searchParams.get('order_category') === 'general';
    });
    const category = page.getByRole('combobox', { name: '訂單分類', exact: true });
    assert.deepEqual(await category.selectOption('general'), ['general']);
    assert.equal(new URL((await generalInvoices).url()).searchParams.get('invoice_followup'), 'true');
    await page.getByText('會餐發票日期待核對', { exact: true }).waitFor({ state: 'hidden' });
    await page.getByText('開票待核對', { exact: true }).waitFor();
    const legacyVoid = page.locator('.operations-list article').filter({ hasText: 'LEGACY-VOID' });
    await legacyVoid.getByText('請至綠界科技電子發票核對並處理作廢。 發票號碼 CC12345678', { exact: true }).waitFor();
    assert.equal(await legacyVoid.getByText('發票作廢處理', { exact: true }).count(), 0);
    const fanyuVoid = page.locator('.operations-list article').filter({ hasText: 'FANYU-VOID' });
    await fanyuVoid.getByText('使用「發票作廢處理」核對汎宇結果。 銷貨單號 FANYU-ORIGINAL', { exact: true }).waitFor();
    await fanyuVoid.getByText('發票作廢處理', { exact: true }).waitFor();
    await noOverflow(page);
    assert.deepEqual(state.writes, []);
    assert.deepEqual(state.unknown, []);
    assert.deepEqual(state.errors, []);
  });

  test(`${width}px 營運選單沿用商品供應底線頁籤，功能分頁仍可操作`, { timeout: 30000 }, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/admin/catalog`);
    const reference = page.getByRole('tab', { name: '商品主檔', exact: true });
    await reference.waitFor();
    const expected = await reference.evaluate((element) => {
      const style = getComputedStyle(element);
      return { background: style.backgroundColor, border: style.borderBottomColor, font: style.fontSize };
    });
    const eyebrow = await page.locator('.workspace-heading .eyebrow').evaluate((element) => getComputedStyle(element).fontSize);
    await page.goto(`${base}/admin?section=orders`);
    const nav = page.getByRole('navigation', { name: '訂單營運子頁面' });
    const active = nav.getByRole('link', { name: '訂單、付款與發票', exact: true });
    await active.waitFor();
    assert.match(await page.getByRole('navigation', { name: '管理工作台分類' }).getByRole('link', { name: '訂餐營運', exact: true }).getAttribute('class'), /active/);
    const style = await active.evaluate((element) => {
      const style = getComputedStyle(element);
      return { background: style.backgroundColor, border: style.borderBottomColor, font: style.fontSize };
    });
    assert.deepEqual(style, expected);
    assert.equal(style.font, '18px');
    if (width === 1440) {
      const box = await nav.boundingBox();
      assert.ok(Math.abs(box.x + box.width / 2 - width / 2) < 1);
      const content = await page.locator('.admin-operation-page').boundingBox();
      assert.ok(Math.abs(content.x + content.width / 2 - width / 2) < 1, '營運內容與頁籤共用置中版面');
      const boxes = await nav.locator('a').evaluateAll((links) => links.map((link) => link.getBoundingClientRect().width));
      assert.ok(Math.max(...boxes) - Math.min(...boxes) < 1, '子頁籤等寬');
    }
    await page.locator('.reading-menu > summary').click();
    const textSize = page.getByRole('group', { name: '文字大小', exact: true });
    await textSize.getByRole('button', { name: '放大', exact: true }).click();
    assert.equal(await active.evaluate(element => getComputedStyle(element).fontSize), '22.5px');
    await noOverflow(page);
    await page.locator('.reading-menu > summary').click();
    await textSize.getByRole('button', { name: '標準', exact: true }).click();
    await screenshot(page, `admin-underline-${width}`);
    await noOverflow(page);
    const issues = nav.getByRole('link', { name: '問題回報', exact: true });
    await issues.click();
    await page.waitForURL('**/admin?section=issues');
    assert.equal(await issues.getAttribute('aria-current'), 'page');
    for (const [label, tab] of [['供應者與審核狀態', 'suppliers'], ['取貨點維護', 'locations']]) {
      assert.equal(await nav.getByRole('link', { name: label, exact: true }).count(), 0);
      await page.goto(`${base}/admin?section=${tab}`);
      await page.waitForURL(`**/admin/catalog?tab=${tab}`);
      await noOverflow(page);
    }
    await page.goto(`${base}/shop`);
    await page.getByRole('heading', { name: '選取餐時間', exact: true }).waitFor();
    assert.equal(await page.locator('.meal-booking-content .section-heading .eyebrow').evaluate((element) => getComputedStyle(element).fontSize), eyebrow);
    await noOverflow(page);
    await screenshot(page, `meal-original-style-${width}`);
    for (const path of ['/admin/finance', '/admin/kitchen']) {
      await page.goto(`${base}${path}`);
      await page.locator('.workspace-heading').waitFor();
      assert.equal(await page.locator('.workspace-heading .eyebrow').evaluate((element) => getComputedStyle(element).fontSize), eyebrow);
      await noOverflow(page);
    }
    assert.deepEqual(state.unknown, []);
    assert.deepEqual(state.errors, []);
  });

  test(`${width}px CSV 選檔只預覽，確認後才匯入並重讀名冊`, { timeout: 20000 }, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/admin/users`);
    await page.getByRole('navigation', { name: '會員管理分類', exact: true }).getByRole('link', { name: '社員名冊', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: '新增既有社員', exact: true }).count(), 0);
    const template = page.getByRole('link', { name: '下載 CSV 範本', exact: true });
    assert.equal(await template.getAttribute('href'), '/templates/member-roster.csv');
    const response = await page.request.get(`${base}/templates/member-roster.csv`);
    assert.match(await response.text(), /社員編號,姓名,Email,手機,股金,股數/);
    await page.getByLabel('選擇 CSV 檔案').setInputFiles({ name: 'isolated.csv', mimeType: 'text/csv', buffer: Buffer.from('社員編號,姓名,Email,手機\nCSV-001,隔離社員,member@example.com,0912345678') });
    const button = page.getByRole('button', { name: '匯入 1 筆社員', exact: true });
    await button.waitFor();
    assert.equal(state.writes.length, 1);
    assert.equal(state.writes[0].contentType, 'text/csv');
    await noOverflow(page);
    await screenshot(page, `roster-csv-${width}`);
    const readCount = state.reads.filter((path) => path === '/v1/admin/member-roster').length;
    await button.click();
    await page.getByText('已匯入 1 筆社員', { exact: true }).waitFor();
    assert.deepEqual(state.writes[1].body, { rows: [entry] });
    assert.ok(state.reads.filter((path) => path === '/v1/admin/member-roster').length > readCount);
    assert.deepEqual(state.unknown, []);
    assert.deepEqual(state.errors, []);
  });
}

test('CSV 錯誤列禁止匯入，預覽後名冊異動要求重新選檔', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t, 390, { invalid: true });
  await page.goto(`${base}/admin/social?tab=roster`);
  await page.waitForURL(url => url.pathname === '/admin/users' && url.searchParams.get('tab') === 'roster');
  await page.getByLabel('選擇 CSV 檔案').setInputFiles({ name: 'duplicate.csv', mimeType: 'text/csv', buffer: Buffer.from('mocked csv') });
  await page.getByText('社員編號已存在於名冊或會籍', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '匯入 0 筆社員', exact: true }).isDisabled(), true);
  assert.equal(state.writes.length, 1);
});

test('匯入時的重複衝突清除舊預覽，避免再送出過期資料', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t, 390);
  state.conflict = true;
  await page.goto(`${base}/admin/users`);
  await page.getByRole('navigation', { name: '會員管理分類', exact: true }).getByRole('link', { name: '社員名冊', exact: true }).click();
  await page.getByLabel('選擇 CSV 檔案').setInputFiles({ name: 'conflict.csv', mimeType: 'text/csv', buffer: Buffer.from('mocked csv') });
  await page.getByRole('button', { name: '匯入 1 筆社員', exact: true }).click();
  await page.getByRole('alert').getByText('社員編號已存在，請重新選擇 CSV 檢查', { exact: true }).waitFor();
  assert.equal(await page.getByRole('region', { name: 'CSV 名冊預覽', exact: true }).count(), 0);
  assert.equal(await page.getByLabel('選擇 CSV 檔案').inputValue(), '');
  assert.equal(state.writes.length, 2);
});
