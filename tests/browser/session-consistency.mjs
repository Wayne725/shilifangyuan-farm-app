import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || !['http:', 'https:'].includes(target.protocol)
  || target.pathname !== '/' || target.username || target.password || target.search || target.hash) {
  throw new Error('Session acceptance must use an isolated local origin');
}
const base = target.origin;
const product = { id: 'session-product', name: '隔離白米', unit: '袋', member_price: 300, nonmember_price: 300, stock_quantity: 20, is_active: true, daily_sales_enabled: true, can_ship: false, allowed_shipping_channels: [], tax_type: 'tax_exempt' };
const cart = [{ product, quantity: 2 }];
const fakeUser = (id = 'a') => ({ id: `session-${id}`, email: `${id}@session.example.test`, display_name: id === 'a' ? '隔離甲' : '隔離乙', user_role: 'customer', membership_type: 'member', membership_status: 'active', saved_mobile_barcode: null, email_verified_at: '2030-01-01T00:00:00Z' });
const userName = id => id === 'a' ? '隔離甲' : '隔離乙';
const order = (id = 'a') => ({ id: `session-order-${id}`, order_number: `ISOLATED-SESSION-${id.toUpperCase()}`, order_kind: 'regular', sales_channel: 'regular', fulfillment_method: 'cooperative_pickup', fulfillment_status: 'pending_confirmation', payment_status: 'pending', invoice_status: 'not_eligible', membership_type_snapshot: 'member', amount_total: 600, created_at: '2030-01-01T01:00:00Z', available_actions: [], items: [{ id: `session-item-${id}`, product_id: product.id, product_name: `${userName(id)}私密商品`, quantity: 2, unit_price: 300, subtotal: 600, unit_label: '袋', tax_type: 'tax_exempt' }] });
const deferred = () => { let release; const promise = new Promise(resolve => { release = resolve; }); return { promise, release }; };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function fixture(t, { width = 1280, authenticated = true } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block', reducedMotion: 'reduce' });
  await context.addInitScript(initialCart => {
    if (!localStorage.getItem('session_fixture_cart_seeded')) {
      localStorage.setItem('slf_cart', JSON.stringify(initialCart));
      localStorage.setItem('session_fixture_cart_seeded', 'true');
    }
    window.__sessionMixes = [];
    new MutationObserver(() => {
      const hasA = document.querySelector('a[aria-label="會員中心：隔離甲"]');
      const hasBData = document.querySelector('main')?.textContent?.includes('隔離乙私密商品');
      if (hasA && hasBData) window.__sessionMixes.push('A header with B order');
    }).observe(document, { childList: true, subtree: true, attributes: true });
  }, cart);
  const state = {
    refreshMode: 'same', refreshes: [], requests: [], acceptedOrders: [], payments: [], unknown: [], errors: [],
    failNext: new Map(), expiredToken: null, loggedOut: false, authenticated, nextVersion: 1,
    holdRefresh: null, heldRefreshStarted: false, holdDirectory: null, heldDirectoryStarted: false,
    switchOnCharges: false, chargesSwitched: false,
  };
  const releases = [];
  function hold(kind) { const gate = deferred(); state[kind] = gate; releases.push(gate.release); return gate.release; }
  async function refresh(route) {
    const number = state.refreshes.length + 1;
    const mode = number === 1 ? 'same' : state.refreshMode;
    state.refreshes.push(mode);
    if (!state.authenticated || state.loggedOut || mode === 'expired') return route.fulfill({ status: 401, json: { detail: '隔離登入已失效' } });
    if (mode === 'unavailable') return route.fulfill({ status: 503, json: { detail: '隔離續期服務暫時無法使用' } });
    const id = mode === 'switch' ? 'b' : 'a';
    const payload = { access_token: `session-${id}-v${state.nextVersion++}`, token_type: 'bearer', user: fakeUser(id) };
    if (state.holdRefresh && number > 1) { state.heldRefreshStarted = true; await state.holdRefresh.promise; }
    return route.fulfill({ json: payload });
  }
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (url.origin !== base) return route.abort();
    if (path === '/isolated-session-payment') return route.fulfill({ contentType: 'text/html', body: '<h1>隔離付款頁</h1>' });
    if (!path.startsWith('/v1/')) return method === 'GET' ? route.continue() : route.abort();
    const token = (request.headers().authorization || '').replace(/^Bearer /, '');
    const owner = token.startsWith('session-b-') ? 'b' : 'a';
    state.requests.push({ path, method, token, ...(request.postData() ? { body: request.postDataJSON() } : {}) });
    if (path === '/v1/auth/refresh') return refresh(route);
    if (path === '/v1/auth/logout') { state.loggedOut = true; return route.fulfill({ status: 204 }); }
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, sales_scope: 'pickup', shipping_enabled: false, support_phone: '0912345678' } });
    const key = `${method} ${path}`;
    if (state.failNext.get(key) > 0) { state.failNext.set(key, state.failNext.get(key) - 1); return route.fulfill({ status: 401, json: { detail: '隔離access token已到期' } }); }
    if (path === '/v1/auth/login' && method === 'POST') {
      state.authenticated = true;
      return route.fulfill({ json: { access_token: `session-a-v${state.nextVersion++}`, token_type: 'bearer', user: fakeUser() } });
    }
    if (state.expiredToken === token || state.loggedOut && path !== '/v1/activities') return route.fulfill({ status: 401, json: { detail: '隔離access token已到期' } });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'member', membership: { status: 'active', member_number: `ISOLATED-${owner.toUpperCase()}` }, directory: { is_public: false } } });
    if (path === '/v1/members/directory') {
      if (owner === 'a' && state.holdDirectory && !state.heldDirectoryStarted) { state.heldDirectoryStarted = true; await state.holdDirectory.promise; }
      return route.fulfill({ json: [{ user_id: `directory-${owner}`, nickname: `${userName(owner)}的私密名錄`, expertise: '隔離專長', bio: '只供本機測試' }] });
    }
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { enabled: false, ready: false, linked: false } });
    if (path === '/v1/membership/charges') {
      if (state.switchOnCharges && !state.chargesSwitched) { state.chargesSwitched = true; return route.fulfill({ status: 401, json: { detail: '隔離另一分頁已切換帳號' } }); }
      return route.fulfill({ json: [] });
    }
    if (path === '/v1/me/points') return route.fulfill({ json: { balance: 0, transactions: [] } });
    if (['/v1/notifications', '/v1/me/badges', '/v1/me/surplus-distributions', '/v1/shipping-rates', '/v1/meal-orders', '/v1/activities'].includes(path)) return route.fulfill({ json: [] });
    if (path === '/v1/pickup-locations') return route.fulfill({ json: [{ id: 'session-pickup', name: '隔離取貨點', address: '隔離測試地址', is_active: true }] });
    if (path === '/v1/orders/quote') return route.fulfill({ json: { amount_total: 600, membership_type: 'member', member_tax_exempt: true, items: [{ product_id: product.id, product_name: product.name, quantity: 2, unit_price: 300, subtotal: 600, unit_label: '袋', tax_type: 'tax_exempt' }] } });
    if (path === '/v1/orders' && method === 'GET') return route.fulfill({ json: [order(owner)] });
    if (path === '/v1/orders' && method === 'POST') { state.acceptedOrders.push({ owner, body: request.postDataJSON() }); return route.fulfill({ status: 201, json: order(owner) }); }
    if (/^\/v1\/orders\/session-order-[ab]\/payment-attempts$/.test(path)) { state.payments.push({ owner, path }); return route.fulfill({ status: 201, json: { id: 'session-attempt', status: 'pending', payment_url: `${base}/isolated-session-payment` } }); }
    state.unknown.push(key);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated session endpoint' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  t.after(async () => {
    releases.forEach(release => release());
    await sleep(30);
    if (process.env.SESSION_EVIDENCE_DIR) {
      await mkdir(process.env.SESSION_EVIDENCE_DIR, { recursive: true });
      const name = t.name.replace(/[^\p{L}\p{N}-]+/gu, '-');
      await writeFile(`${process.env.SESSION_EVIDENCE_DIR}/${name}.json`, JSON.stringify({ refreshes: state.refreshes, requests: state.requests, acceptedOrders: state.acceptedOrders, payments: state.payments, unknown: state.unknown, errors: state.errors, mixes: await page.evaluate(() => window.__sessionMixes || []).catch(() => []) }, null, 2));
    }
    await browser.close();
    assert.deepEqual(state.unknown, []);
    assert.deepEqual(state.errors, []);
  });
  async function visitCheckout() {
    await page.goto(`${base}/checkout`);
    await page.locator('.summary-total dd').getByText('$600', { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector('.checkout-fulfillment select[required]')?.value === 'session-pickup');
    await page.getByRole('button', { name: '前往線上付款', exact: true }).waitFor();
  }
  async function expireAtNextPoll(mode) {
    state.refreshMode = mode;
    state.expiredToken = 'session-a-v1';
    await page.clock.runFor(5100);
  }
  return { page, state, context, hold, visitCheckout, expireAtNextPoll };
}

const assertCartPreserved = async page => assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))), cart);
const header = (page, id) => page.locator(`a[aria-label="會員中心：${userName(id)}"]`);

test('Email登入失敗可重試，成功返回原結帳頁並完成一次建單付款', async t => {
  const { page, state } = await fixture(t, { width: 390, authenticated: false });
  await page.goto(`${base}/login?return_to=%2Fcheckout`);
  await page.getByLabel('電子信箱', { exact: true }).fill('a@session.example.test');
  await page.getByLabel('密碼', { exact: true }).fill('isolated-session-password');
  state.failNext.set('POST /v1/auth/login', 1);
  await page.getByRole('button', { name: '登入系統', exact: true }).click();
  await page.getByRole('alert').getByText('隔離access token已到期', { exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/login');
  assert.equal(await header(page, 'a').count(), 0);
  await assertCartPreserved(page);
  await page.getByRole('button', { name: '登入系統', exact: true }).click();
  await page.waitForURL(`${base}/checkout`);
  await header(page, 'a').waitFor({ state: 'attached' });
  await page.waitForFunction(() => document.querySelector('.checkout-fulfillment select[required]')?.value === 'session-pickup');
  assert.equal(await page.getByRole('textbox', { name: '發票通知 Email', exact: true }).inputValue(), 'a@session.example.test');
  await assertCartPreserved(page);
  await page.getByRole('button', { name: '前往線上付款', exact: true }).click();
  await page.waitForURL(`${base}/isolated-session-payment`);
  assert.equal(state.requests.filter(item => item.path === '/v1/auth/login').length, 2);
  assert.equal(state.acceptedOrders.length, 1);
  assert.equal(state.acceptedOrders[0].owner, 'a');
  assert.equal(state.payments.length, 1);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))), []);
});

test('同帳號access 401續期後GET只安全重試一次，訂單與購物車保留', async t => {
  const { page, state } = await fixture(t);
  state.failNext.set('GET /v1/orders', 1);
  await page.goto(`${base}/orders?order_id=session-order-a`);
  await page.getByText('隔離甲私密商品', { exact: true }).waitFor();
  assert.equal(await header(page, 'a').count(), 1);
  const reads = state.requests.filter(item => item.method === 'GET' && item.path === '/v1/orders');
  assert.deepEqual(reads.map(item => item.token), ['session-a-v1', 'session-a-v2']);
  assert.equal(state.refreshes.length, 2);
  await assertCartPreserved(page);
});

test('同帳號建單POST遇401可續期重試，僅建一筆訂單並正常進付款', async t => {
  const { page, state, visitCheckout } = await fixture(t);
  await visitCheckout();
  state.failNext.set('POST /v1/orders', 1);
  await page.getByRole('button', { name: '前往線上付款', exact: true }).click();
  await page.waitForURL(`${base}/isolated-session-payment`);
  const attempts = state.requests.filter(item => item.path === '/v1/orders' && item.method === 'POST');
  assert.deepEqual(attempts.map(item => item.token), ['session-a-v1', 'session-a-v2']);
  assert.deepEqual(attempts[0].body, attempts[1].body);
  assert.equal(state.acceptedOrders.length, 1);
  assert.equal(state.acceptedOrders[0].owner, 'a');
  assert.equal(state.payments.length, 1);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('slf_cart'))), []);
});

test('refresh401立即遮蔽舊私人快取及身分，保留未下單購物車', async t => {
  const { page, state } = await fixture(t);
  await page.clock.install();
  await page.goto(`${base}/directory`);
  await page.getByRole('heading', { name: '隔離甲的私密名錄', exact: true }).waitFor();
  state.refreshMode = 'expired'; state.expiredToken = 'session-a-v1';
  await page.clock.runFor(31000);
  await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(() => !document.querySelector('a[aria-label="會員中心：隔離甲"]'));
  assert.equal(await page.getByRole('heading', { name: '隔離甲的私密名錄', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '登入帳號', exact: true }).count(), 1);
  await assertCartPreserved(page);
  assert.equal(state.acceptedOrders.length, 0);
});

test('refresh503不誤登出或清購物車，恢復後可重試正常建單付款', async t => {
  const { page, state, visitCheckout } = await fixture(t);
  await visitCheckout();
  state.refreshMode = 'unavailable'; state.expiredToken = 'session-a-v1';
  await page.getByRole('button', { name: '前往線上付款', exact: true }).click();
  await page.getByText('隔離續期服務暫時無法使用', { exact: true }).waitFor();
  assert.equal(await header(page, 'a').count(), 1);
  assert.equal(state.acceptedOrders.length, 0); assert.equal(state.payments.length, 0);
  await assertCartPreserved(page);
  state.refreshMode = 'same';
  await page.getByRole('button', { name: '前往線上付款', exact: true }).click();
  await page.waitForURL(`${base}/isolated-session-payment`);
  assert.equal(state.acceptedOrders.length, 1); assert.equal(state.payments.length, 1);
});

test('A建單POST遇401刷新成B時不得把A原操作改用B憑證重送', async t => {
  const { page, state, visitCheckout } = await fixture(t);
  await visitCheckout();
  state.refreshMode = 'switch'; state.failNext.set('POST /v1/orders', 1);
  await page.getByRole('button', { name: '前往線上付款', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('a[aria-label="會員中心：隔離甲"]'));
  await sleep(100);
  const attempts = state.requests.filter(item => item.path === '/v1/orders' && item.method === 'POST');
  assert.equal(attempts.length, 1);
  assert.equal(attempts[0].token, 'session-a-v1');
  assert.deepEqual(state.acceptedOrders, []); assert.deepEqual(state.payments, []);
  await assertCartPreserved(page);
});

test('A的GET刷新成B後身分與資料一起更新，不能混甲頁首及乙訂單', async t => {
  const { page, state, expireAtNextPoll } = await fixture(t);
  await page.clock.install();
  await page.goto(`${base}/orders?order_id=session-order-a`);
  await page.getByText('隔離甲私密商品', { exact: true }).waitFor();
  await expireAtNextPoll('switch');
  await page.waitForFunction(() => !document.querySelector('a[aria-label="會員中心：隔離甲"]'));
  if (await header(page, 'b').count()) {
    await page.getByText('隔離乙私密商品', { exact: true }).waitFor();
    assert.equal(await page.getByText('隔離甲私密商品', { exact: true }).count(), 0);
  } else {
    assert.equal(await page.getByText('隔離乙私密商品', { exact: true }).count(), 0);
  }
  assert.deepEqual(await page.evaluate(() => window.__sessionMixes), []);
  assert.ok(state.refreshes.includes('switch'));
  await assertCartPreserved(page);
});

test('舊A in-flight成功回應晚於切換身分時不得重新污染共用私人快取', async t => {
  const { page, state, hold } = await fixture(t);
  const releaseOldDirectory = hold('holdDirectory');
  await page.goto(`${base}/directory`);
  await assertEventually(() => state.heldDirectoryStarted);
  state.refreshMode = 'switch'; state.switchOnCharges = true;
  await header(page, 'a').click();
  await page.waitForFunction(() => !document.querySelector('a[aria-label="會員中心：隔離甲"]'));
  await page.goBack();
  if (await header(page, 'b').count()) await page.getByRole('heading', { name: '隔離乙的私密名錄', exact: true }).waitFor();
  releaseOldDirectory();
  await sleep(150);
  assert.equal(await page.getByRole('heading', { name: '隔離甲的私密名錄', exact: true }).count(), 0);
  assert.equal(await header(page, 'a').count(), 0);
  await assertCartPreserved(page);
});

test('logout先完成時晚到refresh不得復活憑證、身分或私人快取', async t => {
  const { page, state, hold } = await fixture(t);
  await page.clock.install();
  await page.goto(`${base}/orders?order_id=session-order-a`);
  await page.getByText('隔離甲私密商品', { exact: true }).waitFor();
  const releaseRefresh = hold('holdRefresh');
  state.failNext.set('GET /v1/orders', 1);
  await page.clock.runFor(5100);
  await assertEventually(() => state.heldRefreshStarted);
  await page.getByRole('button', { name: '開啟導覽', exact: true }).click();
  await page.getByRole('button', { name: '登出 隔離甲', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('a[aria-label="會員中心：隔離甲"]'));
  releaseRefresh();
  await sleep(100);
  assert.equal(await header(page, 'a').count(), 0);
  assert.equal(await page.getByText('隔離甲私密商品', { exact: true }).count(), 0);
  assert.equal(state.requests.filter(item => item.path === '/v1/orders' && item.method === 'GET' && item.token !== 'session-a-v1').length, 0);
  await page.getByRole('navigation', { name: '主要導覽', exact: true }).getByRole('link', { name: '永續活動', exact: true }).click();
  await page.getByRole('heading', { name: '永續活動', exact: true }).waitFor();
  const activities = state.requests.filter(item => item.path === '/v1/activities');
  assert.ok(activities.length > 0);
  assert.equal(activities.at(-1).token, '');
  await assertCartPreserved(page);
});

async function assertEventually(predicate) {
  for (let index = 0; index < 100; index++) { if (predicate()) return; await sleep(20); }
  assert.equal(predicate(), true);
}
