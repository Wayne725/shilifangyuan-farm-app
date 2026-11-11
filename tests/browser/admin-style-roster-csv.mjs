import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4189');
if (!['localhost', '127.0.0.1'].includes(testUrl.hostname) || testUrl.pathname !== '/' || testUrl.username || testUrl.password) throw new Error('Use an isolated local origin');
const base = testUrl.origin;
const entry = { member_number: 'CSV-001', legal_name: '隔離社員', email: 'member@example.com', phone: '0912345678', share_capital_amount: 1000, share_count: 1 };
const preview = { rows: [{ row_number: 2, member_number: entry.member_number, legal_name: entry.legal_name, entry, errors: [] }], valid_count: 1, error_count: 0 };

async function fixture(t, width, { invalid = false, role = 'admin' } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block' });
  const state = { writes: [], reads: [], unknown: [], errors: [], conflict: false, denyRoster: false, denyExport: false, exportGate: null };
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
    if (request.method() === 'GET') {
      state.reads.push(path);
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
      if (path === '/v1/admin/points-ledger') return route.fulfill({ json: { items: [], total: 0 } });
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
        voting_member_proposals: 2, invoice_followups: 5,
      } });
      if (path === '/v1/admin/order-search') state.orderFilters = Object.fromEntries(url.searchParams);
      if (['/v1/admin/order-search', '/v1/admin/failed-jobs'].includes(path)) return route.fulfill({ json: { items: [], total: 0, limit: 12, offset: 0 } });
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
  test(`${width}px 今日待辦一次讀取數量，合計退單及會餐發票捷徑打開對應篩選`, async t => {
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
    await list.getByRole('link', { name: /發票待合作社處理.*5 筆/ }).click();
    await page.getByText('沒有符合條件的訂單。').waitFor();
    assert.equal(state.orderFilters.invoice_followup, 'true');
    assert.equal(state.orderFilters.order_category, 'meal');
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
