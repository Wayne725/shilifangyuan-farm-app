import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use an isolated local origin');
const base = target.origin;
const consent = '我已閱讀並同意十里方圓依上述用途使用及保存 LINE 登入資料。';

async function fixture(t, width, { admin = false, ready = true, startFails = false } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { reads: [], writes: [], errors: [], unknown: [], jobs: Array.from({ length: 12 }, (_, index) => ({
    id: `job-${index}`, event_type: index === 11 ? 'refund.requested' : 'invoice.issue_requested',
    title: index === 11 ? '退款處理' : '電子發票開立', description: '此工作尚未完成確認。',
    kind: index === 11 ? 'refund' : 'invoice', attempts: 8, error: '外部服務回應逾時，尚未確認處理結果。',
    retryable: false, retry_blocker: '需要先核對訂單狀態。', next_action: '開啟訂單核對目前狀態。',
    order_category: index < 10 ? 'general' : 'meal', order_number: `SLF-TEST-${index}`, order_id: `order-${index}`, created_at: '2026-10-16T01:00:00Z',
  })) };
  await context.route('**/*', async (route) => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (url.origin !== base) { if (!['https://fonts.googleapis.com', 'https://fonts.gstatic.com'].includes(url.origin)) state.unknown.push(url.origin); return route.abort(); }
    if (path === '/isolated-line-authorize') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<h1>隔離 LINE 授權頁</h1>' });
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill(admin ? { json: { access_token: 'isolated-admin', user: { id: 'admin', user_role: 'admin', display_name: '隔離管理員', email: 'admin@example.test' } } } : { status: 401, json: { detail: '未登入' } });
    if (req.method() === 'GET') {
      state.reads.push({ path, params: Object.fromEntries(url.searchParams) });
      if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready, linked: false, identity_verified: false, message: ready ? '' : 'LINE登入尚未啟用，可使用Email登入' } });
      if (path === '/v1/admin/failed-jobs') {
        const q = url.searchParams.get('q') || '', kind = url.searchParams.get('kind') || 'all', offset = Number(url.searchParams.get('offset') || 0);
        const category = url.searchParams.get('order_category') || 'all';
        const rows = state.jobs.filter((job) => (!q || job.order_number?.includes(q) || job.title.includes(q)) && (kind === 'all' || job.kind === kind) && (category === 'all' || job.order_category === category));
        return route.fulfill({ json: { items: rows.slice(offset, offset + 10), total: rows.length, offset, limit: 10 } });
      }
      if (path === '/v1/admin/order-search') return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 12 } });
      if (['/v1/products', '/v1/group-campaigns', '/v1/meal-events', '/v1/admin/suppliers', '/v1/admin/pickup-locations'].includes(path)) return route.fulfill({ json: [] });
    }
    if (req.method() === 'POST' && path === '/v1/auth/line/start') {
      state.writes.push({ path, body: req.postDataJSON() });
      return route.fulfill(startFails ? { status: 503, json: { detail: 'LINE登入暫時無法開始' } } : { json: { authorization_url: `${base}/isolated-line-authorize` } });
    }
    state.unknown.push(`${req.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離請求' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000); page.setDefaultNavigationTimeout(6000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  return { page, state };
}
async function verify(page, state) {
  assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}
for (const width of [1440, 390]) {
  test(`${width}px 問題查詢跨頁、類型篩選與訂單入口，不執行重試`, async (t) => {
    const { page, state } = await fixture(t, width, { admin: true });
    await page.goto(`${base}/admin?section=issues`);
    await page.getByRole('heading', { name: '待處理問題', exact: true }).waitFor();
    await page.getByRole('button', { name: '下一頁', exact: true }).click();
    await page.getByText('第 2 頁', { exact: false }).waitFor();
    await page.getByLabel('搜尋問題', { exact: true }).fill('SLF-TEST-0');
    await page.getByRole('button', { name: '查詢問題', exact: true }).click();
    await page.getByText('共 1 筆', { exact: false }).waitFor();
    assert.equal(state.reads.filter((read) => read.path === '/v1/admin/failed-jobs').at(-1).params.offset, '0');
    assert.equal(await page.getByRole('button', { name: '排入重試', exact: true }).count(), 0);
    assert.equal(await page.getByText('失敗原因', { exact: true }).count(), 1);
    await page.getByRole('button', { name: '清除篩選', exact: true }).click();
    await page.getByLabel('問題類型', { exact: true }).selectOption('refund');
    await page.getByRole('heading', { name: '退款處理', exact: true }).waitFor();
    const orderLink = page.getByRole('link', { name: '查看訂單', exact: true });
    assert.equal(new URL(await orderLink.getAttribute('href'), base).searchParams.get('q'), 'SLF-TEST-11');
    await page.locator('.reading-menu > summary').click();
    await page.getByRole('group', { name: '文字大小', exact: true }).getByRole('button', { name: '放大', exact: true }).click();
    await verify(page, state);
    assert.deepEqual(state.writes, []);
    await orderLink.click();
    await page.waitForURL((url) => url.pathname === '/admin' && url.searchParams.get('q') === 'SLF-TEST-11');
    await verify(page, state);
  });
  test(`${width}px 登入頁只有LINE入口，專頁選身分並同意後保留結帳與登入期限`, async (t) => {
    const { page, state } = await fixture(t, width);
    const returnTo = '/checkout?source=meal';
    await page.goto(`${base}/login?return_to=${encodeURIComponent(returnTo)}`);
    await page.getByRole('heading', { name: '帳號登入', exact: true }).waitFor();
    assert.equal(await page.getByRole('group', { name: '社員身分選擇', exact: true }).count(), 0);
    assert.equal(await page.getByRole('checkbox').count(), 1);
    await page.getByLabel('保持登入', { exact: true }).uncheck();
    await page.getByRole('link', { name: '使用LINE登入', exact: true }).click();
    await page.getByRole('heading', { name: '使用 LINE 登入', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '使用LINE登入', exact: true }).count(), 0);
    await page.getByRole('link', { name: '我是社員', exact: true }).click();
    await page.getByLabel('姓名', { exact: true }).fill('隔離社員');
    await page.getByLabel('社員編號', { exact: true }).fill('M001');
    await page.getByLabel('Email', { exact: true }).fill('member@example.test');
    await page.getByRole('button', { name: '繼續選擇註冊方式', exact: true }).click();
    await page.getByRole('button', { name: '使用 LINE 註冊', exact: true }).click();
    assert.deepEqual(state.writes, []);
    await page.getByLabel('我已閱讀並同意 LINE 登入及社員資料的使用說明。', { exact: true }).check();
    await page.getByRole('button', { name: '放大', exact: true }).click();
    await verify(page, state);
    await page.getByRole('button', { name: '使用LINE登入', exact: true }).click();
    await page.getByRole('heading', { name: '隔離 LINE 授權頁', exact: true }).waitFor();
    assert.deepEqual(state.writes, [{ path: '/v1/auth/line/start', body: { intent: 'member', remember_me: false, mode: 'login', member_claim: { legal_name: '隔離社員', member_number: 'M001', email: 'member@example.test' }, data_consent_version: 'line-data-v1', return_to: returnTo } }]);
    await verify(page, state);
  });
}
test('LINE無法啟用時可返回原結帳的Email登入，外部返回網址會回到商店', async (t) => {
  const { page, state } = await fixture(t, 390, { ready: false });
  await page.goto(`${base}/line-consent?return_to=${encodeURIComponent('https://evil.example')}`);
  await page.getByRole('button', { name: '我不是社員', exact: true }).click();
  await page.getByText('LINE登入尚未啟用，可使用Email登入', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '使用LINE登入', exact: true }).isEnabled(), false);
  assert.equal(new URL(await page.getByRole('link', { name: '使用 Email 登入', exact: true }).getAttribute('href'), base).searchParams.get('return_to'), '/shop');
  await verify(page, state); assert.deepEqual(state.writes, []);
});
test('LINE開始失敗保留專頁並顯示可理解的錯誤', async (t) => {
  const { page, state } = await fixture(t, 390, { startFails: true });
  await page.goto(`${base}/line-consent?return_to=%2Fcheckout`);
  await page.getByRole('button', { name: '我不是社員', exact: true }).click();
  await page.getByLabel(consent, { exact: true }).check();
  await page.getByRole('button', { name: '使用LINE登入', exact: true }).click();
  await page.getByRole('alert').getByText('LINE登入暫時無法開始', { exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/line-consent');
  await verify(page, state);
});


for (const width of [1440, 390]) {
  test(`${width}px 問題查詢分類保留關鍵字、重設頁碼並保留未關聯系統工作`, async (t) => {
    const { page, state } = await fixture(t, width, { admin: true });
    state.jobs.push({ id: 'system-job', event_type: 'maintenance.task', title: '系統資料核對', kind: 'other',
      attempts: 1, error: '連線逾時', retryable: false, retry_blocker: '人工核對', order_category: 'system', order_number: null });
    await page.goto(`${base}/admin?section=issues`);
    await page.getByRole('heading', { name: '待處理問題', exact: true }).waitFor();
    const categories = page.getByRole('combobox', { name: '訂單分類', exact: true });
    const pagination = page.getByRole('navigation', { name: '問題清單分頁', exact: true });
    await pagination.getByRole('button', { name: '下一頁', exact: true }).click();
    await pagination.getByText('共 13 筆・第 2 頁', { exact: true }).waitFor();
    await categories.selectOption('general');
    await pagination.getByText('共 10 筆・第 1 頁', { exact: true }).waitFor();
    assert.equal(state.reads.filter((row) => row.path === '/v1/admin/failed-jobs').at(-1).params.offset, '0');
    assert.equal(await page.locator('.failed-job').count(), 10);
    assert.equal(await page.getByText('SLF-TEST-11', { exact: true }).count(), 0);
    await page.getByLabel('搜尋問題', { exact: true }).fill('SLF-TEST-0');
    await page.getByRole('button', { name: '查詢問題', exact: true }).click();
    await pagination.getByText('共 1 筆・第 1 頁', { exact: true }).waitFor();
    await categories.selectOption('meal');
    await page.getByText('沒有符合條件的問題。', { exact: true }).waitFor();
    const query = state.reads.filter((row) => row.path === '/v1/admin/failed-jobs').at(-1).params;
    assert.equal(query.q, 'SLF-TEST-0'); assert.equal(query.order_category, 'meal');
    await page.getByRole('button', { name: '清除篩選', exact: true }).click();
    await pagination.getByText('共 2 筆・第 1 頁', { exact: true }).waitFor();
    await categories.selectOption('system');
    await page.getByRole('heading', { name: '系統資料核對', exact: true }).waitFor();
    assert.equal(await page.getByRole('link', { name: '查看訂單', exact: true }).count(), 0);
    await page.getByText('未關聯訂單', { exact: true }).waitFor();
    await verify(page, state); assert.deepEqual(state.writes, []);
  });
}
