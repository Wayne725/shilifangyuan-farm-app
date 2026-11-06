import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { extname, resolve } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const base = 'http://127.0.0.1:4243';
const root = resolve(process.env.BROWSER_TEST_DIST || resolve(import.meta.dirname, '../../web/dist'));
const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp' };
const counts = { payment_attempts: 0, proposals: 0, campaigns: 0, member_proposals: 0, activities: 2, meal_events: 0, meal_events_created: 0, outbox_completed: 0, outbox_failed: 0 };
const cleanup = { enabled: true, retention_days: 30, batch_size: 100, max_batches: 5, interval_seconds: 3600,
  status: 'succeeded', last_started_at: '2030-01-02T01:00:00Z', last_finished_at: '2030-01-02T01:01:00Z',
  last_success_at: '2030-01-02T01:01:00Z', deleted_count: 500, error_type: null, oldest_eligible_at: '2029-11-01T00:00:00Z' };
const status = {
  current: { id: 5, source: 'periodic', status: 'partial_failure', started_at: '2030-01-02T01:00:00Z', progress_at: '2030-01-02T01:01:00Z', finished_at: '2030-01-02T01:01:00Z', current_phase: null, deferred_reason: null, counts,
    phases: { member_proposals: { status: 'failed', error_type: 'RuntimeError', error_code: 'stage_failed', duration_ms: 3 }, outbox: { status: 'succeeded', duration_ms: 5 } } },
  last_success: { id: 4, source: 'internal', status: 'succeeded', started_at: '2030-01-01T01:00:00Z', progress_at: '2030-01-01T01:01:00Z', finished_at: '2030-01-01T01:01:00Z', current_phase: null, deferred_reason: null, counts, phases: {} },
  long_running: false, stale_after_seconds: 300,
  backlog: [{ kind: 'outbox_due', count: 7, oldest_at: '2030-01-02T00:00:00Z' }, { kind: 'email_unknown', count: 2, oldest_at: '2030-01-02T00:00:00Z' }],
};

async function fixture(t, width, { admin = true, cleanupMode = 'succeeded' } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { statusReads: 0, statusCode: 200, errors: [], unknown: [], writes: [] };
  const payload = { ...status, ...(cleanupMode === 'legacy' ? {} : { cleanup: { ...cleanup,
    ...(cleanupMode === 'failed' ? { status: 'failed', deleted_count: 100, error_type: 'RuntimeError', owner: 'private-lease-must-not-render' } : {}),
    ...(cleanupMode === 'disabled' ? { enabled: false } : {}) } }) };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) {
      const file = extname(path) ? resolve(root, `.${path}`) : resolve(root, 'index.html');
      assert.ok(file.startsWith(`${root}/`));
      return route.fulfill({ contentType: types[extname(file)] || 'application/octet-stream', body: await readFile(file) });
    }
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated-monitor', user: {
      id: 'monitor-user', display_name: '隔離監測員', email: 'monitor@example.test', user_role: admin ? 'admin' : 'customer', membership_type: 'member' } } });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'member', membership: { status: 'active' } } });
    if (path === '/v1/admin/reconciliation-status') {
      state.statusReads += 1;
      state.onStatusRead?.();
      return route.fulfill(state.statusCode === 200 ? { json: payload } : { status: state.statusCode, json: { detail: '背景狀態暫時無法更新' } });
    }
    if (path === '/v1/admin/failed-jobs') return route.fulfill({ json: { items: [{ id: 'normal-failed', title: '付款成功通知', event_type: 'send_email', error: '供應商設定未完成', attempts: 1, retryable: true, retry_blocker: '' }], total: 1 } });
    if (path === '/v1/admin/order-search') return route.fulfill({ json: { items: [], total: 0, limit: 12, offset: 0 } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    state.unknown.push(`${request.method()} ${path}`);
    if (request.method() !== 'GET') state.writes.push(path);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated request' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.clock.install();
  await page.goto(`${base}/admin?section=issues`);
  await page.getByRole('heading', { name: admin ? '付款成功通知' : '管理工作台', exact: true }).waitFor();
  t.after(() => { assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.writes, []); });
  return { page, state, panel: page.getByRole('region', { name: '背景結算狀態', exact: true }) };
}

for (const width of [390, 1440]) {
  test(`${width}px 背景結算顯示部分失敗、最後完整巡檢與待辦數`, async t => {
    const { page, panel, state } = await fixture(t, width);
    await panel.getByRole('heading', { name: '背景結算狀態', exact: true }).waitFor();
    await panel.getByText('部分階段未完成', { exact: true }).waitFor();
    await panel.getByText('社員提案結算', { exact: true }).waitFor();
    await panel.getByText('RuntimeError', { exact: true }).waitFor();
    await panel.getByText('內部結算入口', { exact: true }).waitFor();
    await panel.getByText('待處理通知工作', { exact: true }).waitFor();
    assert.equal(await panel.getByText('7', { exact: true }).count(), 1);
    assert.equal(await panel.getByText('2', { exact: true }).count(), 1);
    assert.equal(state.statusReads, 1);
    const retention = panel.getByRole('region', { name: '監測紀錄保留', exact: true });
    await retention.getByText('最近清理完成', { exact: true }).waitFor();
    await retention.getByText('30 天', { exact: true }).waitFor();
    await retention.getByText('500 筆', { exact: true }).waitFor();
    await retention.getByText('待清理最舊時間', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  });

  test(`${width}px 結算只在可見issues頁每30秒更新，離頁即停止`, async t => {
    const { page, panel, state } = await fixture(t, width);
    await panel.getByText('部分階段未完成', { exact: true }).waitFor();
    await page.clock.runFor(31000);
    assert.ok(state.statusReads >= 2);
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      window.dispatchEvent(new Event('visibilitychange'));
    });
    const hiddenReads = state.statusReads;
    await page.clock.runFor(61000);
    assert.equal(state.statusReads, hiddenReads);
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
      window.dispatchEvent(new Event('visibilitychange'));
    });
    await page.getByRole('navigation', { name: '訂單營運子頁面' }).getByRole('link', { name: '訂單、付款與發票', exact: true }).click();
    await page.getByRole('heading', { name: '訂單、付款與發票', exact: true }).waitFor();
    const awayReads = state.statusReads;
    await page.clock.runFor(61000);
    assert.equal(state.statusReads, awayReads);
  });

  test(`${width}px 狀態503不清除既有重試原因，恢復後可更新`, async t => {
    const { page, panel, state } = await fixture(t, width);
    await panel.getByText('部分階段未完成', { exact: true }).waitFor();
    const reason = page.getByLabel('處理原因', { exact: true });
    await reason.fill('保留已核對的通知信處理原因');
    state.statusCode = 503;
    await panel.getByRole('button', { name: '更新結算狀態', exact: true }).click();
    await panel.getByRole('alert').getByText('背景狀態暫時無法更新', { exact: true }).waitFor();
    assert.equal(await reason.inputValue(), '保留已核對的通知信處理原因');
    state.statusCode = 200;
    await panel.getByRole('button', { name: '更新結算狀態', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.admin-reconciliation-status [role=alert]'));
    assert.equal(await reason.inputValue(), '保留已核對的通知信處理原因');
  });

  test(`${width}px 非管理員不讀或顯示背景結算`, async t => {
    const { page, panel, state } = await fixture(t, width, { admin: false });
    await page.getByRole('link', { name: '返回社務系統', exact: true }).waitFor();
    assert.equal(await panel.count(), 0);
    assert.equal(state.statusReads, 0);
  });

  test(`${width}px 清理失敗只顯安全狀態與已提交筆數，不冒稱完成`, async t => {
    const { page, panel } = await fixture(t, width, { cleanupMode: 'failed' });
    const retention = panel.getByRole('region', { name: '監測紀錄保留', exact: true });
    await retention.getByText('清理未完成', { exact: true }).waitFor();
    await retention.getByText('100 筆', { exact: true }).waitFor();
    await retention.getByText('RuntimeError', { exact: true }).waitFor();
    assert.equal(await retention.getByText('最近清理完成', { exact: true }).count(), 0);
    assert.equal((await page.locator('body').innerText()).includes('private-lease-must-not-render'), false);
  });

  test(`${width}px 舊API沒有cleanup欄位仍保留既有問題與巡檢畫面`, async t => {
    const { panel } = await fixture(t, width, { cleanupMode: 'legacy' });
    await panel.getByText('部分階段未完成', { exact: true }).waitFor();
    assert.equal(await panel.getByRole('region', { name: '監測紀錄保留', exact: true }).count(), 0);
  });

  test(`${width}px 清理停用明確顯示設定狀態`, async t => {
    const { panel } = await fixture(t, width, { cleanupMode: 'disabled' });
    await panel.getByRole('region', { name: '監測紀錄保留', exact: true }).getByText('已停用', { exact: true }).waitFor();
  });

  test(`${width}px 權限403會清掉清理狀態並停止輪詢`, async t => {
    const { page, panel, state } = await fixture(t, width);
    await panel.getByRole('region', { name: '監測紀錄保留', exact: true }).waitFor();
    state.statusCode = 403;
    await panel.getByRole('button', { name: '更新結算狀態', exact: true }).click();
    await page.getByRole('alert').getByText('管理權限已失效或已變更，請重新驗證後再操作。', { exact: true }).waitFor();
    assert.equal(await panel.count(), 0);
    assert.equal(await page.getByRole('region', { name: '監測紀錄保留', exact: true }).count(), 0);
    const reads = state.statusReads;
    await page.clock.runFor(61000);
    assert.equal(state.statusReads, reads);
  });
}
