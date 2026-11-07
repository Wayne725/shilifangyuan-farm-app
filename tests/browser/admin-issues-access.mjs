import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { extname, resolve } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const base = 'http://127.0.0.1:4245';
const root = resolve(process.env.BROWSER_TEST_DIST || resolve(import.meta.dirname, '../../web/dist'));
const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp' };
const privateTitle = '撤權前私有通知';
const privateStage = 'PRIVATE-STAGE-DETAIL';
const privateReceipt = 'PRIVATE-RECEIPT-ISOLATED';
const riskLabel = '已查詢供應商確認未寄出，並了解重新排入仍可能重寄';
const status = {
  current: { id: 5, source: 'periodic', status: 'partial_failure', started_at: '2030-01-02T01:00:00Z', progress_at: '2030-01-02T01:01:00Z', finished_at: '2030-01-02T01:01:00Z', current_phase: null, deferred_reason: null,
    phases: { outbox: { status: 'failed', error_type: privateStage } } },
  last_success: null, long_running: false,
  backlog: [{ kind: 'email_unknown', count: 9, oldest_at: '2030-01-02T00:00:00Z' }],
};
const jobs = { items: [{ id: 'isolated-private-email', title: privateTitle, event_type: 'send_email', error: '隔離供應商失敗原因', attempts: 1, retryable: true, retry_blocker: '', delivery_unknown: true, requires_delivery_confirmation: true, delivery_provider: 'resend', provider_message_id: privateReceipt, order_number: 'ISOLATED-PRIVATE-ORDER' }], total: 1 };

async function fixture(t, width) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { monitorReads: 0, jobReads: 0, refreshes: 0, monitorCode: 200, jobCode: 200, refreshCode: 200, retryCodes: [], monitorCodes: [], posts: [], errors: [], unknown: [], holdMonitor: false, holdJobs: false, holdRetry: false, pendingMonitor: [], pendingJobs: [], pendingRetry: [] };
  let markMonitorHeld, markJobsHeld, markRetryHeld;
  const monitorHeldIngress = new Promise(resolve => { markMonitorHeld = resolve; });
  const jobsHeldIngress = new Promise(resolve => { markJobsHeld = resolve; });
  const retryHeldIngress = new Promise(resolve => { markRetryHeld = resolve; });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) {
      const file = extname(path) ? resolve(root, `.${path}`) : resolve(root, 'index.html');
      assert.ok(file.startsWith(`${root}/`));
      return route.fulfill({ contentType: types[extname(file)] || 'application/octet-stream', body: await readFile(file) });
    }
    if (path === '/v1/auth/refresh') {
      state.refreshes += 1;
      return route.fulfill(state.refreshCode === 200 ? { json: { access_token: `isolated-admin-access-${state.refreshes}`, user: { id: 'isolated-access-admin', display_name: '隔離管理員', email: 'admin-access@example.test', user_role: 'admin', membership_type: 'member' } } } : { status: state.refreshCode, json: { detail: '登入已失效' } });
    }
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'member', membership: { status: 'active' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/admin/order-search') return route.fulfill({ json: { items: [], total: 0, limit: 12, offset: 0 } });
    if (path === '/v1/admin/reconciliation-status' || path === '/v1/admin/failed-jobs') {
      const monitor = path.endsWith('reconciliation-status');
      if (monitor) state.monitorReads += 1; else state.jobReads += 1;
      const code = monitor ? (state.monitorCodes.shift() ?? state.monitorCode) : state.jobCode;
      if (monitor ? state.holdMonitor : state.holdJobs) await new Promise(resolve => {
        (monitor ? state.pendingMonitor : state.pendingJobs).push(resolve);
        (monitor ? markMonitorHeld : markJobsHeld)();
      });
      try {
        return await route.fulfill(code === 200 ? { json: monitor ? status : jobs } : { status: code, json: { detail: code === 403 ? '管理權限已撤銷' : code === 401 ? '登入已失效' : '隔離暫時無法更新' } });
      } catch (error) {
        if (!/already handled|closed|canceled|cancelled|aborted|Invalid InterceptionId/i.test(String(error))) throw error;
      }
      return;
    }
    if (path === '/v1/admin/failed-jobs/isolated-private-email/retry' && request.method() === 'POST') {
      state.posts.push(request.postDataJSON());
      const code = state.retryCodes.shift() ?? 200;
      if (state.holdRetry) await new Promise(resolve => {
        state.pendingRetry.push(resolve);
        markRetryHeld();
      });
      return route.fulfill(code === 200 ? { json: { message: '已重新排入隔離通知' } } : { status: code, json: { detail: code === 403 ? '管理權限已撤銷' : code === 401 ? '登入已失效' : '隔離重試暫時受阻' } });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated request' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.clock.install();
  await page.goto(`${base}/admin?section=issues`);
  await page.getByRole('heading', { name: privateTitle, exact: true }).waitFor();
  await page.getByText(privateStage, { exact: true }).waitFor();
  t.after(() => { assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); });
  const monitor = page.getByRole('region', { name: '背景結算狀態', exact: true });
  async function prepareRetry() {
    await page.getByLabel('處理原因', { exact: true }).fill('已核對隔離供應商，保留草稿');
    await page.getByLabel(riskLabel, { exact: true }).check();
  }
  async function assertPrivateHidden() {
    assert.equal(await page.getByRole('heading', { name: privateTitle, exact: true }).count(), 0, '撤權後不得保留私有工作內容');
    assert.equal(await page.getByText(privateStage, { exact: true }).count(), 0, '撤權後不得保留私有結算內容');
    assert.equal(await page.getByText(privateReceipt, { exact: true }).count(), 0);
    assert.equal(await page.getByText('ISOLATED-PRIVATE-ORDER', { exact: true }).count(), 0);
    assert.equal(await page.getByRole('searchbox', { name: '搜尋問題', exact: true }).count(), 0);
    assert.equal(await page.getByLabel('處理原因', { exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '排入重試', exact: true }).count(), 0);
  }
  async function assertPollingStopped() {
    const before = [state.monitorReads, state.jobReads, state.posts.length];
    await page.clock.runFor(61000);
    assert.deepEqual([state.monitorReads, state.jobReads, state.posts.length], before, '撤權後不得繼續私有輪詢或重試');
  }
  function watchCompletionOrder() {
    const events = [];
    page.on('response', response => events.push({ kind: 'response', request: response.request(), status: response.status() }));
    for (const name of ['requestfailed', 'requestfinished']) page.on(name, request => events.push({ kind: 'terminal', request }));
    return (delayedRequest, denied) => {
      assert.equal(denied.status(), 403);
      const denialIndex = events.findIndex(event => event.kind === 'response' && event.request === denied.request() && event.status === 403);
      assert.ok(denialIndex >= 0, '必須觀察到指定請求真正的403');
      assert.ok(!events.slice(0, denialIndex).some(event => event.kind === 'terminal' && event.request === delayedRequest), '延遲請求不得在真正403之前已逾時、取消或完成');
    };
  }
  async function assertLateReplyOrAbort(request) {
    const response = await request.response();
    if (response) {
      assert.equal(response.status(), 200);
      const error = await response.finished();
      if (error) assert.match(String(error), /abort|cancel/i);
    } else {
      assert.match(request.failure()?.errorText || '', /abort|cancel/i);
    }
  }
  async function releaseMonitorAndObserve(request) {
    state.pendingMonitor.splice(0).forEach(resolve => resolve());
    state.holdMonitor = false;
    const response = await request.response();
    if (response) {
      assert.equal(response.status(), 200);
      const error = await response.finished();
      if (!error) return '舊 monitor GET 完成 200';
      assert.match(String(error), /abort|cancel/i);
      return '舊 monitor GET 收到 200 headers 後取消';
    }
    assert.match(request.failure()?.errorText || '', /abort|cancel/i);
    return '舊 monitor GET 已取消，未收到 200 response';
  }
  return { page, state, monitor, monitorHeldIngress, jobsHeldIngress, retryHeldIngress, watchCompletionOrder, assertLateReplyOrAbort, releaseMonitorAndObserve, prepareRetry, assertPrivateHidden, assertPollingStopped };
}

for (const width of [390, 1440]) {
  test(`${width}px confirmed403 monitor GET隱藏整個issues並停止輪詢，可明確重驗恢復`, async t => {
    const f = await fixture(t, width);
    await f.prepareRetry();
    f.state.monitorCode = 403;
    const response403 = f.page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === '/v1/admin/reconciliation-status' && response.status() === 403);
    await f.monitor.getByRole('button', { name: '更新結算狀態', exact: true }).click();
    await response403;
    await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).waitFor();
    await f.assertPrivateHidden();
    await f.assertPollingStopped();
    assert.equal(await f.page.getByRole('heading', { name: '待處理問題', exact: true }).count(), 1);
    f.state.monitorCode = 200;
    await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).click();
    await f.page.getByRole('heading', { name: privateTitle, exact: true }).waitFor();
    assert.equal(await f.page.getByLabel('處理原因', { exact: true }).inputValue(), '');
    assert.equal(await f.page.getByLabel(riskLabel, { exact: true }).isChecked(), false);
    assert.equal(f.state.posts.length, 0);
  });

  test(`${width}px confirmed403 failed-jobs GET後取消或忽略既有monitor回應且不復活`, async t => {
    const f = await fixture(t, width);
    f.state.holdJobs = true;
    f.state.jobCode = 403;
    const jobsRequestPromise = f.page.waitForRequest(request => request.method() === 'GET' && new URL(request.url()).pathname === '/v1/admin/failed-jobs');
    await f.page.clock.runFor(31000);
    const jobsRequest = await jobsRequestPromise;
    await f.jobsHeldIngress;
    assert.equal(f.state.pendingJobs.length, 1);
    await f.page.waitForFunction(() => document.querySelector('.admin-reconciliation-status button')?.disabled === false);
    f.state.holdMonitor = true;
    const monitorRequestPromise = f.page.waitForRequest(request => request.method() === 'GET' && new URL(request.url()).pathname === '/v1/admin/reconciliation-status');
    const monitorStartedAt = await f.page.evaluate(() => Date.now());
    await f.monitor.getByRole('button', { name: '更新結算狀態', exact: true }).click();
    const monitorRequest = await monitorRequestPromise;
    await f.monitorHeldIngress;
    assert.equal(f.state.pendingMonitor.length, 1);
    const response403 = jobsRequest.response();
    f.state.pendingJobs.splice(0).forEach(resolve => resolve());
    f.state.holdJobs = false;
    const denied = await response403;
    assert.equal(denied?.status(), 403);
    assert.equal(denied.url(), jobsRequest.url());
    assert.equal(denied.request().method(), 'GET');
    await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).waitFor();
    await f.assertPrivateHidden();
    assert.ok(await f.page.evaluate(() => Date.now()) - monitorStartedAt < 12000, '監控請求不得先跨過 HTTP timeout');
    t.diagnostic(await f.releaseMonitorAndObserve(monitorRequest));
    await f.page.clock.runFor(100);
    await f.assertPrivateHidden();
    await f.assertPollingStopped();
  });

  test(`${width}px 逾時提示不等於confirmed403，確認撤權前保留草稿`, async t => {
    const f = await fixture(t, width);
    await f.prepareRetry();
    f.state.holdMonitor = true;
    const monitorRequestPromise = f.page.waitForRequest(request => request.method() === 'GET' && new URL(request.url()).pathname === '/v1/admin/reconciliation-status');
    await f.monitor.getByRole('button', { name: '更新結算狀態', exact: true }).click();
    const monitorRequest = await monitorRequestPromise;
    await f.monitorHeldIngress;
    f.state.holdJobs = true;
    f.state.jobCode = 403;
    const jobsRequestPromise = f.page.waitForRequest(request => request.method() === 'GET' && new URL(request.url()).pathname === '/v1/admin/failed-jobs');
    await f.page.clock.runFor(32500);
    const jobsRequest = await jobsRequestPromise;
    await f.jobsHeldIngress;
    await f.page.getByRole('alert').getByText('後端服務回應逾時，可能正在重新啟動，請稍後重試。', { exact: true }).waitFor();
    assert.equal(await f.page.getByRole('heading', { name: privateTitle, exact: true }).count(), 1);
    assert.equal(await f.page.getByLabel('處理原因', { exact: true }).inputValue(), '已核對隔離供應商，保留草稿');
    assert.equal(await f.page.getByLabel(riskLabel, { exact: true }).isChecked(), true);
    assert.equal(await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).count(), 0);
    assert.equal(f.state.pendingJobs.length, 1);
    const response403 = jobsRequest.response();
    f.state.pendingJobs.splice(0).forEach(resolve => resolve());
    f.state.holdJobs = false;
    const denied = await response403;
    assert.equal(denied?.status(), 403);
    assert.equal(denied.url(), jobsRequest.url());
    assert.equal(denied.request().method(), 'GET');
    await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).waitFor();
    await f.assertPrivateHidden();
    t.diagnostic(await f.releaseMonitorAndObserve(monitorRequest));
    await f.page.clock.runFor(100);
    await f.assertPrivateHidden();
    await f.assertPollingStopped();
  });

  test(`${width}px confirmed403 retry POST不被晚到failed-jobs200復活`, async t => {
    const f = await fixture(t, width);
    await f.prepareRetry();
    f.state.holdJobs = true;
    const assertDenialPrecedesEnd = f.watchCompletionOrder();
    const jobsRequestPromise = f.page.waitForRequest(request => request.method() === 'GET' && new URL(request.url()).pathname === '/v1/admin/failed-jobs');
    await f.page.clock.runFor(31000);
    const jobsRequest = await jobsRequestPromise;
    await f.jobsHeldIngress;
    assert.equal(f.state.pendingJobs.length, 1);
    f.state.retryCodes.push(403);
    const retryResponse = f.page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/v1/admin/failed-jobs/isolated-private-email/retry' && response.status() === 403);
    await f.page.getByRole('button', { name: '排入重試', exact: true }).click();
    const denied = await retryResponse;
    await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).waitFor();
    await f.assertPrivateHidden();
    assertDenialPrecedesEnd(jobsRequest, denied);
    f.state.pendingJobs.splice(0).forEach(resolve => resolve());
    f.state.holdJobs = false;
    await f.assertLateReplyOrAbort(jobsRequest);
    await f.page.clock.runFor(100);
    await f.assertPrivateHidden();
    await f.assertPollingStopped();
    assert.equal(f.state.posts.length, 1);
  });

  test(`${width}px confirmed403之後晚到retry200不得顯示成功或重啟私有查詢`, async t => {
    const f = await fixture(t, width);
    await f.prepareRetry();
    f.state.holdRetry = true;
    const assertDenialPrecedesEnd = f.watchCompletionOrder();
    const posted = f.page.waitForRequest(request => request.url().endsWith('/isolated-private-email/retry'));
    await f.page.getByRole('button', { name: '排入重試', exact: true }).click();
    const retryRequest = await posted;
    await f.retryHeldIngress;
    await f.page.getByRole('button', { name: '排入中…', exact: true }).waitFor();
    assert.equal(f.state.pendingRetry.length, 1);
    f.state.monitorCode = 403;
    const response403 = f.page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === '/v1/admin/reconciliation-status' && response.status() === 403);
    await f.monitor.getByRole('button', { name: '更新結算狀態', exact: true }).click();
    const denied = await response403;
    await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).waitFor();
    await f.assertPrivateHidden();
    assertDenialPrecedesEnd(retryRequest, denied);
    f.state.pendingRetry.splice(0).forEach(resolve => resolve());
    f.state.holdRetry = false;
    await f.assertLateReplyOrAbort(retryRequest);
    await f.page.clock.runFor(100);
    await f.assertPrivateHidden();
    assert.equal(await f.page.getByText('已重新排入隔離通知', { exact: true }).count(), 0);
    await f.assertPollingStopped();
    assert.equal(f.state.posts.length, 1);
  });

  test(`${width}px 同帳401成功refresh與monitor503均保留私有草稿`, async t => {
    const f = await fixture(t, width);
    await f.prepareRetry();
    f.state.monitorCodes.push(401, 200);
    const refreshed = f.page.waitForResponse(response => response.url().endsWith('/v1/admin/reconciliation-status') && response.status() === 200);
    await f.monitor.getByRole('button', { name: '更新結算狀態', exact: true }).click();
    await refreshed;
    await f.page.waitForFunction(() => !document.querySelector('.admin-reconciliation-status button')?.disabled);
    assert.equal(f.state.refreshes, 2);
    f.state.monitorCode = 503;
    await f.monitor.getByRole('button', { name: '更新結算狀態', exact: true }).click();
    await f.monitor.getByRole('alert').waitFor();
    assert.equal(await f.page.getByLabel('處理原因', { exact: true }).inputValue(), '已核對隔離供應商，保留草稿');
    assert.equal(await f.page.getByLabel(riskLabel, { exact: true }).isChecked(), true);
    assert.equal(await f.page.getByRole('heading', { name: privateTitle, exact: true }).count(), 1);
    assert.equal(await f.page.getByText(privateStage, { exact: true }).count(), 1);
    assert.equal(await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).count(), 0);
  });

  for (const code of [409, 503]) test(`${width}px retry${code}保留原因及未知寄送確認，401成功續期不誤撤權`, async t => {
    const f = await fixture(t, width);
    await f.prepareRetry();
    f.state.retryCodes.push(401, code);
    await f.page.getByRole('button', { name: '排入重試', exact: true }).click();
    await f.page.getByRole('alert').first().waitFor();
    assert.equal(f.state.refreshes, 2);
    assert.equal(f.state.posts.length, 2);
    assert.deepEqual(f.state.posts[0], f.state.posts[1]);
    assert.equal(await f.page.getByLabel('處理原因', { exact: true }).inputValue(), '已核對隔離供應商，保留草稿');
    assert.equal(await f.page.getByLabel(riskLabel, { exact: true }).isChecked(), true);
    assert.equal(await f.page.getByRole('heading', { name: privateTitle, exact: true }).count(), 1);
    assert.equal(await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).count(), 0);
  });

  test(`${width}px confirmed401 refresh拒絕後隱藏私有內容並停止輪詢`, async t => {
    const f = await fixture(t, width);
    f.state.monitorCode = 401;
    f.state.refreshCode = 401;
    await f.monitor.getByRole('button', { name: '更新結算狀態', exact: true }).click();
    await f.page.getByRole('heading', { name: privateTitle, exact: true }).waitFor({ state: 'hidden' });
    await f.assertPrivateHidden();
    await f.assertPollingStopped();
  });

  test(`${width}px 未確認未知寄送風險時不發出POST`, async t => {
    const f = await fixture(t, width);
    await f.page.getByLabel('處理原因', { exact: true }).fill('尚未確認供應商');
    assert.equal(await f.page.getByRole('button', { name: '排入重試', exact: true }).isDisabled(), true);
    assert.equal(f.state.posts.length, 0);
  });
}
