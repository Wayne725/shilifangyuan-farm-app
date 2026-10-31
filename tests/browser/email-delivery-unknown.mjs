import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { extname, resolve } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const base = 'http://127.0.0.1:4241';
const root = resolve(process.env.BROWSER_TEST_DIST || resolve(import.meta.dirname, '../../web/dist'));
const confirmation = '已查詢供應商確認未寄出，並了解重新排入仍可能重寄';
const reason = '已核對供應商紀錄，確認這封通知未寄出';
const success = '已重新排入通知信工作';
const errors = { 401: '登入已失效，請重新登入', 409: '請先確認供應商未寄出，再允許重新排入', 503: '郵件服務暫時無法使用，請稍後重試' };
const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.jpg': 'image/jpeg', '.woff2': 'font/woff2' };

async function fixture(t, width, { unknown = true, retryable = true, hasReceipt = true, statuses = [], expired = false, admin = true } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const provider = width === 390 ? 'mailersend' : 'resend';
  const user = { id: 'isolated-email-admin', user_role: admin ? 'admin' : 'customer', display_name: '隔離郵件管理員', email: 'email-admin@example.test', membership_type: 'member' };
  const job = { id: 'isolated-email-job', event_type: 'send_email', attempts: 1, title: '付款成功通知',
    error: unknown ? '寄送結果未知，可能已受理，請先查詢供應商。' : '供應商設定尚未完成。',
    retryable, retry_blocker: retryable ? '' : '帳號驗證信必須由本人重新申請',
    delivery_unknown: unknown, requires_delivery_confirmation: unknown,
    delivery_provider: unknown ? provider : null, provider_message_id: unknown && hasReceipt ? 'isolated-provider-receipt' : null,
    order_category: 'general', order_id: 'isolated-order', order_number: 'ISOLATED-EMAIL-ORDER', created_at: '2026-10-31T00:00:00Z' };
  const state = { writes: [], reads: [], errors: [], unknown: [], statuses: [...statuses], jobs: [job], refreshes: 0 };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) {
      const file = extname(path) ? resolve(root, `.${path}`) : resolve(root, 'index.html');
      assert.ok(file.startsWith(`${root}/`));
      return route.fulfill({ contentType: types[extname(file)] || 'application/octet-stream', body: await readFile(file) });
    }
    if (path === '/v1/admin/reconciliation-status') return route.fulfill({ json: { current: null, last_success: null, long_running: false, stale_after_seconds: 300, backlog: [] } });
    if (path === '/v1/auth/refresh') {
      state.refreshes += 1;
      return route.fulfill(expired && state.refreshes > 1
        ? { status: 401, json: { detail: errors[401] } }
        : { json: { access_token: `isolated-admin-token-${state.refreshes}`, user } });
    }
    if (request.method() === 'GET') {
      state.reads.push(path);
      if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'member', membership: { status: 'active' } } });
      if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
      if (path === '/v1/admin/failed-jobs') return route.fulfill({ json: { items: state.jobs, total: state.jobs.length } });
    }
    if (path === `/v1/admin/failed-jobs/${job.id}/retry` && request.method() === 'POST') {
      const body = request.postDataJSON();
      state.writes.push(body);
      // This mock reflects the separately tested backend gate; it does not prove the real endpoint's authorization.
      if (!admin || !retryable || (unknown && body.confirm_delivery_risk !== true)) {
        return route.fulfill({ status: 409, json: { detail: errors[409] } });
      }
      const status = state.statuses.shift() || 200;
      if (status !== 200) return route.fulfill({ status, json: { detail: errors[status] } });
      state.jobs = [];
      return route.fulfill({ json: { message: success } });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated request' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.goto(`${base}/admin?section=issues`);
  if (admin) await page.getByRole('heading', { name: job.title, exact: true }).waitFor();
  else await page.getByRole('heading', { name: '管理工作台', exact: true }).waitFor();
  t.after(() => { assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); });
  return { page, state, provider, row: page.locator('article.failed-job') };
}

async function prepare(row) {
  await row.getByLabel('處理原因', { exact: true }).fill(reason);
  await row.getByRole('checkbox', { name: confirmation, exact: true }).check();
}

async function verifyDraft(row, state, writes) {
  assert.equal(await row.getByLabel('處理原因', { exact: true }).inputValue(), reason);
  assert.equal(await row.getByRole('checkbox', { name: confirmation, exact: true }).isChecked(), true);
  assert.equal(await row.getByRole('button', { name: '排入重試', exact: true }).isEnabled(), true);
  assert.deepEqual(state.writes, Array.from({ length: writes }, () => ({ reason, confirm_delivery_risk: true })));
}

async function verifyLayout(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}

for (const width of [390, 1440]) {
  test(`${width}px 未知郵件顯示核對資料，未確認零 POST，確認後保留理由重排`, async t => {
    const { page, state, row, provider } = await fixture(t, width);
    await row.getByRole('status').getByText('寄送結果未知', { exact: true }).waitFor();
    await row.getByText(provider === 'resend' ? 'Resend' : 'MailerSend', { exact: true }).waitFor();
    await row.getByText('isolated-provider-receipt', { exact: true }).waitFor();
    const button = row.getByRole('button', { name: '排入重試', exact: true });
    assert.equal(await button.isEnabled(), false);
    await row.getByLabel('處理原因', { exact: true }).fill(reason);
    assert.equal(await button.isEnabled(), false);
    await row.getByLabel('處理原因', { exact: true }).press('Enter');
    assert.deepEqual(state.writes, []);
    await row.getByRole('checkbox', { name: confirmation, exact: true }).check();
    await verifyLayout(page);
    await button.click();
    await page.getByRole('status').getByText(success, { exact: true }).waitFor();
    await page.getByText('目前沒有待處理問題。', { exact: true }).waitFor();
    assert.deepEqual(state.writes, [{ reason, confirm_delivery_risk: true }]);
  });

  test(`${width}px 一般失敗郵件維持原重試，不顯示未知風險或確認`, async t => {
    const { page, state, row } = await fixture(t, width, { unknown: false });
    assert.equal(await row.getByRole('checkbox').count(), 0);
    assert.equal(await row.getByText('寄送結果未知', { exact: true }).count(), 0);
    assert.equal(await row.getByText('郵件供應商', { exact: true }).count(), 0);
    await row.getByLabel('處理原因', { exact: true }).fill(reason);
    await row.getByRole('button', { name: '排入重試', exact: true }).click();
    await page.getByRole('status').getByText(success, { exact: true }).waitFor();
    assert.deepEqual(state.writes, [{ reason }]);
    await verifyLayout(page);
  });

  for (const status of [409, 503]) {
    test(`${width}px 未知郵件 ${status} 保留理由與確認，可用同一問題重試`, async t => {
      const { page, state, row } = await fixture(t, width, { statuses: [status] });
      await prepare(row);
      await row.getByRole('button', { name: '排入重試', exact: true }).click();
      await row.getByRole('alert').getByText(errors[status], { exact: true }).waitFor();
      await verifyDraft(row, state, 1);
      assert.equal(await page.getByText(success, { exact: true }).count(), 0);
      await verifyLayout(page);
      await row.getByRole('button', { name: '排入重試', exact: true }).click();
      await page.getByRole('status').getByText(success, { exact: true }).waitFor();
      assert.deepEqual(state.writes, [{ reason, confirm_delivery_risk: true }, { reason, confirm_delivery_risk: true }]);
    });
  }

  test(`${width}px 401 同帳號恢復後若服務失敗仍保留原重試資料`, async t => {
    const { page, state, row } = await fixture(t, width, { statuses: [401, 503] });
    await prepare(row);
    await row.getByRole('button', { name: '排入重試', exact: true }).click();
    await row.getByRole('alert').getByText(errors[503], { exact: true }).waitFor();
    assert.equal(state.refreshes, 2);
    await verifyDraft(row, state, 2);
    await row.getByRole('button', { name: '排入重試', exact: true }).click();
    await page.getByRole('status').getByText(success, { exact: true }).waitFor();
    assert.deepEqual(state.writes, Array.from({ length: 3 }, () => ({ reason, confirm_delivery_risk: true })));
  });

  test(`${width}px 401 且登入恢復失敗時隱藏舊管理資料及重試權限`, async t => {
    const { page, state, row } = await fixture(t, width, { statuses: [401], expired: true });
    await prepare(row);
    await row.getByRole('button', { name: '排入重試', exact: true }).click();
    await page.getByRole('button', { name: '管理者登入', exact: true }).waitFor();
    assert.equal(state.refreshes, 2);
    assert.equal(await page.locator('article.failed-job').count(), 0);
    assert.equal(await page.getByRole('button', { name: '排入重試', exact: true }).count(), 0);
    assert.deepEqual(state.writes, [{ reason, confirm_delivery_risk: true }]);
  });

  test(`${width}px 身分驗證類未知郵件仍需本人申請，沒有管理員重寄入口`, async t => {
    const { page, state, row } = await fixture(t, width, { retryable: false, hasReceipt: false });
    await row.getByText('帳號驗證信必須由本人重新申請', { exact: true }).waitFor();
    await row.getByText('未取得', { exact: true }).waitFor();
    assert.equal(await row.getByRole('checkbox').count(), 0);
    assert.equal(await row.getByRole('button', { name: '排入重試', exact: true }).count(), 0);
    assert.equal(await row.getByLabel('處理原因', { exact: true }).count(), 0);
    assert.deepEqual(state.writes, []);
    await verifyLayout(page);
  });

  test(`${width}px 非管理員不載入問題清單及重寄表單`, async t => {
    const { page, state } = await fixture(t, width, { admin: false });
    await page.getByRole('link', { name: '返回社務系統', exact: true }).waitFor();
    assert.equal(state.reads.includes('/v1/admin/failed-jobs'), false);
    assert.equal(await page.locator('article.failed-job').count(), 0);
    assert.deepEqual(state.writes, []);
  });
}
