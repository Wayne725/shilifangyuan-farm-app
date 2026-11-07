import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { extname, resolve } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const base = 'http://127.0.0.1:4247';
const root = resolve(process.env.BROWSER_TEST_DIST || resolve(import.meta.dirname, '../../web/dist'));
const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp' };
const privateName = '隔離撤權前顧客';
const privateOrder = 'PRIVATE-KITCHEN-001';
const privatePreview = 'PRIVATE-PRINT-PREVIEW';
const order = { id: 'kitchen-order', order_number: privateOrder, customer_name: privateName,
  kitchen_note: '私人餐點備註', meal_event_id: 'meal-event', event_title: '隔離便當', location: '隔離取餐處',
  pickup_at: '2030-01-03T03:30:00Z', pickup_time_confirmed: true, fulfillment_status: 'pending_confirmation',
  needs_utensils: true, items: [{ name: '隔離便當', quantity: 2, unit: '份', options: [] }] };
const job = { id: 'print-job', order_id: order.id, order_number: privateOrder, status: 'unknown', mode: 'live',
  kind: 'order', reprint_of_id: null, attempts: 1, can_query: true, last_error: 'transport_result_unknown',
  pickup_at: order.pickup_at, created_at: '2030-01-02T02:00:00Z', preview: `${privatePreview}\n${privateName}` };

async function fixture(t, width) {
  const browser = await engine.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = { reads: [], writes: [], errors: [], unknown: [], external: [], refreshes: 0, refreshCode: 200,
    codes: {}, queues: {}, holds: new Set(), pending: {}, holdEntered: {}, prints: 0 };
  await context.exposeBinding('isolatedKitchenPrint', () => { state.prints += 1; });
  await context.addInitScript(() => { window.print = () => void window.isolatedKitchenPrint(); });
  const fulfill = async (route, response) => {
    try { await route.fulfill(response); }
    catch (error) { if (!/already handled|closed|canceled|cancelled|aborted|Invalid InterceptionId/i.test(String(error))) throw error; }
  };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin === 'https://fonts.googleapis.com') return fulfill(route, { contentType: 'text/css', body: '' });
    if (url.origin !== base) { state.external.push(url.origin); return route.abort(); }
    if (!path.startsWith('/v1/')) {
      const file = extname(path) ? resolve(root, `.${path}`) : resolve(root, 'index.html');
      assert.ok(file.startsWith(`${root}/`));
      return fulfill(route, { contentType: types[extname(file)] || 'application/octet-stream', body: await readFile(file) });
    }
    if (path === '/v1/auth/refresh') {
      state.refreshes += 1;
      return fulfill(route, state.refreshCode === 200 ? { json: { access_token: `isolated-kitchen-${state.refreshes}`,
        user: { id: 'isolated-kitchen-admin', email: 'kitchen@example.invalid', display_name: '隔離管理員', user_role: 'admin', membership_type: 'member' } } }
        : { status: state.refreshCode, json: { detail: '登入已失效' } });
    }
    if (path === '/v1/members/me') return fulfill(route, { json: { membership_type: 'member', membership: { status: 'active' } } });
    if (path === '/v1/platform') return fulfill(route, { json: { orders_open: true } });
    let key, body;
    if (path === '/v1/admin/platform') { key = 'platform'; body = { kitchen_sound: false }; }
    else if (path === '/v1/admin/meal-kitchen') { key = 'list'; body = { items: [order], total: 1, meal_quantity: 2, products: [], limit: 50, offset: 0 }; }
    else if (path.endsWith('/kitchen-order/ticket')) { key = 'ticket'; body = order; }
    else if (path.endsWith('/kitchen-order/status')) { key = 'status'; body = { ...order, fulfillment_status: 'preparing' }; }
    else if (path === '/v1/admin/printers/status') { key = 'device'; body = { enabled: true, dry_run: false, status: 'online', error: null }; }
    else if (path === '/v1/admin/printers/jobs') { key = 'jobs'; body = { items: [job], total: 1 }; }
    else if (path === '/v1/admin/printers/jobs/print-job') { key = 'preview'; body = job; }
    else if (path.startsWith('/v1/admin/printers/jobs/print-job/')) { key = path.split('/').at(-1); body = key === 'reprint' ? { ...job, id: 'new-print-job', status: 'pending' } : job; }
    if (!key || !['GET', 'POST', 'PATCH'].includes(request.method())) {
      state.unknown.push(`${request.method()} ${path}`);
      return fulfill(route, { status: 404, json: { detail: 'Undefined isolated request' } });
    }
    const record = { key, method: request.method(), path, ...(request.method() === 'GET' ? {} : { body: request.postDataJSON() }) };
    (request.method() === 'GET' ? state.reads : state.writes).push(record);
    const code = state.queues[key]?.shift() ?? state.codes[key] ?? 200;
    if (state.holds.has(key)) await new Promise(done => {
      (state.pending[key] ||= []).push(done);
      state.holdEntered[key]?.(); delete state.holdEntered[key];
    });
    return fulfill(route, code === 200 ? { json: body } : { status: code,
      json: { detail: code === 403 ? '管理權限已撤銷' : code === 401 ? '登入已失效' : '隔離暫時受阻' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.clock.install();
  await page.goto(`${base}/admin/kitchen`);
  await page.locator('.meal-kitchen-order h2').getByText(privateOrder, { exact: true }).waitFor();
  await page.getByRole('button', { name: '查看／補印', exact: true }).click();
  await page.getByRole('region', { name: '雲端出單預覽', exact: true }).getByText(privatePreview, { exact: false }).waitFor();
  t.after(async () => {
    Object.values(state.pending).flat().forEach(done => done());
    if (process.env.KITCHEN_ACCESS_EVIDENCE_DIR) {
      await mkdir(process.env.KITCHEN_ACCESS_EVIDENCE_DIR, { recursive: true });
      await writeFile(resolve(process.env.KITCHEN_ACCESS_EVIDENCE_DIR, `${t.name.replace(/[^\p{L}\p{N}]+/gu, '-')}.json`), JSON.stringify({
        name: t.name, engine: process.env.BROWSER_ENGINE || 'chromium', reads: state.reads, writes: state.writes,
        refreshes: state.refreshes, print_calls: state.prints, errors: state.errors, unknown: state.unknown, external: state.external }, null, 2));
    }
    assert.deepEqual(state.errors, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.external, []);
  });
  async function hidden() {
    // A response event can precede error-body parsing and the React access-gate commit.
    try {
      await page.locator('section.admin-gate').waitFor({ state: 'visible', timeout: 6000 });
    } catch (error) {
      t.diagnostic(JSON.stringify({ phase: 'await-access-gate', engine: process.env.BROWSER_ENGINE || 'chromium',
        recent_reads: state.reads.slice(-10), writes: state.writes.length, print_calls: state.prints, errors: state.errors }));
      throw error;
    }
    const body = await page.locator('body').innerText();
    for (const value of [privateOrder, privateName, privatePreview]) assert.equal(body.includes(value), false, `撤權後不得保留${value}`);
    assert.equal(await page.getByRole('button', { name: /建立正式補印|開始製作|重新查詢結果|開啟列印對話框/ }).count(), 0);
  }
  async function stopped() {
    const before = [state.reads.length, state.writes.length, state.prints];
    await page.clock.runFor(61000);
    assert.deepEqual([state.reads.length, state.writes.length, state.prints], before, '撤權後停止私有輪詢與新操作');
  }
  async function reason() {
    await page.getByLabel('補印原因', { exact: true }).fill('保留隔離補印草稿');
    await page.getByLabel('已核對紙本，接受可能重複列印', { exact: true }).check();
  }
  async function ticket() {
    await page.getByRole('button', { name: '預覽備餐單／補印', exact: true }).click();
    await page.locator('.meal-kitchen-ticket-order').getByText(privateOrder, { exact: true }).waitFor();
  }
  const release = key => { state.holds.delete(key); (state.pending[key] || []).splice(0).forEach(done => done()); };
  const hold = key => { state.holds.add(key); return new Promise(done => { state.holdEntered[key] = done; }); };
  return { page, state, hidden, stopped, reason, ticket, release, hold };
}

for (const width of [390, 1440]) {
  test(`${width}px 雲端工作GET403隱藏廚房和既有私有預覽並停止輪詢`, async t => {
    const f = await fixture(t, width);
    f.state.codes.jobs = 403;
    const denied = f.page.waitForResponse(response => new URL(response.url()).pathname === '/v1/admin/printers/jobs' && response.status() === 403);
    await f.page.clock.runFor(11000);
    await denied;
    await f.page.clock.runFor(100);
    await f.hidden();
    await f.stopped();
    f.state.codes.jobs = 200;
    await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).click();
    await f.page.locator('.meal-kitchen-order h2').getByText(privateOrder, { exact: true }).waitFor();
    assert.equal(f.state.writes.length, 0);
  });
}

for (const key of ['device', 'preview', 'list', 'ticket', 'platform']) {
  test(`${key} GET403也隱藏父子全部私人內容並停止輪詢`, async t => {
    const f = await fixture(t, 1440);
    f.state.codes[key] = 403;
    const denied = f.page.waitForResponse(response => response.status() === 403);
    if (key === 'device') await f.page.getByRole('button', { name: '查詢設備', exact: true }).click();
    else if (key === 'list') await f.page.getByRole('button', { name: '更新清單', exact: true }).click();
    else if (key === 'ticket') await f.page.getByRole('button', { name: '預覽備餐單／補印', exact: true }).click();
    else if (key === 'platform') await f.page.reload();
    else await f.page.clock.runFor(11000);
    await denied;
    await f.page.clock.runFor(100);
    await f.hidden(); await f.stopped();
  });
}

for (const [key, button] of [['query', '重新查詢結果'], ['resolve', '確認紙本已印'], ['reprint', '建立正式補印'], ['status', '開始製作']]) {
  test(`${key} 寫入403共享撤權並禁止後續動作`, async t => {
    const f = await fixture(t, 1440);
    await f.reason();
    f.state.codes[key] = 403;
    const denied = f.page.waitForResponse(response => response.status() === 403);
    await f.page.getByRole('button', { name: button, exact: true }).click();
    await denied;
    await f.page.clock.runFor(100);
    await f.hidden(); await f.stopped();
    assert.equal(f.state.writes.length, 1);
  });
}

test('列印前重新核對GET403不得開啟列印且父子一併撤權', async t => {
  const f = await fixture(t, 1440);
  await f.ticket();
  f.state.codes.ticket = 403;
  const denied = f.page.waitForResponse(response => response.url().endsWith('/ticket') && response.status() === 403);
  await f.page.getByRole('button', { name: '開啟列印對話框', exact: true }).click();
  await denied;
  await f.page.clock.runFor(100);
  await f.hidden(); await f.stopped();
  assert.equal(f.state.prints, 0);
});

test('已撤權後晚到列印核對200不得print或恢復私人畫面', async t => {
  const f = await fixture(t, 1440);
  await f.ticket();
  const held = f.hold('ticket');
  const checking = f.page.waitForRequest(request => request.url().endsWith('/ticket'));
  await f.page.getByRole('button', { name: '開啟列印對話框', exact: true }).click();
  await checking; await held;
  f.state.codes.jobs = 403;
  const denied = f.page.waitForResponse(response => new URL(response.url()).pathname === '/v1/admin/printers/jobs' && response.status() === 403);
  await f.page.clock.runFor(11000); await denied;
  await f.page.clock.runFor(100); await f.hidden();
  f.release('ticket');
  await f.page.clock.runFor(100); await f.hidden(); await f.stopped();
  assert.equal(f.state.prints, 0);
});

test('已撤權後晚到補印200不能顯示成功或重啟查詢', async t => {
  const f = await fixture(t, 1440);
  await f.reason(); const held = f.hold('reprint');
  const posted = f.page.waitForRequest(request => request.url().endsWith('/reprint'));
  await f.page.getByRole('button', { name: '建立正式補印', exact: true }).click(); await posted; await held;
  f.state.codes.list = 403;
  const denied = f.page.waitForResponse(response => new URL(response.url()).pathname === '/v1/admin/meal-kitchen' && response.status() === 403);
  await f.page.getByRole('button', { name: '更新清單', exact: true }).click(); await denied;
  await f.page.clock.runFor(100); await f.hidden();
  f.release('reprint'); await f.page.clock.runFor(100);
  await f.hidden(); await f.stopped();
  assert.equal(await f.page.getByText('已建立正式補印工作：等待發送', { exact: true }).count(), 0);
  assert.equal(f.state.writes.length, 1);
});

test('撤權後晚到舊preview200不得復活且不再輪詢', async t => {
  const f = await fixture(t, 1440);
  const held = f.hold('preview');
  await f.page.clock.runFor(11000);
  await held;
  assert.ok(f.state.pending.preview?.length);
  f.state.codes.device = 403;
  const denied = f.page.waitForResponse(response => response.url().endsWith('/printers/status') && response.status() === 403);
  await f.page.getByRole('button', { name: '查詢設備', exact: true }).click(); await denied;
  await f.page.clock.runFor(100); await f.hidden();
  f.release('preview'); await f.page.clock.runFor(100);
  await f.hidden(); await f.stopped();
});

for (const code of [409, 503]) {
  test(`補印${code}保留草稿與相同request_id，不重送且可明確再試`, async t => {
    const f = await fixture(t, 1440); await f.reason();
    f.state.codes.reprint = code;
    await f.page.getByRole('button', { name: '建立正式補印', exact: true }).click();
    await f.page.getByRole('alert').getByText('隔離暫時受阻', { exact: true }).waitFor();
    assert.equal(await f.page.getByLabel('補印原因', { exact: true }).inputValue(), '保留隔離補印草稿');
    assert.equal(await f.page.getByLabel('已核對紙本，接受可能重複列印', { exact: true }).isChecked(), true);
    assert.equal(f.state.writes.length, 1);
    assert.equal(await f.page.getByRole('button', { name: '重新驗證管理權限', exact: true }).count(), 0);
    f.state.codes.reprint = 200;
    await f.page.getByRole('button', { name: '建立正式補印', exact: true }).click();
    await f.page.getByText('已建立正式補印工作：等待發送', { exact: true }).waitFor();
    assert.equal(f.state.writes.length, 2);
    assert.deepEqual(f.state.writes[0].body, f.state.writes[1].body);
  });
}

test('同帳401成功refresh與讀取503保留廚房和補印草稿', async t => {
  const f = await fixture(t, 1440); await f.reason();
  f.state.queues.device = [401, 200];
  const renewed = f.page.waitForResponse(response => response.url().endsWith('/printers/status') && response.status() === 200);
  await f.page.getByRole('button', { name: '查詢設備', exact: true }).click(); await renewed;
  assert.equal(f.state.refreshes, 2);
  f.state.codes.device = 503;
  await f.page.getByRole('button', { name: '查詢設備', exact: true }).click();
  await f.page.clock.runFor(1500);
  await f.page.getByRole('alert').getByText('隔離暫時受阻', { exact: true }).waitFor();
  assert.equal(await f.page.getByLabel('補印原因', { exact: true }).inputValue(), '保留隔離補印草稿');
  assert.equal(await f.page.getByLabel('已核對紙本，接受可能重複列印', { exact: true }).isChecked(), true);
  assert.equal(await f.page.locator('.meal-kitchen-order h2').getByText(privateOrder, { exact: true }).count(), 1);
  assert.equal(f.state.writes.length, 0);
});

test('confirmed401及refresh拒絕清除整頁私有內容並停止輪詢', async t => {
  const f = await fixture(t, 1440);
  f.state.codes.jobs = 401; f.state.refreshCode = 401;
  await f.page.clock.runFor(11000);
  await f.page.locator('.cloud-print-panel').waitFor({ state: 'hidden' });
  await f.hidden(); await f.stopped();
});

test('正常核對本人廚房訂單後只開啟一次列印，沒有新增補印POST', async t => {
  const f = await fixture(t, 1440);
  await f.ticket();
  await f.page.getByRole('button', { name: '開啟列印對話框', exact: true }).click();
  await f.page.getByText('已要求瀏覽器開啟列印對話框，請確認印表機與紙本結果；系統未記錄為列印成功。', { exact: true }).waitFor();
  assert.equal(await f.page.locator('.meal-kitchen-ticket-order').getByText(privateOrder, { exact: true }).isVisible(), true);
  assert.equal(f.state.prints, 1, '列印觀測器必須有真陽性，不能以未掛載的觀測器冒充late print零次');
  assert.equal(f.state.writes.length, 0);
});
