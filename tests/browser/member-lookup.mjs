import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4194');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use an isolated local origin');
const base = target.origin;
const identity = { phone: '0912345678', email: 'member@example.test' };
const claimConsent = '我已閱讀並同意 LINE 登入及社員資料的使用說明。';
const loginConsent = '我已閱讀並同意十里方圓依上述用途使用及保存 LINE 登入資料。';

async function fixture(t, width, { signedIn = false, rejected = false, start = '/line-consent?return_to=%2Fadmin%2Fusers&remember_me=false' } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 950 }, serviceWorkers: 'block' });
  const state = { starts: [], lookups: [], unknown: [], errors: [], refreshes: 0 };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (path === '/isolated-authorize') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<h1>隔離 LINE 授權</h1>' });
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') {
      state.refreshes++;
      return route.fulfill(signedIn ? { json: { access_token: 'isolated', user: { id: 'member', display_name: '已登入測試帳號', user_role: 'customer', membership_type: 'nonmember' } } } : { status: 401, json: { detail: '未登入' } });
    }
    if (path === '/v1/auth/member-lookup') {
      state.lookups.push(request.method());
      return route.fulfill({ status: 410, json: { detail: '舊查詢流程不可使用' } });
    }
    if (path === '/v1/auth/line/start' && request.method() === 'POST') {
      state.starts.push(request.postDataJSON());
      return route.fulfill(rejected ? { status: 409, json: { detail: '社員資料無法核對，請確認電話號碼與 Email' } } : { json: { authorization_url: `${base}/isolated-authorize` } });
    }
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true, linked: false } });
    if (path === '/v1/meal-checkout/contact') return route.fulfill({ json: { phone: '0912345678' } });
    if (path === '/v1/meal-announcements') return route.fulfill({ json: [] });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (['/v1/products', '/v1/group-campaigns', '/v1/meal-events/calendar'].includes(path)) return route.fulfill({ json: [] });
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
  const phone = page.getByLabel('電話號碼', { exact: true });
  assert.equal(await phone.getAttribute('type'), 'tel');
  await phone.fill(identity.phone);
  await page.getByLabel('Email', { exact: true }).fill(identity.email);
  assert.equal(await page.getByLabel('姓名', { exact: true }).count(), 0);
  assert.equal(await page.getByLabel('社員編號', { exact: true }).count(), 0);
  await page.getByRole('button', { name: '繼續使用 LINE', exact: true }).click();
}

function assertRegistrationUrl(page, returnTo, rememberMe = 'false') {
  const url = new URL(page.url());
  assert.equal(url.pathname, '/register');
  assert.equal(url.searchParams.get('intent'), 'existing_member');
  assert.equal(url.searchParams.get('return_to'), returnTo);
  assert.equal(url.searchParams.get('remember_me'), rememberMe);
}

async function verify(page, state) {
  assert.deepEqual(state.lookups, [], '不可呼叫舊社員查詢寄信 API');
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}

for (const width of [390, 1280]) {
  test(`${width}px 不確定社員入口直接進既有社員驗證，電話與Email經同意後沿用LINE核對`, async t => {
    const { page, state } = await fixture(t, width);
    const entry = page.getByRole('link', { name: '不確定是否是社員？', exact: true });
    await entry.waitFor();
    const link = new URL(await entry.getAttribute('href'), base);
    assert.equal(link.pathname, '/register');
    assert.equal(link.searchParams.get('intent'), 'existing_member');
    await entry.click();
    await page.getByRole('heading', { name: '社員資料驗證', exact: true }).waitFor();
    assertRegistrationUrl(page, '/admin/users');
    assert.equal(await page.getByRole('button', { name: '寄送查詢結果', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '繼續使用 LINE', exact: true }).isEnabled(), false);
    await inputContact(page);
    assert.deepEqual(state.starts, []);
    assert.equal(await page.getByRole('button', { name: '使用LINE登入', exact: true }).isEnabled(), false);
    await page.getByLabel(claimConsent, { exact: true }).check();
    await verify(page, state);
    await page.getByRole('button', { name: '使用LINE登入', exact: true }).click();
    await page.getByRole('heading', { name: '隔離 LINE 授權', exact: true }).waitFor();
    assert.deepEqual(state.starts, [{ intent: 'member', remember_me: false, mode: 'login', member_claim: identity, data_consent_version: 'line-data-v1', return_to: '/admin/users' }]);
    await verify(page, state);
  });

  test(`${width}px 已有帳號直接LINE登入不要求或夾帶社員資料`, async t => {
    const { page, state } = await fixture(t, width, { start: '/member-lookup?return_to=%2Fcheckout&remember_me=false' });
    await page.getByRole('heading', { name: '社員資料驗證', exact: true }).waitFor();
    assertRegistrationUrl(page, '/checkout');
    if (width === 1280) {
      await inputContact(page);
      await page.getByLabel(claimConsent, { exact: true }).check();
    }
    await page.getByRole('button', { name: '曾用 LINE 登入，直接登入', exact: true }).click();
    await page.getByRole('heading', { name: '使用 LINE 登入', exact: true }).waitFor();
    assert.equal(await page.getByLabel('電話號碼', { exact: true }).count(), 0);
    assert.equal(await page.getByLabel('Email', { exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '使用LINE登入', exact: true }).isEnabled(), false);
    assert.equal(await page.getByLabel(loginConsent, { exact: true }).isChecked(), false);
    assert.deepEqual(state.starts, []);
    await page.getByLabel(loginConsent, { exact: true }).check();
    await verify(page, state);
    await page.getByRole('button', { name: '使用LINE登入', exact: true }).click();
    await page.getByRole('heading', { name: '隔離 LINE 授權', exact: true }).waitFor();
    assert.deepEqual(state.starts, [{ intent: 'nonmember', remember_me: false, mode: 'login', data_consent_version: 'line-data-v1', return_to: '/checkout' }]);
    await verify(page, state);
  });
}

for (const [returnTo, expected, rememberMe] of [
  ['/help?from=member#membership', '/help?from=member#membership', 'false'],
  ['https://evil.example/steal', '/shop', 'true'],
]) {
  test(`舊查詢連結保留安全返回目標與登入偏好：${expected}`, async t => {
    const { page, state } = await fixture(t, 390, { start: `/member-lookup?return_to=${encodeURIComponent(returnTo)}&remember_me=${rememberMe}` });
    await page.getByRole('heading', { name: '社員資料驗證', exact: true }).waitFor();
    assertRegistrationUrl(page, expected, rememberMe);
    assert.deepEqual(state.starts, []);
    await verify(page, state);
  });
}

test('社員核對失敗保留目前流程，不寄查詢信或擅自建立帳號', async t => {
  const { page, state } = await fixture(t, 390, { rejected: true, start: '/member-lookup?return_to=%2Fcheckout&remember_me=false' });
  await page.getByRole('heading', { name: '社員資料驗證', exact: true }).waitFor();
  await inputContact(page);
  await page.getByLabel(claimConsent, { exact: true }).check();
  await page.getByRole('button', { name: '使用LINE登入', exact: true }).click();
  await page.getByRole('alert').getByText('社員資料無法核對，請確認電話號碼與 Email', { exact: true }).waitFor();
  assertRegistrationUrl(page, '/checkout');
  assert.equal(state.starts.length, 1);
  await page.getByRole('button', { name: '上一步', exact: true }).click();
  assert.equal(await page.getByLabel('電話號碼', { exact: true }).inputValue(), identity.phone);
  assert.equal(await page.getByLabel('Email', { exact: true }).inputValue(), identity.email);
  await verify(page, state);
});

test('已登入使用者從新舊入口直接返回原頁，不重新核對或呼叫LINE', async t => {
  const returnTo = '/help?from=member#membership';
  const { page, state } = await fixture(t, 1280, { signedIn: true, start: `/member-lookup?return_to=${encodeURIComponent(returnTo)}&remember_me=false` });
  await page.waitForURL(`${base}${returnTo}`);
  for (const path of ['/register?intent=existing_member&', '/line-consent?']) {
    await page.goto(`${base}${path}return_to=${encodeURIComponent(returnTo)}&remember_me=false`);
    await page.waitForURL(`${base}${returnTo}`);
  }
  assert.deepEqual(state.starts, []);
  assert.equal(await page.getByLabel('電話號碼', { exact: true }).count(), 0);
  await verify(page, state);
});
