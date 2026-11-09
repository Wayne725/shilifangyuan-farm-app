import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use an isolated local origin');
const base = target.origin;
const match = { roster_entry_id: 'roster-1', member_number: 'OLD-001', legal_name: '名冊測試社員', phone_masked: '******0000' };
const confirmationLabel = '我已核對手機持有人身分及線下入社／繳費資料。';
const confirmButton = '確認授予正式社員資格';

async function fixture(t, width, { role = 'admin', status = null, previewError = null, grantError = null, deferPreview = false } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block', timezoneId: 'Asia/Taipei' });
  const state = { status, previews: [], grants: [], userReads: 0, unknown: [], errors: [], releasePreview: null };
  let previewStarted;
  state.previewStarted = new Promise(resolve => { previewStarted = resolve; });
  const item = () => ({
    id: 'buyer', membership_id: state.status ? 'membership-1' : null, membership_status: state.status,
    membership_type: state.status === 'active' ? 'member' : 'nonmember', display_name: '網站測試帳號',
    email: null, member_number: state.status ? match.member_number : null,
    is_active: true, user_role: 'customer', created_at: '2030-01-01T00:00:00Z',
  });
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return req.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'actor', display_name: '隔離測試使用者', email: 'actor@example.test', user_role: role } } });
    if (path === '/v1/admin/users') { state.userReads++; return route.fulfill({ json: { total: 1, items: [item()] } }); }
    if (path === '/v1/admin/users/buyer/membership-phone-check' && req.method() === 'POST') {
      state.previews.push(req.postDataJSON());
      if (deferPreview) await new Promise(resolve => { state.releasePreview = resolve; previewStarted(); });
      return route.fulfill({ status: previewError ? 409 : 200, json: previewError ? { detail: previewError } : match });
    }
    if (path === '/v1/admin/users/buyer/offline-membership' && req.method() === 'POST') {
      state.grants.push(req.postDataJSON());
      if (!grantError) state.status = 'active';
      return route.fulfill({ status: grantError ? 409 : 200, json: grantError ? { detail: grantError } : item() });
    }
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true, linked: false } });
    state.unknown.push(`${req.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.goto(`${base}/admin/users`);
  return { page, state };
}

async function openForm(page, suspended = false) {
  await page.getByRole('button', { name: suspended ? '核對並恢復社員資格' : '線下確認並設為社員', exact: true }).click();
  const phone = page.getByLabel('手機號碼', { exact: true });
  assert.equal(await phone.getAttribute('type'), 'tel');
  assert.equal(await phone.getAttribute('inputmode'), 'tel');
  assert.equal(await page.locator('.admin-offline-membership').getByLabel('社員編號', { exact: false }).count(), 0);
  return phone;
}

async function verify(page, state) {
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
}

for (const width of [390, 1280]) {
  test(`${width}px 手機核對只預覽，核對說明留空仍可勾選確認並沿用名冊編號`, async t => {
    const { page, state } = await fixture(t, width);
    const phone = await openForm(page);
    assert.equal(await page.getByRole('button', { name: confirmButton, exact: true }).isEnabled(), false);
    await phone.fill('+886 900-000-000');
    await page.getByRole('button', { name: '核對手機', exact: true }).click();
    const result = page.getByRole('status', { name: '手機核對結果' });
    await result.getByText(match.legal_name, { exact: true }).waitFor();
    assert.equal(await result.getByText(match.member_number, { exact: true }).count(), 1);
    assert.deepEqual(state.previews, [{ phone: '+886 900-000-000' }]);
    assert.equal(state.grants.length, 0);
    assert.equal(await page.getByRole('button', { name: confirmButton, exact: true }).isEnabled(), false);
    await page.getByLabel(confirmationLabel, { exact: true }).check();
    await verify(page, state);
    await page.getByRole('button', { name: confirmButton, exact: true }).click();
    await page.getByText('社員資格已啟用。', { exact: true }).waitFor();
    assert.equal(state.grants.length, 1);
    assert.deepEqual(state.grants[0], {
      confirmed_offline: true,
      confirmed_on: new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Taipei' }).format(new Date()),
      reason: '', phone: '+886 900-000-000', roster_entry_id: match.roster_entry_id,
    });
    assert.equal(state.status, 'active');
    await verify(page, state);
  });
}

test('恢復會籍仍需手機核對，修改電話會清除舊結果與人工確認', async t => {
  const { page, state } = await fixture(t, 390, { status: 'suspended' });
  const phone = await openForm(page, true);
  await phone.fill('0900000000');
  await page.getByRole('button', { name: '核對手機', exact: true }).click();
  await page.getByRole('status', { name: '手機核對結果' }).waitFor();
  await page.getByLabel('核對說明／收據參考', { exact: true }).fill('核對恢復資格');
  await page.getByLabel(confirmationLabel, { exact: true }).check();
  assert.equal(await page.getByRole('button', { name: confirmButton, exact: true }).isEnabled(), true);
  await phone.fill('0911111111');
  assert.equal(await page.getByRole('status', { name: '手機核對結果' }).count(), 0);
  assert.equal(await page.getByLabel(confirmationLabel, { exact: true }).isChecked(), false);
  assert.equal(await page.getByRole('button', { name: confirmButton, exact: true }).isEnabled(), false);
  assert.equal(state.grants.length, 0);
  await verify(page, state);
});

test('核對中的舊電話回覆不能授予已改號碼的帳號', async t => {
  const { page, state } = await fixture(t, 390, { deferPreview: true });
  const phone = await openForm(page);
  await phone.fill('0900000000');
  const request = page.waitForRequest(req => req.url().endsWith('/membership-phone-check'));
  await page.getByRole('button', { name: '核對手機', exact: true }).click();
  await request;
  await state.previewStarted;
  assert.equal(typeof state.releasePreview, 'function');
  await phone.fill('0911111111');
  const response = page.waitForResponse(res => res.url().endsWith('/membership-phone-check'));
  state.releasePreview();
  await (await response).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.getByRole('button', { name: '核對手機', exact: true }).waitFor();
  assert.equal(await page.getByRole('status', { name: '手機核對結果' }).count(), 0);
  assert.equal(await page.getByLabel(confirmationLabel, { exact: true }).isEnabled(), false);
  assert.equal(await page.getByRole('button', { name: confirmButton, exact: true }).isEnabled(), false);
  assert.equal(state.grants.length, 0);
  await verify(page, state);
});

test('重複手機的核對錯誤不授予資格', async t => {
  const detail = '此手機對應多筆社員名冊，請先核對名冊資料';
  const { page, state } = await fixture(t, 390, { previewError: detail });
  const phone = await openForm(page);
  await phone.fill('0900000000');
  await page.getByRole('button', { name: '核對手機', exact: true }).click();
  await page.getByRole('alert').getByText(detail, { exact: true }).waitFor();
  assert.equal(await page.getByRole('status', { name: '手機核對結果' }).count(), 0);
  assert.equal(await page.getByRole('button', { name: confirmButton, exact: true }).isEnabled(), false);
  assert.equal(state.grants.length, 0);
  await verify(page, state);
});

test('確認時名冊已變更，保留錯誤與非社員狀態', async t => {
  const detail = '社員名冊已變更，請重新核對手機';
  const { page, state } = await fixture(t, 1280, { grantError: detail });
  const phone = await openForm(page);
  await phone.fill('0900000000');
  await page.getByRole('button', { name: '核對手機', exact: true }).click();
  await page.getByRole('status', { name: '手機核對結果' }).waitFor();
  await page.getByLabel('核對說明／收據參考', { exact: true }).fill('人工核對線下資料');
  await page.getByLabel(confirmationLabel, { exact: true }).check();
  await page.getByRole('button', { name: confirmButton, exact: true }).click();
  await page.getByRole('alert').getByText(detail, { exact: true }).waitFor();
  assert.equal(state.status, null);
  assert.equal(await page.getByText('社員資格已啟用。', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('status', { name: '手機核對結果' }).count(), 0);
  assert.equal(await page.getByLabel(confirmationLabel, { exact: true }).isChecked(), false);
  assert.equal(await page.getByLabel(confirmationLabel, { exact: true }).isEnabled(), false);
  assert.equal(await page.getByRole('button', { name: confirmButton, exact: true }).isEnabled(), false);
  assert.equal(state.grants.length, 1);
  await verify(page, state);
});

test('一般會員不能看到手機核對入口，也不讀取管理員會員清單', async t => {
  const { page, state } = await fixture(t, 390, { role: 'customer' });
  await page.getByText('請使用管理員帳號登入。', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '核對手機', exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '線下確認並設為社員', exact: true }).count(), 0);
  assert.equal(state.userReads, 0);
  assert.equal(state.previews.length, 0);
  assert.equal(state.grants.length, 0);
  await verify(page, state);
});
