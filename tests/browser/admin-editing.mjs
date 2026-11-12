import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4192');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use isolated local fixtures only');
const base = target.origin;
const version = '2030-01-02T01:00:00Z';
const category = { id: 'category', name: '社務會議', is_active: true };
const adminPath = '/v1/admin/users/buyer/profile';
const rosterPath = '/v1/admin/member-roster/roster/contact';
const meetingPath = '/v1/admin/meetings/meeting';
const resolutionPath = `${meetingPath}/resolutions/resolution`;

async function fixture(t, path, role = 'admin', { linkedRoster = false } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: 'block', timezoneId: 'Asia/Taipei' });
  const state = {
    reads: [], writes: [], errors: [], unknown: [],
    meal: { id: 'meal', name: '隔離餐點', description: '原說明', price: 100, tax_type: 'taxable', is_active: true, updated_at: version, option_groups: [] },
    profile: { display_name: '原帳號姓名', phone: '0912345678', is_active: true },
    roster: { legal_name: '原名冊姓名', email: 'roster@example.test', phone: '0912345678', address: '原地址', updated_at: version },
    activity: { id: 'activity', title: '原活動', description: '', location: '原活動地點', starts_at: '2099-01-03T03:00:00Z',
      ends_at: '2099-01-03T05:00:00Z', registration_deadline: '2099-01-02T12:00:00Z', status: 'published',
      registration_count: 2, waitlist_count: 1, capacity: 3, updated_at: version },
    meeting: { id: 'meeting', category_id: 'category', category_name: category.name, title: '原會議', location: '原會議地點', starts_at: '2030-01-10T03:00:00Z',
      agenda: [{ title: '原議程', attachment: '保留' }], attended_count: 1, eligible_member_count: 2, attendance_rate: 0.5, can_delete: false,
      resolutions: [{ id: 'resolution', title: '原決議', resolution_text: '原內容', created_at: version }] },
  };
  await context.route('**/*', route => {
    const request = route.request();
    const url = new URL(request.url()), endpoint = url.pathname, method = request.method();
    if (url.origin !== base) return route.abort();
    if (!endpoint.startsWith('/v1/')) return route.continue();
    if (endpoint === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated',
      user: { id: role === 'admin' ? 'admin' : 'buyer', email: 'user@example.test',
        display_name: role === 'admin' ? '管理員' : state.profile.display_name, user_role: role, membership_type: 'nonmember' } } });
    if (method === 'GET') state.reads.push(endpoint);
    else {
      const payload = request.postData() ? request.postDataJSON() : {};
      state.writes.push({ endpoint, method, payload });
      let data;
      if (endpoint === '/v1/admin/meals/meal') data = Object.assign(state.meal, payload);
      else if ([adminPath, '/v1/auth/profile'].includes(endpoint)) data = Object.assign(state.profile, payload);
      else if (endpoint === '/v1/admin/activities/activity') data = Object.assign(state.activity, payload);
      else if (endpoint === meetingPath) data = Object.assign(state.meeting, payload);
      else if (endpoint === resolutionPath) data = Object.assign(state.meeting.resolutions[0], payload);
      else if (endpoint === `${resolutionPath}/revoke`) data = Object.assign(state.meeting.resolutions[0], { revoked_at: version });
      else if (endpoint === rosterPath) data = Object.assign(state.roster, payload);
      if (data) return route.fulfill({ json: data });
    }
    const payloads = {
      '/v1/platform': { orders_open: true }, '/v1/auth/line/status': { ready: true, linked: false },
      '/v1/members/me': { membership_type: 'nonmember' }, '/v1/notifications': [], '/v1/membership/charges': [],
      '/v1/me/points': { balance: 0 }, '/v1/me/badges': [], '/v1/me/surplus-distributions': [],
      '/v1/admin/meals': [state.meal], '/v1/admin/meal-events': [],
      '/v1/admin/meals/meal/edit-impact': [{ id: 'event', title: '已發布午餐' }],
      '/v1/admin/users': { items: [{ id: 'buyer', email: 'buyer@example.test', user_role: 'customer', created_at: version,
        membership_type: 'nonmember', ...state.profile }], total: 1 },
      '/v1/auth/profile': state.profile, [adminPath]: state.profile,
      '/v1/admin/member-roster': [{ id: 'roster', member_number: 'M1', legal_name: state.roster.legal_name, email_masked: 'r***@example.test',
        phone_masked: '******5678', share_count: 0, share_capital_amount: 0, is_active: true, claimed: linkedRoster }],
      [rosterPath]: state.roster, '/v1/admin/activities': [state.activity],
      '/v1/admin/meetings': [state.meeting], '/v1/admin/meeting-categories': [category],
      '/v1/admin/member-proposals': [], '/v1/admin/members': [],
      [`${resolutionPath}/history`]: [{ id: 'audit', actor: '管理員', created_at: version,
        action: 'meeting_resolution.update', before: { resolution_text: '原內容' }, after: { resolution_text: '新內容' } }],
    };
    if (method === 'GET' && Object.hasOwn(payloads, endpoint)) return route.fulfill({ json: payloads[endpoint] });
    state.unknown.push(`${method} ${endpoint}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated request' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(6500);
  page.on('pageerror', error => state.errors.push(error.message));
  await page.goto(`${base}${path}`);
  return { page, state };
}

async function evidence(page, state, name) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.deepEqual(state.errors, []);
  assert.deepEqual(state.unknown, []);
  if (process.env.ADMIN_EDITING_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.ADMIN_EDITING_ARTIFACT_DIR}/${name}-390.png`, fullPage: true });
}

test('390px 編輯餐點須確認已發布場次', async t => {
  const { page, state } = await fixture(t, '/admin/meals?tab=meals');
  await page.getByRole('button', { name: '編輯餐點', exact: true }).click();
  const form = page.getByRole('form', { name: '編輯餐點' });
  await form.getByLabel('餐點名稱').fill('更新餐點');
  await form.getByLabel('售價').fill('145');
  await form.getByText('已發布午餐').waitFor();
  const save = form.getByRole('button', { name: '儲存餐點' });
  assert.equal(await save.isDisabled(), true);
  await form.getByRole('checkbox').check();
  await evidence(page, state, 'edit-meal');
  await save.click();
  await page.getByRole('heading', { name: '更新餐點' }).waitFor();
  assert.equal(state.writes[0].payload.price, 145);
  assert.deepEqual(state.writes[0].payload.confirmed_event_ids, ['event']);
});

test('390px 只改餐點說明可直接儲存', async t => {
  const { page, state } = await fixture(t, '/admin/meals?tab=meals');
  await page.getByRole('button', { name: '編輯餐點', exact: true }).click();
  const form = page.getByRole('form', { name: '編輯餐點' });
  await form.getByLabel('說明').fill('更新餐點說明');
  assert.equal(await form.getByRole('checkbox').count(), 0);
  await evidence(page, state, 'edit-meal-description');
  await form.getByRole('button', { name: '儲存餐點' }).click();
  await page.getByText('更新餐點說明', { exact: true }).waitFor();
  assert.deepEqual(state.writes[0].payload.confirmed_event_ids, []);
  assert.equal(state.reads.includes('/v1/admin/meals/meal/edit-impact'), false);
});

for (const admin of [true, false]) {
  test(`390px ${admin ? '後台可更名並停用帳號' : '會員中心可更改姓名和電話'}`, async t => {
    const { page, state } = await fixture(t, admin ? '/admin/users' : '/account', admin ? 'admin' : 'customer');
    await page.getByRole('button', { name: '編輯帳號資料' }).click();
    const form = page.getByRole('form', { name: '編輯帳號資料' });
    await form.getByLabel('姓名', { exact: true }).fill('新帳號姓名');
    await form.getByLabel('聯絡電話').fill('0923456789');
    if (admin) {
      await form.getByRole('combobox', { name: '帳號狀態' }).selectOption('inactive');
      await form.getByRole('checkbox', { name: '確認停用登入與下單' }).check();
    }
    await evidence(page, state, admin ? 'edit-admin-account' : 'edit-own-account');
    await form.getByRole('button', { name: '儲存帳號資料' }).click();
    await page.getByRole('heading', { name: /新帳號姓名/ }).waitFor();
    assert.equal(state.writes[0].payload.phone, '0923456789');
    assert.equal(state.writes[0].payload.is_active, admin ? false : undefined);
  });
}

test('390px 更正名冊地址後顯示新姓名', async t => {
  const { page, state } = await fixture(t, '/admin/users?tab=roster');
  await page.getByRole('button', { name: '更正名冊資料' }).click();
  const form = page.getByRole('form', { name: '更正社員名冊' });
  await form.getByLabel('社員姓名').fill('新名冊姓名');
  await form.getByLabel('地址').fill('隔離測試新地址');
  await evidence(page, state, 'edit-roster');
  await form.getByRole('button', { name: '儲存名冊更正' }).click();
  await page.getByRole('heading', { name: /新名冊姓名/ }).waitFor();
  assert.equal(state.writes[0].payload.address, '隔離測試新地址');
});

test('390px 已連結名冊提示更新社員本人資料', async t => {
  const { page, state } = await fixture(t, '/admin/users?tab=roster', 'admin', { linkedRoster: true });
  const card = page.locator('.roster-entry-card');
  await card.getByText('此社員已連結帳號，請改社員本人資料').waitFor();
  assert.equal(await card.getByRole('button', { name: '更正名冊資料' }).count(), 0);
  assert.equal(state.reads.includes(rosterPath), false);
  await evidence(page, state, 'linked-roster');
});

test('390px 編輯活動地點時顯示通知提示並保存', async t => {
  const { page, state } = await fixture(t, '/admin/social?tab=activities');
  await page.getByRole('button', { name: '編輯活動', exact: true }).click();
  const form = page.getByRole('form', { name: '編輯活動' });
  await form.getByLabel('活動標題').fill('更新活動');
  await form.getByLabel('地點').fill('更新活動地點');
  await form.getByText('變更時間或地點後，系統會通知報名者。').waitFor();
  await evidence(page, state, 'edit-activity');
  await form.getByRole('button', { name: '儲存活動' }).click();
  await page.getByRole('heading', { name: '更新活動' }).waitFor();
  assert.equal(state.writes[0].payload.expected_updated_at, version);
});

test('390px 編輯會議與決議，撤銷後仍可查閱修改紀錄', async t => {
  const { page, state } = await fixture(t, '/admin/social?tab=meetings');
  await page.getByRole('button', { name: '出席與決議' }).click();
  await page.getByRole('button', { name: '編輯會議', exact: true }).click();
  const meeting = page.getByRole('form', { name: '編輯會議' });
  await meeting.getByLabel('會議名稱').fill('新會議');
  await evidence(page, state, 'edit-meeting');
  await meeting.getByRole('button', { name: '儲存會議' }).click();
  await page.getByRole('heading', { name: '新會議' }).waitFor();
  assert.deepEqual(state.writes[0].payload.agenda, [{ title: '原議程', attachment: '保留' }]);
  await page.getByRole('button', { name: '編輯決議' }).click();
  const resolution = page.getByRole('form', { name: '編輯決議' });
  await resolution.getByLabel('決議內容').fill('新內容');
  await resolution.getByRole('button', { name: '儲存決議修改' }).click();
  await page.getByText('新內容', { exact: true }).waitFor();
  await page.getByRole('button', { name: '撤銷決議', exact: true }).click();
  await page.getByRole('button', { name: '確認撤銷決議', exact: true }).click();
  await page.getByText('已撤銷', { exact: true }).waitFor();
  await page.getByText('修改紀錄', { exact: true }).click();
  await page.getByText(/修改前.*原內容/).waitFor();
  await evidence(page, state, 'edit-resolution');
});
