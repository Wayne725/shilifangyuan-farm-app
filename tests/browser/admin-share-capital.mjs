import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const testUrl = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['127.0.0.1', 'localhost'].includes(testUrl.hostname) || !['http:', 'https:'].includes(testUrl.protocol)
  || testUrl.pathname !== '/' || testUrl.username || testUrl.password || testUrl.search || testUrl.hash) {
  throw new Error('Share-capital browser acceptance must use an isolated local origin');
}
const base = testUrl.origin;
const originalVersion = '2026-09-17T01:00:00Z';
const changedVersion = '2026-09-17T02:00:00Z';
const rosterPath = '/v1/admin/member-roster';
const membersPath = '/v1/admin/members';

async function fixture(t, userRole = 'admin') {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
  const state = {
    roster: [
      { id: 'roster-claimed', member_number: 'ISOLATED-001', legal_name: '隔離已認領社員', email_masked: 'm***@example.test', phone_masked: '******0001', share_capital_amount: 1000, share_count: 1, is_active: true, claimed: true, claimed_at: originalVersion, updated_at: originalVersion },
      { id: 'roster-unclaimed', member_number: 'ISOLATED-002', legal_name: '隔離未認領社員', email_masked: 'n***@example.test', phone_masked: '', share_capital_amount: 0, share_count: 0, is_active: true, claimed: false, updated_at: originalVersion },
    ],
    members: [{ id: 'membership-claimed', user_id: 'isolated-member', status: 'active', member_number: 'ISOLATED-001', share_capital_amount: 1000, share_count: 1, updated_at: originalVersion }],
    reads: [], writes: [], unknownApi: [],
  };
  await context.route('**/*', (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== base) return route.abort();
    if (!url.pathname.startsWith('/v1/')) return route.continue();
    const path = url.pathname;
    const method = request.method();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated-share-edit-token', user: { id: 'isolated-user', email: 'operator@example.test', display_name: '隔離操作員', user_role: userRole } } });
    if (method === 'GET') {
      state.reads.push(path);
      if (path === rosterPath) return route.fulfill({ json: state.roster });
      if (path === membersPath) return route.fulfill({ json: state.members });
      if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'member', membership: { member_number: 'ISOLATED-001', share_capital_amount: 999999, share_count: 999, share_certificate_number: 'MUST-NOT-DISPLAY' }, directory: { is_public: false } } });
      if (['/v1/admin/activities', '/v1/admin/member-proposals', '/v1/admin/membership-applications', '/v1/wishes', '/v1/meetings', '/v1/activities', '/v1/member-proposals', '/v1/notifications'].includes(path)) return route.fulfill({ json: [] });
    }
    if (method === 'PATCH' && [ `${rosterPath}/roster-claimed/shares`, `${rosterPath}/roster-unclaimed/shares`, `${membersPath}/membership-claimed/shares` ].includes(path)) {
      const payload = request.postDataJSON();
      state.writes.push({ path, payload });
      const target = path.includes('roster-unclaimed') ? state.roster[1] : path.startsWith(membersPath) ? state.members[0] : state.roster[0];
      if (payload.expected_updated_at !== target.updated_at) return route.fulfill({ status: 409, json: { detail: '股籍已由其他管理者更新，請重新讀取' } });
      const update = { share_capital_amount: payload.share_capital_amount, share_count: payload.share_count, updated_at: '2026-09-17T03:00:00Z' };
      Object.assign(target, update);
      if (target !== state.roster[1]) { Object.assign(state.roster[0], update); Object.assign(state.members[0], update); }
      return route.fulfill({ json: target });
    }
    state.unknownApi.push(`${method} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API，禁止連線真實後端' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  return { page, state };
}

function rosterCard(page, claimed = true) {
  return page.locator('.roster-entry-card').filter({ has: page.getByRole('heading', { name: claimed ? 'ISOLATED-001 · 隔離已認領社員' : 'ISOLATED-002 · 隔離未認領社員', exact: true }) });
}

function memberCard(page) {
  return page.locator('.admin-review-card').filter({ has: page.getByRole('heading', { name: 'ISOLATED-001', exact: true }) });
}

async function beginRosterEdit(page, claimed = true) {
  await page.goto(`${base}/admin/social`);
  await page.getByRole('tab', { name: '社員名冊', exact: true }).click();
  const card = rosterCard(page, claimed);
  await card.getByRole('button', { name: '編輯股金／股數', exact: true }).click();
  return { card, form: card.getByRole('form', { name: '修改股金與股數', exact: true }) };
}

test('已認領名冊可修正股金股數，必填原因且成功同步重新讀取兩份列表', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t);
  const { card, form } = await beginRosterEdit(page);
  await form.getByText(/僅修正股籍，不會收款或退款/).waitFor();
  const save = form.getByRole('button', { name: '儲存股籍修正', exact: true });
  assert.equal(await save.isDisabled(), true);
  await form.getByLabel('股金（元）', { exact: true }).fill('2400');
  await form.getByLabel('股數', { exact: true }).fill('4');
  await form.getByLabel('修改原因', { exact: true }).fill('  核對合作社股籍紀錄  ');
  const rosterReads = state.reads.filter((path) => path === rosterPath).length;
  const memberReads = state.reads.filter((path) => path === membersPath).length;
  await save.click();
  await card.getByRole('status').getByText('股籍已更新；未收款或退款。', { exact: true }).waitFor();
  await card.getByText('股金 2400 元 · 4 股', { exact: true }).waitFor();
  assert.deepEqual(state.writes, [{ path: `${rosterPath}/roster-claimed/shares`, payload: { share_capital_amount: 2400, share_count: 4, reason: '核對合作社股籍紀錄', expected_updated_at: originalVersion } }]);
  assert.ok(state.reads.filter((path) => path === rosterPath).length > rosterReads);
  assert.ok(state.reads.filter((path) => path === membersPath).length > memberReads);
  await page.getByRole('tab', { name: '審核工作', exact: true }).click();
  await memberCard(page).getByText('股金 2400 元 · 4 股', { exact: true }).waitFor();
  assert.doesNotMatch(await page.locator('main').innerText(), /股票號碼|股票代號/);
  assert.deepEqual(state.unknownApi, []);
});

test('會籍股籍可反向修正並同步名冊，零元零股不觸發退款', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/admin/social`);
  const card = memberCard(page);
  await card.getByRole('button', { name: '編輯股金／股數', exact: true }).click();
  const form = card.getByRole('form', { name: '修改股金與股數', exact: true });
  await form.getByLabel('股金（元）', { exact: true }).fill('0');
  await form.getByLabel('股數', { exact: true }).fill('0');
  await form.getByLabel('修改原因', { exact: true }).fill('修正暫存股籍，非退款作業');
  await form.getByRole('button', { name: '儲存股籍修正', exact: true }).click();
  await card.getByText('股金 0 元 · 0 股', { exact: true }).waitFor();
  await page.getByRole('tab', { name: '社員名冊', exact: true }).click();
  await rosterCard(page).getByText('股金 0 元 · 0 股', { exact: true }).waitFor();
  assert.equal(state.writes.length, 1);
  assert.equal(state.writes[0].path, `${membersPath}/membership-claimed/shares`);
  assert.equal(state.members[0].status, 'active');
  assert.deepEqual(state.unknownApi, []);
});

test('未認領名冊也可編輯，負數小數空值及超過整數上限皆不能送出', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t);
  const { card, form } = await beginRosterEdit(page, false);
  const amount = form.getByLabel('股金（元）', { exact: true });
  const count = form.getByLabel('股數', { exact: true });
  const save = form.getByRole('button', { name: '儲存股籍修正', exact: true });
  await form.getByLabel('修改原因', { exact: true }).fill('人工核對股籍');
  for (const value of ['-1', '1.5', '', '2147483648']) {
    await amount.fill(value);
    assert.equal(await save.isDisabled(), true);
  }
  await amount.fill('2000');
  await count.fill('1.5');
  assert.equal(await save.isDisabled(), true);
  await count.fill('2');
  assert.equal(state.writes.length, 0);
  await save.click();
  await card.getByText('股金 2000 元 · 2 股', { exact: true }).waitFor();
  assert.equal(state.writes[0].path, `${rosterPath}/roster-unclaimed/shares`);
  assert.equal(state.members[0].share_capital_amount, 1000);
  assert.deepEqual(state.unknownApi, []);
});

test('股籍409保留輸入與舊版本，重讀且明確核對後才可使用新版本送出', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t);
  const { card, form } = await beginRosterEdit(page);
  await form.getByLabel('股金（元）', { exact: true }).fill('3000');
  await form.getByLabel('股數', { exact: true }).fill('3');
  await form.getByLabel('修改原因', { exact: true }).fill('核對原始股籍文件');
  Object.assign(state.roster[0], { share_capital_amount: 2500, share_count: 5, updated_at: changedVersion });
  Object.assign(state.members[0], { share_capital_amount: 2500, share_count: 5, updated_at: changedVersion });
  const save = form.getByRole('button', { name: '儲存股籍修正', exact: true });
  await save.click();
  await form.getByRole('alert').getByText('股籍已由其他管理者更新，請重新讀取', { exact: true }).waitFor();
  assert.equal(await form.getByLabel('股金（元）', { exact: true }).inputValue(), '3000');
  assert.equal(await form.getByLabel('股數', { exact: true }).inputValue(), '3');
  assert.equal(await form.getByLabel('修改原因', { exact: true }).inputValue(), '核對原始股籍文件');
  assert.equal(await save.isDisabled(), true);
  assert.equal(state.writes.length, 1);
  await form.getByRole('button', { name: '重新讀取最新股籍', exact: true }).click();
  await form.getByText('最新股金 2500 元 · 5 股', { exact: true }).waitFor();
  assert.equal(await form.getByLabel('股金（元）', { exact: true }).inputValue(), '3000');
  assert.equal(await save.isDisabled(), true);
  assert.equal(state.writes.length, 1);
  await form.getByLabel('我已核對最新股籍，確認以目前輸入修正', { exact: true }).check();
  if (process.env.BROWSER_SCREENSHOT_DIR) {
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, left: 0, behavior: 'instant' }); });
    await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/admin-shares-conflict-mobile.png`, fullPage: true });
  }
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  await save.click();
  await card.getByText('股金 3000 元 · 3 股', { exact: true }).waitFor();
  assert.equal(state.writes.length, 2);
  assert.equal(state.writes[0].payload.expected_updated_at, originalVersion);
  assert.equal(state.writes[1].payload.expected_updated_at, changedVersion);
  assert.deepEqual(state.unknownApi, []);
});

test('一般社員看不到股籍編輯，社務頁仍不顯示股金股數或股票號碼', { timeout: 20000 }, async (t) => {
  const { page, state } = await fixture(t, 'customer');
  await page.goto(`${base}/admin/social`);
  await page.getByRole('heading', { name: '社務管理只向管理者開放。', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '編輯股金／股數', exact: true }).count(), 0);
  assert.equal(state.reads.some((path) => path.startsWith('/v1/admin/')), false);
  await page.goto(`${base}/social`);
  await page.getByText('ISOLATED-001', { exact: true }).waitFor();
  assert.doesNotMatch(await page.locator('main').innerText(), /股金|股數|股票號碼|999999|MUST-NOT-DISPLAY/);
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.unknownApi, []);
});
