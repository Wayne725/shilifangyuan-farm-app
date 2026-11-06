import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4194');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use an isolated local origin');
const base = target.origin;
const resultMessage = '查詢已受理，請查看填寫的 Email。';

async function fixture(t, width, { signedIn = false, status = 202, detail = '', deferResponse = false, start = '/line-consent?return_to=%2Fadmin%2Fusers&remember_me=false' } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 950 }, serviceWorkers: 'block' });
  const state = { requests: [], unknown: [], errors: [], refreshes: 0, releaseResponse: null };
  let requestStarted;
  state.requestStarted = new Promise(resolve => { requestStarted = resolve; });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') {
      state.refreshes++;
      return route.fulfill(signedIn ? { json: { access_token: 'isolated', user: { id: 'member', display_name: '已登入測試帳號', user_role: 'customer', membership_type: 'nonmember' } } } : { status: 401, json: { detail: '未登入' } });
    }
    if (path === '/v1/auth/member-lookup' && request.method() === 'POST') {
      state.requests.push(request.postDataJSON());
      if (deferResponse) await new Promise(resolve => { state.releaseResponse = resolve; requestStarted(); });
      return route.fulfill({ status, json: status >= 200 && status < 300 ? { message: resultMessage } : { detail } });
    }
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true, linked: false } });
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.goto(`${base}${start}`);
  return { page, state };
}

async function inputContact(page) {
  const phone = page.getByLabel('手機號碼', { exact: true });
  assert.equal(await phone.getAttribute('type'), 'tel');
  assert.equal(await phone.getAttribute('inputmode'), 'tel');
  await phone.fill('+886 900-000-000');
  await page.getByLabel('入社時登記的 Email', { exact: true }).fill('member@example.test');
}

async function verify(page, state) {
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}

for (const width of [390, 1280]) {
  test(`${width}px LINE 入口到社員查詢保留返回目標，結果不建帳或公開名冊`, async t => {
    const { page, state } = await fixture(t, width);
    const entry = page.getByRole('link', { name: '不確定是否是社員？', exact: true });
    await entry.click();
    await page.getByRole('heading', { name: '查詢社員資格', exact: true }).waitFor();
    const lookupUrl = new URL(page.url());
    assert.equal(lookupUrl.pathname, '/member-lookup');
    assert.equal(lookupUrl.searchParams.get('return_to'), '/admin/users');
    assert.equal(lookupUrl.searchParams.get('remember_me'), 'false');
    assert.equal(await page.getByRole('button', { name: '寄送查詢結果', exact: true }).isEnabled(), false);
    await inputContact(page);
    await page.getByRole('button', { name: '寄送查詢結果', exact: true }).click();
    await page.getByRole('status').getByText(resultMessage, { exact: true }).waitFor();
    assert.deepEqual(state.requests, [{ phone: '+886 900-000-000', email: 'member@example.test' }]);
    assert.equal(page.url().includes('member%40'), false);
    assert.equal(await page.getByText('member@example.test', { exact: true }).count(), 0);
    assert.equal(await page.getByLabel('手機號碼', { exact: true }).count(), 0);
    const registerUrl = new URL(await page.getByRole('link', { name: '既有社員註冊', exact: true }).getAttribute('href'), base);
    assert.equal(registerUrl.pathname, '/register');
    assert.equal(registerUrl.searchParams.get('intent'), 'existing_member');
    assert.equal(registerUrl.searchParams.get('return_to'), '/admin/users');
    assert.equal(registerUrl.searchParams.get('remember_me'), 'false');
    await verify(page, state);
    await page.getByRole('button', { name: '再次查詢', exact: true }).click();
    assert.equal(await page.getByLabel('手機號碼', { exact: true }).inputValue(), '');
    assert.equal(await page.getByLabel('入社時登記的 Email', { exact: true }).inputValue(), '');
    await page.getByRole('link', { name: '返回 LINE 登入', exact: true }).click();
    await entry.waitFor();
    assert.equal(new URL(page.url()).searchParams.get('remember_me'), 'false');
    await verify(page, state);
  });
}

test('受限與失敗時保留聯絡輸入、不顯示成功結果', async t => {
  const detail = '嘗試次數過多，請稍後再試';
  const { page, state } = await fixture(t, 390, { status: 429, detail, start: '/member-lookup' });
  await inputContact(page);
  await page.getByRole('button', { name: '寄送查詢結果', exact: true }).click();
  await page.getByRole('alert').getByText(detail, { exact: true }).waitFor();
  assert.equal(await page.getByLabel('手機號碼', { exact: true }).inputValue(), '+886 900-000-000');
  assert.equal(await page.getByLabel('入社時登記的 Email', { exact: true }).inputValue(), 'member@example.test');
  assert.equal(await page.getByRole('status').count(), 0);
  assert.equal(state.requests.length, 1);
  await verify(page, state);
});

test('已登入使用者也可獨立查詢，外站返回網址會改用安全站內目標', async t => {
  const { page, state } = await fixture(t, 1280, { signedIn: true, start: '/member-lookup?return_to=https%3A%2F%2Fevil.example&remember_me=false' });
  await page.getByRole('heading', { name: '查詢社員資格', exact: true }).waitFor();
  const backUrl = new URL(await page.getByRole('link', { name: '返回 LINE 登入', exact: true }).getAttribute('href'), base);
  assert.equal(backUrl.searchParams.get('return_to'), '/shop');
  await inputContact(page);
  await page.getByRole('button', { name: '寄送查詢結果', exact: true }).click();
  await page.getByRole('status').getByText(resultMessage, { exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/member-lookup');
  assert.equal(state.requests.length, 1);
  await verify(page, state);
});

test('快速重複點擊及回覆等待期間只送出一次查詢', async t => {
  const { page, state } = await fixture(t, 390, { deferResponse: true, start: '/member-lookup' });
  await inputContact(page);
  await page.getByRole('button', { name: '寄送查詢結果', exact: true }).dblclick();
  await state.requestStarted;
  const pending = page.getByRole('button', { name: '查詢送出中…', exact: true });
  assert.equal(await pending.isEnabled(), false);
  assert.equal(await page.getByLabel('手機號碼', { exact: true }).isEnabled(), false);
  assert.equal(await page.getByLabel('入社時登記的 Email', { exact: true }).isEnabled(), false);
  assert.equal(state.requests.length, 1);
  state.releaseResponse();
  await page.getByRole('status').getByText(resultMessage, { exact: true }).waitFor();
  assert.equal(state.requests.length, 1);
  await verify(page, state);
});
