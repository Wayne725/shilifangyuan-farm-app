import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use isolated local LINE entry');
const base = target.origin;
const identity = { legal_name: '隔離社員', member_number: 'M001', email: 'member@example.test' };
const registrationConsent = '我已閱讀並同意 LINE 登入及社員資料的使用說明。';
async function fixture(t, width, { rejected = false } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block' });
  const writes = [], unknown = [], errors = [];
  await context.route('**/*', (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (path === '/isolated-authorize') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<h1>隔離 LINE 授權</h1>' });
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ status: 401, json: { detail: '未登入' } });
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true, linked: false } });
    if (path === '/v1/auth/line/start' && request.method() === 'POST') {
      writes.push(request.postDataJSON());
      return route.fulfill(rejected ? { status: 409, json: { detail: '社員資料無法核對，請確認姓名、社員編號與 Email' } } : { json: { authorization_url: `${base}/isolated-authorize` } });
    }
    unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`${base}/line-consent?return_to=%2Fcheckout&remember_me=false`);
  await page.getByRole('heading', { name: '使用 LINE 登入', exact: true }).waitFor();
  return { page, writes, unknown, errors };
}
async function openMemberRegistration(page) {
  await page.getByRole('link', { name: '我是社員', exact: true }).click();
  await page.getByRole('heading', { name: '填寫社員資料', exact: true }).waitFor();
}
async function fillMemberIdentity(page) {
  await page.getByLabel('姓名', { exact: true }).fill(identity.legal_name);
  await page.getByLabel('社員編號', { exact: true }).fill(identity.member_number);
  await page.getByLabel('Email', { exact: true }).fill(identity.email);
  await page.getByRole('button', { name: '繼續選擇註冊方式', exact: true }).click();
  await page.getByRole('button', { name: '使用 LINE 註冊', exact: true }).click();
}
for (const width of [1440, 390]) {
  test(`${width}px 我是社員直接進既有社員註冊，不得略過三項資料直接LINE建帳`, async (t) => {
    const { page, writes, unknown, errors } = await fixture(t, width);
    const member = page.getByRole('link', { name: '我是社員', exact: true });
    assert.equal(await member.count(), 1, '社員按鈕未連到既有社員註冊');
    await openMemberRegistration(page);
    assert.equal(new URL(page.url()).searchParams.get('intent'), 'existing_member');
    assert.equal(new URL(page.url()).searchParams.get('return_to'), '/checkout');
    assert.equal(await page.getByRole('button', { name: '繼續選擇註冊方式', exact: true }).isEnabled(), false);
    assert.deepEqual(writes, []);
    await fillMemberIdentity(page);
    assert.equal(await page.getByRole('button', { name: '使用LINE登入', exact: true }).isEnabled(), false);
    await page.getByLabel(registrationConsent, { exact: true }).check();
    await page.getByRole('button', { name: '放大', exact: true }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.getByRole('button', { name: '使用LINE登入', exact: true }).click();
    await page.getByRole('heading', { name: '隔離 LINE 授權', exact: true }).waitFor();
    assert.deepEqual(writes, [{ intent: 'member', remember_me: false, mode: 'login', member_claim: identity, data_consent_version: 'line-data-v1', return_to: '/checkout' }]);
    assert.deepEqual(unknown, []); assert.deepEqual(errors, []);
  });
}
test('非社員仍可直接LINE登入，不要求或傳送社員資料', async (t) => {
  const { page, writes, unknown } = await fixture(t, 390);
  await page.getByRole('button', { name: '我不是社員', exact: true }).click();
  assert.equal(await page.getByLabel('社員編號', { exact: true }).count(), 0);
  await page.getByLabel('我已閱讀並同意十里方圓依上述用途使用及保存 LINE 登入資料。', { exact: true }).check();
  await page.getByRole('button', { name: '使用LINE登入', exact: true }).click();
  await page.getByRole('heading', { name: '隔離 LINE 授權', exact: true }).waitFor();
  assert.equal(writes[0].intent, 'nonmember'); assert.equal('member_claim' in writes[0], false);
  assert.deepEqual(unknown, []);
});
test('社員核對失敗停留既有社員註冊，不前往授權或另建帳號', async (t) => {
  const { page, writes, unknown } = await fixture(t, 390, { rejected: true });
  await openMemberRegistration(page); await fillMemberIdentity(page);
  await page.getByLabel(registrationConsent, { exact: true }).check();
  await page.getByRole('button', { name: '使用LINE登入', exact: true }).click();
  await page.getByRole('alert').getByText(/社員資料無法核對/).waitFor();
  assert.equal(new URL(page.url()).pathname, '/register');
  assert.equal(writes.length, 1); assert.deepEqual(unknown, []);
});

test('已有網站帳號可直接LINE登入，不重走社員註冊', async (t) => {
  const { page, writes, unknown } = await fixture(t, 390);
  await page.getByRole('button', { name: '已有網站帳號，直接登入', exact: true }).click();
  assert.equal(await page.getByLabel('社員編號', { exact: true }).count(), 0);
  await page.getByLabel('我已閱讀並同意十里方圓依上述用途使用及保存 LINE 登入資料。', { exact: true }).check();
  await page.getByRole('button', { name: '使用LINE登入', exact: true }).click();
  await page.getByRole('heading', { name: '隔離 LINE 授權', exact: true }).waitFor();
  assert.equal('member_claim' in writes[0], false);
  assert.deepEqual(unknown, []);
});
