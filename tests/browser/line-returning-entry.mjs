import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use isolated local LINE entry');
const base = target.origin;
const entry = (intent) => `/register?intent=${intent}&return_to=%2Fshop&remember_me=true`;

async function fixture(t, width = 390, { signedIn = true, holdRefresh = false, refreshStatus } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
  const state = { writes: [], errors: [], refreshes: 0 };
  let releaseRefresh;
  const refreshGate = new Promise((resolve) => { releaseRefresh = resolve; });
  await context.route('**/*', async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') {
      state.refreshes++;
      if (holdRefresh) await refreshGate;
      return route.fulfill({ status: refreshStatus ?? (signedIn ? 200 : 401), json: signedIn ? {
        access_token: 'isolated-returning-line-user',
        user: { id: 'line-buyer', display_name: '回訪買家', email: null, user_role: 'customer', membership_type: 'nonmember' },
      } : { detail: '尚未登入' } });
    }
    if (request.method() !== 'GET') {
      state.writes.push(path);
      return route.fulfill({ status: 400, json: { detail: '回訪不應重新註冊或授權' } });
    }
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, social_open: true } });
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true, linked: signedIn } });
    return route.fulfill({ json: [] });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  return { page, state, releaseRefresh };
}

async function expectMeals(page, state) {
  await page.waitForURL(`${base}/shop`);
  await page.getByRole('heading', { name: '我要訂餐', exact: true }).waitFor();
  assert.equal(await page.getByRole('tab', { name: '便當預購', selected: true }).count(), 1);
  assert.equal(await page.getByRole('heading', { name: '註冊帳號', exact: true }).count(), 0);
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.errors, []);
}

for (const width of [1440, 390]) {
  for (const intent of ['existing_member', 'nonmember']) {
    test(`${width}px 已登入再次從 LINE ${intent} 入口直接到便當，返回不重走註冊`, async (t) => {
      const { page, state } = await fixture(t, width);
      await page.goto(`${base}/help`);
      await page.getByRole('main').waitFor();
      await page.goto(`${base}${entry(intent)}`);
      await expectMeals(page, state);
      await page.goBack();
      await page.waitForURL(`${base}/help`);
      await page.goto(`${base}${entry(intent)}`);
      await expectMeals(page, state);
    });
  }
  test(`${width}px 已登入從舊 LINE 同意入口也自動進便當`, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/line-consent`);
    await expectMeals(page, state);
  });
}

for (const intent of ['existing_member', 'nonmember']) {
  test(`首次或登入過期的 ${intent} 入口保留註冊與資料同意`, async (t) => {
    const { page, state } = await fixture(t, 390, { signedIn: false });
    await page.goto(`${base}${entry(intent)}`);
    const member = intent === 'existing_member';
    await page.getByRole('heading', { name: member ? '社員資料驗證' : '選擇註冊方式', exact: true }).waitFor();
    if (member) {
      await page.getByLabel('電話號碼', { exact: true }).fill('0912345678');
      await page.getByLabel('Email', { exact: true }).fill('member@example.test');
      await page.getByRole('button', { name: '繼續使用 LINE', exact: true }).click();
    } else {
      await page.getByRole('button', { name: '使用 LINE 註冊', exact: true }).click();
    }
    assert.equal(await page.getByLabel('我已閱讀並同意 LINE 登入及社員資料的使用說明。', { exact: true }).isChecked(), false);
    assert.equal(await page.getByRole('button', { name: '使用LINE登入', exact: true }).isEnabled(), false);
    assert.equal(new URL(page.url()).pathname, '/register');
    assert.deepEqual(state.writes, []);
  });
}

test('登入還原完成前不閃現註冊表單，還原成功才進便當', async (t) => {
  const { page, state, releaseRefresh } = await fixture(t, 390, { holdRefresh: true });
  await page.goto(`${base}${entry('existing_member')}`);
  await page.getByRole('region', { name: '正在確認登入', exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/register');
  assert.equal(await page.getByLabel('電話號碼', { exact: true }).count(), 0);
  releaseRefresh();
  await expectMeals(page, state);
});

test('登入還原服務失敗仍顯示重試，不誤判為回訪登入成功', async (t) => {
  const { page, state } = await fixture(t, 390, { refreshStatus: 503 });
  await page.goto(`${base}${entry('nonmember')}`);
  await page.getByRole('heading', { name: '暫時無法確認登入狀態', exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/register');
  assert.equal(await page.getByRole('heading', { name: '選擇註冊方式', exact: true }).count(), 0);
  assert.deepEqual(state.writes, []);
});

for (const path of ['/register?intent=nonmember', '/line-consent']) {
  test(`${path} 保留指定站內返回頁，拒絕外站返回網址`, async (t) => {
    const { page, state } = await fixture(t);
    const separator = path.includes('?') ? '&' : '?';
    const returnTo = '/checkout?source=meal#summary';
    await page.goto(`${base}${path}${separator}return_to=${encodeURIComponent(returnTo)}`);
    await page.waitForURL(`${base}${returnTo}`);
    await page.goto(`${base}${path}${separator}return_to=${encodeURIComponent('https://external.example/checkout')}`);
    await expectMeals(page, state);
  });
}
