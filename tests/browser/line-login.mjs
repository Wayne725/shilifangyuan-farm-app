import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4192');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) {
  throw new Error('LINE acceptance must use an isolated local origin');
}
const base = target.origin;
const session = {
  access_token: 'isolated-line-session',
  user: { id: 'original-buyer', email: 'buyer@example.test', display_name: '測試買家', user_role: 'customer', membership_type: 'nonmember', email_verified_at: '2030-01-02T00:00:00Z' },
};
const newLineSession = { ...session, user: { ...session.user, email: null, email_verified_at: null } };
const needsEmail = { status: 'needs_email', return_to: '/account', intent: 'member', message: '請填寫訂單通知信箱' };

async function fixture(t, width, options = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const state = {
    calls: [], unknown: [], external: [], errors: [], signedIn: false, ready: true, linked: false,
    refreshStatus: undefined, startStatus: 200, callbackStatus: 200, registerStatus: 200,
    callback: { status: 'authenticated', return_to: '/account', session }, pending: needsEmail, holdRefresh: false, session, ...options,
  };
  await context.route('**/*', async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) { state.external.push(url.origin); return route.abort(); }
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    const body = request.postData() ? request.postDataJSON() : null;
    state.calls.push({ path, method: request.method(), body, authorization: request.headers().authorization ?? null });
    if (path === '/v1/auth/refresh' && request.method() === 'POST') {
      if (state.holdRefresh) await new Promise((resolve) => { state.releaseRefresh = resolve; });
      const status = state.refreshStatus ?? (state.signedIn ? 200 : 401);
      return route.fulfill({ status, json: status === 200 ? state.session : { detail: status === 503 ? '登入狀態確認失敗，請稍後重試' : '找不到登入工作階段' } });
    }
    if (path === '/v1/auth/line/status' && request.method() === 'GET') {
      return route.fulfill({ json: { ready: state.ready, linked: state.linked, identity_verified: state.linked, message: state.ready ? '' : 'LINE登入尚未啟用，可使用Email登入' } });
    }
    if (path === '/v1/auth/line/start' && request.method() === 'POST') {
      return route.fulfill({ status: state.startStatus, json: state.startStatus === 200 ? { authorization_url: `${base}/auth/line/callback?state=isolated-state&code=isolated-code` } : { detail: 'LINE授權已失效，請重新登入' } });
    }
    if (path === '/v1/auth/line/callback' && request.method() === 'POST') {
      if (state.callbackStatus === 200 && ['linked', 'authenticated'].includes(state.callback.status)) { state.signedIn = true; state.linked = true; state.session = state.callback.session || state.session; state.refreshStatus = undefined; }
      return route.fulfill({ status: state.callbackStatus, json: state.callbackStatus === 200 ? state.callback : { detail: 'LINE授權已失效，請重新登入' } });
    }
    if (path === '/v1/auth/line/pending' && request.method() === 'GET') return route.fulfill({ json: state.pending });
    if (path === '/v1/auth/line/register' && request.method() === 'POST') {
      if (state.registerStatus !== 200) return route.fulfill({ status: state.registerStatus, json: { detail: '此Email已有帳號，請使用原帳號登入後連結LINE' } });
      state.signedIn = true;
      state.session = newLineSession;
      state.linked = true;
      state.refreshStatus = undefined;
      return route.fulfill({ json: { status: 'authenticated', return_to: state.registerReturnTo || '/account', intent: state.callback.intent || 'nonmember', session: newLineSession } });
    }
    if (path === '/v1/auth/login' && request.method() === 'POST') {
      state.signedIn = true;
      return route.fulfill({ json: session });
    }
    if (request.method() === 'GET') {
      if (path === '/v1/platform') return route.fulfill({json: {orders_open: true}});
      if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'nonmember', membership: null } });
      if (path === '/v1/me/points') return route.fulfill({ json: { balance: 0 } });
      if (['/v1/products', '/v1/group-campaigns', '/v1/meal-events', '/v1/membership/charges', '/v1/notifications', '/v1/me/badges', '/v1/me/surplus-distributions'].includes(path)) return route.fulfill({ json: [] });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated LINE route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  return { page, state };
}

function calls(state, path) { return state.calls.filter((call) => call.path === `/v1/auth/${path}`); }
async function enlarge(page) {
  await page.getByRole('button', { name: '放大', exact: true }).click();
  await page.waitForFunction(() => getComputedStyle(document.documentElement).fontSize === '20px');
}
async function verify(page, state) {
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.equal(state.calls.some((call) => /payment|refund|invoice|checkout/.test(call.path)), false);
}
async function openLogin(page) {
  await page.goto(`${base}/account`);
  await page.getByRole('main').getByRole('button', { name: '帳號登入', exact: true }).click();
  await page.getByRole('heading', { name: '帳號登入', exact: true }).waitFor();
  return page.getByRole('main');
}

for (const width of [1440, 390]) {
  for (const intent of ['member', 'nonmember']) test(`${width}px LINE首次註冊${intent}不填Email即回訂餐，社員資格不自動升權`, async (t) => {
    const { page, state } = await fixture(t, width, { callback: { status: 'authenticated', intent, return_to: '/shop', session: newLineSession } });
    await page.goto(`${base}/auth/line/callback?state=isolated&code=isolated`);
    await page.waitForURL(`${base}/shop`);
    await page.getByRole('heading', { name: '我要訂餐', exact: true }).waitFor();
    assert.equal(await page.getByLabel('Email', { exact: true }).count(), 0);
    assert.equal(await page.locator('a[href="/social"]').count(), 0);
    assert.equal(calls(state, 'line/register').length, 0);
    assert.equal(session.user.membership_type, 'nonmember');
    await verify(page, state);
  });
  for (const remember of [true, false]) test(`${width}px 單一保持登入設定同時控制Email預填與登入期限（${remember}）`, async (t) => {
    const { page, state } = await fixture(t, width);
    const dialog = await openLogin(page);
    assert.equal(await dialog.getByRole('checkbox').count(), 1);
    await dialog.getByLabel('保持登入', { exact: true }).setChecked(remember);
    if (process.env.ADMIN_CLARITY_SCREENSHOTS && width === 390 && remember) await page.screenshot({ path: '/Users/liuweiwei/Documents/farm/outputs/login-single-preference-verified.png', fullPage: true });
    await dialog.getByRole('textbox', { name: '電子信箱', exact: true }).fill('buyer@example.test');
    await dialog.locator('input[name="password"]').fill('isolated-password');
    await dialog.getByRole('button', { name: '登入系統', exact: true }).click();
    await page.getByRole('heading', { name: /測試買家/ }).waitFor();
    assert.equal(calls(state, 'login').at(-1).body.remember_me, remember);
    assert.equal(await page.evaluate(() => localStorage.getItem('shilifangyuan.remembered-login-email')), remember ? 'buyer@example.test' : null);
    await verify(page, state);
  });
  test(`${width}px LINE未設定時按鈕停用，Email仍可登入且大字不溢出`, async (t) => {
    const { page, state } = await fixture(t, width, { ready: false });
    await page.goto(`${base}/account`); await enlarge(page);
    await page.getByRole('main').getByRole('button', { name: '帳號登入', exact: true }).click();
    const dialog = page.getByRole('main');
    await dialog.getByRole('link', { name: '使用LINE登入', exact: true }).click();
    await page.getByRole('button', { name: '我不是社員', exact: true }).click();
    await page.getByText(/LINE登入尚未啟用/).waitFor();
    assert.equal(await dialog.getByRole('button', { name: /使用\s*LINE\s*登入/ }).isEnabled(), false);
    await page.getByRole('link', { name: '使用 Email 登入', exact: true }).click();
    assert.equal(await dialog.getByRole('button', { name: '登入系統', exact: true }).isEnabled(), true);
    await dialog.getByLabel('電子信箱', { exact: true }).fill('buyer@example.test');
    await dialog.getByLabel('密碼', { exact: true }).fill('Isolated-test-only-123');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await dialog.getByRole('button', { name: '登入系統', exact: true }).click();
    await page.getByRole('heading', { name: /測試買家/ }).waitFor();
    assert.equal(calls(state, 'line/start').length, 0);
    assert.equal(calls(state, 'login').length, 1);
    await verify(page, state);
  });

  test(`${width}px 社員三項資料及保持登入傳入LINE，callback只送一次並清除授權參數`, async (t) => {
    const { page, state } = await fixture(t, width);
    const dialog = await openLogin(page);
    await dialog.getByLabel('保持登入', { exact: true }).uncheck();
    await dialog.getByRole('link', { name: '使用LINE登入', exact: true }).click();
    await dialog.getByRole('link', { name: '我是社員', exact: true }).click();
    await page.getByLabel('姓名', { exact: true }).fill('隔離社員');
    await page.getByLabel('社員編號', { exact: true }).fill('M001');
    await page.getByLabel('Email', { exact: true }).fill('member@example.test');
    await page.getByRole('button', { name: '繼續選擇註冊方式', exact: true }).click();
    await page.getByRole('button', { name: '使用 LINE 註冊', exact: true }).click();
    await dialog.getByRole('checkbox').check();
    await dialog.getByRole('button', { name: /使用\s*LINE\s*登入/ }).click();
    await page.getByRole('heading', { name: /測試買家/ }).waitFor();
    assert.deepEqual(calls(state, 'line/start').map((call) => call.body), [{ intent: 'member', remember_me: false, mode: 'login', member_claim: { legal_name: '隔離社員', member_number: 'M001', email: 'member@example.test' }, data_consent_version: 'line-data-v1', return_to: '/account' }]);
    assert.deepEqual(calls(state, 'line/callback').map((call) => call.body), [{ state: 'isolated-state', code: 'isolated-code', error: '' }]);
    assert.equal(new URL(page.url()).search, '');
    assert.equal(calls(state, 'line/pending').length, 0);
    await enlarge(page); await verify(page, state);
  });

  test(`${width}px 原帳號連結LINE後仍為原使用者，不送出註冊或合併帳號`, async (t) => {
    const { page, state } = await fixture(t, width, { signedIn: true, callback: { status: 'linked', return_to: '/account', message: 'LINE已連結' } });
    await page.goto(`${base}/account`);
    await page.getByRole('checkbox', { name: /我已閱讀並同意LINE登入資料使用說明/ }).check();
    await page.getByRole('button', { name: /連結\s*LINE/ }).click();
    await page.getByText(/已連結\s*LINE/).waitFor();
    assert.equal(new URL(page.url()).pathname, '/account');
    assert.deepEqual(calls(state, 'line/start').map((call) => call.body), [{ intent: 'nonmember', remember_me: true, mode: 'link', data_consent_version: 'line-data-v1', return_to: '/account' }]);
    assert.equal(calls(state, 'line/start')[0].authorization, `Bearer ${session.access_token}`);
    assert.equal(calls(state, 'line/callback').length, 1);
    assert.equal(calls(state, 'line/register').length, 0);
    assert.equal(await page.getByText('buyer@example.test', { exact: true }).count(), 1);
    await enlarge(page); await verify(page, state);
  });

  test(`${width}px 舊版待註冊409不重送或合併，Email登入仍可使用`, async (t) => {
    const { page, state } = await fixture(t, width, { callback: needsEmail, registerStatus: 409 });
    await page.goto(`${base}/auth/line/callback?state=isolated-state&code=isolated-code`);
    await page.getByRole('alert').filter({ hasText: '此Email已有帳號' }).waitFor();
    assert.equal(await page.getByLabel('Email', { exact: true }).count(), 0);
    assert.deepEqual(calls(state, 'line/register').map((call) => call.body), [{}]);
    assert.equal(calls(state, 'line/callback').length, 1);
    await enlarge(page);
    await page.getByRole('button', { name: /Email.*登入/ }).click();
    const dialog = page.getByRole('main');
    await dialog.getByLabel('電子信箱', { exact: true }).fill('buyer@example.test');
    await dialog.getByLabel('密碼', { exact: true }).fill('Isolated-test-only-123');
    await dialog.getByRole('button', { name: '登入系統', exact: true }).click();
    await page.waitForURL(`${base}/account`);
    assert.equal(calls(state, 'line/register').length, 1);
    assert.equal(state.signedIn, true);
    await verify(page, state);
  });

  test(`${width}px callback401不refresh或自動重送，重新整理改查pending`, async (t) => {
    const { page, state } = await fixture(t, width, { callbackStatus: 401 });
    await page.goto(`${base}/auth/line/callback?state=isolated-state&code=isolated-code`);
    await page.getByRole('alert').filter({ hasText: 'LINE授權已失效' }).waitFor();
    assert.equal(calls(state, 'refresh').length, 1);
    assert.equal(calls(state, 'line/callback').length, 1);
    assert.equal(new URL(page.url()).search, '');
    await page.reload();
    await page.waitForURL(`${base}/account`);
    await page.getByRole('heading', { name: /測試買家/ }).waitFor();
    assert.equal(calls(state, 'line/callback').length, 1);
    assert.equal(calls(state, 'line/pending').length, 1);
    assert.equal(calls(state, 'refresh').length, 3);
    await verify(page, state);
  });
}

test('既有LINE帳號登入即使選社員仍回原頁，不當成新註冊送去認領', async (t) => {
  const { page, state } = await fixture(t, 390, { callback: { status: 'authenticated', return_to: '/shop', intent: 'member', session } });
  await page.goto(`${base}/auth/line/callback?state=isolated&code=isolated`);
  await page.waitForURL(`${base}/shop`);
  await page.getByRole('heading', { name: '我要訂餐', exact: true }).waitFor();
  assert.equal(calls(state, 'line/register').length, 0);
  assert.equal(await page.locator('a[href="/social"]').count(), 0);
  await verify(page, state);
});

test('非社員入口保留保持登入，start401不refresh或重試', async (t) => {
  const { page, state } = await fixture(t, 390, { startStatus: 401 });
  const dialog = await openLogin(page);
  assert.equal(await dialog.getByLabel('保持登入', { exact: true }).isChecked(), true);
  await dialog.getByRole('link', { name: '使用LINE登入', exact: true }).click();
  await dialog.getByRole('button', { name: '我不是社員', exact: true }).click();
  assert.equal(await dialog.getByRole('button', { name: '我不是社員', exact: true }).getAttribute('aria-pressed'), 'true');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: /使用\s*LINE\s*登入/ }).click();
  await dialog.getByRole('alert').filter({ hasText: 'LINE授權已失效' }).waitFor();
  assert.deepEqual(calls(state, 'line/start').map((call) => call.body), [{ intent: 'nonmember', remember_me: true, mode: 'login', data_consent_version: 'line-data-v1', return_to: '/account' }]);
  assert.equal(calls(state, 'refresh').length, 2);
  assert.equal(calls(state, 'line/callback').length, 0);
  await verify(page, state);
});

test('LINE取消授權可返回原頁，送出error且不送出註冊', async (t) => {
  const { page, state } = await fixture(t, 390, { callback: { status: 'cancelled', return_to: '/shop', message: '已取消LINE登入' } });
  await page.goto(`${base}/auth/line/callback?state=isolated-state&error=access_denied`);
  await page.getByText('已取消LINE登入', { exact: true }).waitFor();
  assert.deepEqual(calls(state, 'line/callback').map((call) => call.body), [{ state: 'isolated-state', code: '', error: 'access_denied' }]);
  assert.equal(new URL(page.url()).search, '');
  await page.getByRole('link', { name: '返回原頁', exact: true }).click();
  await page.waitForURL(`${base}/shop`);
  assert.equal(calls(state, 'line/register').length, 0);
  await verify(page, state);
});

test('先完成工作階段恢復才送callback，恢復錯誤可手動重試而不提前消耗code', async (t) => {
  const { page, state } = await fixture(t, 390, { holdRefresh: true, refreshStatus: 503 });
  await page.goto(`${base}/auth/line/callback?state=isolated-state&code=isolated-code`);
  await page.waitForFunction(() => document.querySelector('.line-callback-page'));
  assert.equal(calls(state, 'refresh').length, 1);
  assert.equal(calls(state, 'line/callback').length, 0);
  assert.equal(typeof state.releaseRefresh, 'function');
  state.releaseRefresh();
  await page.getByText('登入狀態確認失敗，請稍後重試', { exact: true }).waitFor();
  assert.equal(calls(state, 'line/callback').length, 0);
  assert.equal(new URL(page.url()).searchParams.get('code'), 'isolated-code');
  state.holdRefresh = false; state.refreshStatus = 401;
  await page.getByRole('button', { name: /重新確認登入狀態|重試連線/ }).click();
  await page.waitForURL(`${base}/account`);
  await page.getByRole('heading', { name: /測試買家/ }).waitFor();
  assert.equal(calls(state, 'refresh').length, 3);
  assert.equal(calls(state, 'line/callback').length, 1);
  assert.equal(new URL(page.url()).search, '');
  await verify(page, state);
});

test('舊版等待中的註冊自動完成，不再補Email，重整不重複建帳', async (t) => {
  const { page, state } = await fixture(t, 1440);
  await page.goto(`${base}/auth/line/callback`);
  await page.waitForURL(`${base}/account`);
  assert.equal(calls(state, 'line/callback').length, 0);
  assert.equal(calls(state, 'line/pending').length, 1);
  await page.reload();
  await page.getByRole('heading', { name: /測試買家/ }).waitFor();
  assert.equal(calls(state, 'line/pending').length, 1);
  assert.equal(calls(state, 'line/callback').length, 0);
  assert.equal(calls(state, 'line/register').length, 1);
  assert.equal(state.signedIn, true);
  await verify(page, state);
});
