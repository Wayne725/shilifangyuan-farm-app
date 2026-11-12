import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Local isolated fixtures only');
const base = target.origin;
const version = '2030-01-01T00:00:00Z';
const initialCategories = [
  { id: 'affairs', name: '社務會議', sort_order: 0, is_active: true, updated_at: version },
  { id: 'general', name: '社員大會', sort_order: 10, is_active: true, updated_at: version },
  { id: 'history', name: '歷史委員會', sort_order: 20, is_active: false, updated_at: version },
];
const makeMeeting = (id, categoryId, title) => ({ id, category_id: categoryId, category_name: initialCategories.find(c => c.id === categoryId).name, meeting_type: 'affairs', title,
  starts_at: '2030-02-01T04:00:00Z', location: '隔離會議室', agenda: [{ title: '採購規劃' }], resolutions: [{ id: `r-${id}`, title: '預算決議', resolution_text: '核定社區種植預算' }], attended_count: 1, eligible_member_count: 2, attendance_rate: 0.5 });
async function fixture(t, { width = 1280, role = 'admin', membership = 'active', categoryFailure = 0, meetingFailure = 0 } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE }); t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 900 }, timezoneId: 'Asia/Taipei', reducedMotion: 'reduce', serviceWorkers: 'block' });
  const state = { reads: [], writes: [], unknown: [], errors: [], categoryFailure, meetingFailure, patchFailure: 0, createFailure: 0, createDelay: 0,
    categories: structuredClone(initialCategories), meetings: [makeMeeting('meeting-active', 'affairs', '二月社務會議'), makeMeeting('meeting-history', 'history', '舊年度委員會')] };
  const mappedMeetings = () => state.meetings.map(meeting => ({ ...meeting, category_name: state.categories.find(category => category.id === meeting.category_id)?.name || meeting.category_name }));
  await context.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return method === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') {
      if (role === 'guest' || state.sessionRevoked) return route.fulfill({ status: 401, json: { detail: '隔離登入已失效' } });
      return route.fulfill({ json: { access_token: 'isolated-meetings', user: { id: 'local-user', email: 'isolated@example.test', display_name: '隔離使用者', user_role: role, membership_type: membership === 'active' ? 'member' : 'nonmember' } } });
    }
    if (method === 'GET') state.reads.push(path); else state.writes.push({ method, path, body: req.postDataJSON() });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/admin/membership/admissions') return route.fulfill({ json: { items: [], total: 0 } });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: ['active', 'suspended'].includes(membership) ? 'member' : membership === 'trainee' ? 'trainee' : 'nonmember', membership: { status: membership } } });
    if (path === '/v1/meeting-categories' || path === '/v1/admin/meeting-categories') {
      if (method === 'POST' && path.startsWith('/v1/admin/')) {
        const body = req.postDataJSON(); const record = { ...body, id: 'new-category', updated_at: version }; state.categories.push(record); return route.fulfill({ json: record });
      }
      if (method === 'GET') {
        if (state.categoryFailure === 401) state.sessionRevoked = true;
        return route.fulfill({ status: state.categoryFailure || 200, json: state.categoryFailure ? { detail: '隔離類別無法讀取' } : state.categories.filter(c => path.startsWith('/v1/admin/') || c.is_active).sort((a,b) => a.sort_order - b.sort_order) });
      }
    }
    if (path.startsWith('/v1/admin/meeting-categories/') && method === 'PATCH') {
      const record = state.categories.find(c => c.id === path.split('/').at(-1));
      if (state.patchFailure) {
        if (state.patchFailure === 401) state.sessionRevoked = true;
        return route.fulfill({ status: state.patchFailure, json: { detail: state.patchFailure === 409 ? '類別資料已更新，請重新載入後核對' : '隔離儲存失敗' } });
      }
      const body = req.postDataJSON(); assert.equal(body.expected_updated_at, record.updated_at);
      Object.assign(record, { name: body.name, sort_order: body.sort_order, is_active: body.is_active, updated_at: '2030-01-02T00:00:00Z' }); return route.fulfill({ json: record });
    }
    if (path === '/v1/meetings' || path === '/v1/admin/meetings') {
      if (method === 'POST' && path.startsWith('/v1/admin/')) {
        if (state.createDelay) await new Promise(resolve => setTimeout(resolve, state.createDelay));
        if (state.createFailure) return route.fulfill({ status: state.createFailure, json: { detail: '此會議類別已停用，請重新選擇' } });
        const body = req.postDataJSON(), record = { ...makeMeeting('new-meeting', 'affairs', body.title), ...body }; state.meetings.push(record); return route.fulfill({ json: record });
      }
      if (method === 'GET') {
        if (state.meetingFailure === 401) state.sessionRevoked = true;
        return route.fulfill({ status: state.meetingFailure || 200, json: state.meetingFailure ? { detail: '隔離會議讀取失敗' } : mappedMeetings() });
      }
    }
    if (['/v1/admin/activities', '/v1/admin/member-proposals', '/v1/admin/membership-applications', '/v1/admin/members', '/v1/admin/member-roster', '/v1/wishes', '/v1/notifications'].includes(path) && method === 'GET') return route.fulfill({ json: [] });
    state.unknown.push(`${method} ${path}`); return route.fulfill({ status: 404, json: { detail: '未定義隔離API' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000); page.on('pageerror', e => state.errors.push(e.message));
  return { page, state, admin: async () => { await page.goto(`${base}/admin/social`); await page.getByRole('tab', { name: '會議管理', exact: true }).click(); await page.getByRole('heading', { name: '二月社務會議', exact: true }).waitFor(); }, member: () => page.goto(`${base}/meetings`) };
}
async function clean(page, state) { assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []); assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)); }
async function fillMeeting(page) {
  await page.getByRole('button', { name: '建立會議', exact: true }).click();
  const form = page.getByRole('form', { name: '建立各項會議', exact: true });
  await form.getByLabel('建立會議類別', { exact: true }).selectOption('affairs');
  await form.getByLabel('會議名稱', { exact: true }).fill('新會議草稿');
  await form.getByLabel('開始時間', { exact: true }).fill('2030-02-02T12:00');
  await form.getByLabel('地點', { exact: true }).fill('隔離新場地');
  await form.getByLabel('議程（每行一項）', { exact: true }).fill('議程甲\n議程乙'); return form;
}
for (const width of [1280, 390]) {
  test(`${width}px 管理員新增、改名、排序、停用類別，歷史與查詢同步`, async t => {
    const { page, state, admin } = await fixture(t, { width, membership: 'nonmember' }); await admin();
    assert.ok(state.reads.includes('/v1/admin/meetings')); assert.ok(!state.reads.includes('/v1/meetings'));
    await page.getByText('會議類別', { exact: true }).filter({ hasNot: page.locator('option') }).first().click();
    const add = page.getByRole('form', { name: '新增會議類別', exact: true });
    await add.getByLabel('新類別名稱', { exact: true }).fill('理事會'); await add.getByLabel('新類別排序', { exact: true }).fill('5');
    assert.equal(state.writes.length, 0); await add.getByRole('button', { name: '新增類別', exact: true }).click();
    await page.getByText('會議類別已新增', { exact: true }).waitFor();
    let row = page.getByRole('article', { name: '類別 社務會議', exact: true }); await row.getByRole('button', { name: '編輯', exact: true }).click();
    await row.getByLabel('類別名稱', { exact: true }).fill('日常社務'); await row.getByLabel('排序', { exact: true }).fill('30'); await row.getByLabel('類別狀態', { exact: true }).selectOption('inactive');
    assert.equal(state.writes.length, 1); await row.getByRole('button', { name: '儲存類別', exact: true }).click();
    await page.getByText('會議類別已更新', { exact: true }).waitFor(); row = page.getByRole('article', { name: '類別 日常社務', exact: true }); await row.getByRole('button', { name: '編輯', exact: true }).waitFor();
    assert.deepEqual(state.writes.map(w => w.method), ['POST', 'PATCH']); assert.equal(state.writes[1].body.expected_updated_at, version);
    assert.deepEqual(await page.locator('.meeting-category-summary > strong').allTextContents(), ['理事會', '社員大會', '歷史委員會', '日常社務']);
    await page.getByLabel('查詢會議類別', { exact: true }).selectOption('affairs'); assert.equal(await page.locator('.meeting-admin-card').count(), 1); assert.match(await page.locator('.meeting-admin-card').innerText(), /日常社務/);
    await page.getByRole('button', { name: '建立會議', exact: true }).click(); assert.equal(await page.getByLabel('建立會議類別', { exact: true }).locator('option[value="affairs"]').count(), 0);
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, left: 0, behavior: "instant" }); });
    if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/meeting-admin-${width}.png`, fullPage: true });
    await clean(page, state);
  });
  test(`${width}px 社員可查啟用及停用類別歷史、議程、決議`, async t => {
    const { page, state, member } = await fixture(t, { width, role: 'customer' }); await member(); await page.getByRole('heading', { name: '各項會議', exact: true }).waitFor();
    await page.getByText('共 2 場會議', { exact: true }).waitFor(); await page.getByLabel('查詢會議類別', { exact: true }).selectOption('history'); assert.equal(await page.locator('.meeting-list article').count(), 1);
    await page.getByRole('heading', { name: '舊年度委員會', exact: true }).waitFor(); await page.getByRole('button', { name: '清除查詢', exact: true }).click();
    await page.getByLabel('查詢會議關鍵字', { exact: true }).fill('社區種植'); assert.equal(await page.locator('.meeting-list article').count(), 2);
    await page.getByLabel('查詢會議關鍵字', { exact: true }).fill('不存在'); await page.getByText('沒有符合條件的會議', { exact: true }).waitFor();
    await page.getByRole('button', { name: '清除查詢', exact: true }).click(); assert.equal(await page.getByRole('button', { name: '建立會議', exact: true }).count(), 0);
    await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo({ top: 0, left: 0, behavior: "instant" }); });
    if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/meetings-member-${width}.png`, fullPage: true });
    assert.deepEqual(state.writes, []); await clean(page, state);
  });
}
test('類別409保留草稿，重讀核對前不能再送出', async t => {
  const { page, state, admin } = await fixture(t); await admin(); await page.locator('.meeting-category-panel > summary').click();
  let row = page.getByRole('article', { name: '類別 社務會議', exact: true }); await row.getByRole('button', { name: '編輯', exact: true }).click(); await row.getByLabel('類別名稱', { exact: true }).fill('我的草稿');
  state.patchFailure = 409; state.categories[0].name = '其他管理員已改名'; state.categories[0].updated_at = '2030-01-03T00:00:00Z';
  await row.getByRole('button', { name: '儲存類別', exact: true }).click(); await row.getByRole('alert').waitFor(); assert.equal(await row.getByLabel('類別名稱', { exact: true }).inputValue(), '我的草稿'); assert.equal(await row.getByRole('button', { name: '儲存類別', exact: true }).isEnabled(), false);
  await row.getByRole('button', { name: '重新讀取最新類別', exact: true }).click(); row = page.getByRole('article', { name: '類別 其他管理員已改名', exact: true });
  await row.getByText('目前類別：其他管理員已改名 · 排序 0 · 啟用', { exact: true }).waitFor(); assert.equal(await row.getByLabel('類別名稱', { exact: true }).inputValue(), '我的草稿'); assert.equal(await row.getByRole('button', { name: '儲存類別', exact: true }).isEnabled(), false);
  await row.getByLabel('已核對最新類別，確認儲存目前輸入', { exact: true }).check(); state.patchFailure = 0; await row.getByRole('button', { name: '儲存類別', exact: true }).click(); await page.getByText('會議類別已更新', { exact: true }).waitFor();
  assert.equal(state.writes[1].body.expected_updated_at, '2030-01-03T00:00:00Z'); await clean(page, state);
});
test('類別儲存失敗保留草稿，取消不修改資料', async t => {
  const { page, state, admin } = await fixture(t); await admin(); await page.locator('.meeting-category-panel > summary').click();
  const row = page.getByRole('article', { name: '類別 社務會議', exact: true }); await row.getByRole('button', { name: '編輯', exact: true }).click(); await row.getByLabel('類別名稱', { exact: true }).fill('失敗草稿');
  state.patchFailure = 503; await row.getByRole('button', { name: '儲存類別', exact: true }).click(); await row.getByText('隔離儲存失敗', { exact: true }).waitFor(); assert.equal(await row.getByLabel('類別名稱', { exact: true }).inputValue(), '失敗草稿');
  await row.getByRole('button', { name: '取消編輯', exact: true }).click(); assert.equal(state.writes.length, 1); assert.equal(state.categories[0].name, '社務會議'); await clean(page, state);
});
test('建立會議只在確認後送出類別與議程，忙碌防重複', async t => {
  const { page, state, admin } = await fixture(t); await admin(); const form = await fillMeeting(page); assert.equal(state.writes.length, 0);
  assert.equal(await form.getByLabel('建立會議類別', { exact: true }).locator('option[value="history"]').count(), 0);
  state.createDelay = 500; await form.getByRole('button', { name: '確認建立會議', exact: true }).click(); assert.equal(await form.getByRole('button', { name: '建立中…', exact: true }).isEnabled(), false);
  await page.getByText('會議已建立', { exact: true }).waitFor(); await page.getByRole('heading', { name: '新會議草稿', exact: true }).waitFor();
  assert.equal(state.writes.length, 1); assert.deepEqual(state.writes[0].body, { category_id: 'affairs', title: '新會議草稿', agenda: [{ title: '議程甲' }, { title: '議程乙' }], starts_at: '2030-02-02T04:00:00.000Z', location: '隔離新場地' }); await clean(page, state);
});
test('建立時類別被停用，409保留草稿並重讀可用類別', async t => {
  const { page, state, admin } = await fixture(t); await admin(); const form = await fillMeeting(page); state.categories[0].is_active = false; state.createFailure = 409;
  await form.getByRole('button', { name: '確認建立會議', exact: true }).click(); await form.getByText('此會議類別已停用，請重新選擇', { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector('select[aria-label="建立會議類別"] option:checked')?.textContent.includes('已停用'));
  assert.equal(await form.getByLabel('會議名稱', { exact: true }).inputValue(), '新會議草稿'); assert.equal(await form.getByRole('button', { name: '確認建立會議', exact: true }).isEnabled(), false);
  assert.equal(state.writes.length, 1); await clean(page, state);
});
for (const membership of ['nonmember', 'trainee', 'suspended']) test(`${membership} 不讀取會議及類別且無管理介面`, async t => {
  const { page, state, member } = await fixture(t, { role: 'customer', membership }); await member(); await page.getByRole('heading', { name: '此功能開放給正式社員', exact: true }).waitFor();
  assert.ok(!state.reads.some(path => ['/v1/meetings', '/v1/meeting-categories'].includes(path))); await page.goto(`${base}/admin/social`); await page.getByRole('heading', { name: '社務管理只向管理者開放。', exact: true }).waitFor(); assert.ok(!state.reads.some(path => path.startsWith('/v1/admin/'))); await clean(page, state);
});
for (const endpoint of ['meeting', 'category']) for (const status of [401, 403]) test(`${endpoint} API ${status} 覆蓋社員快取資格，不再重試且隱藏會議與類別`, async t => {
  const { page, state, member } = await fixture(t, { role: 'customer', [`${endpoint}Failure`]: status }); await member();
  await page.getByRole('heading', { name: status === 401 ? '登入後查看各項會議' : '此功能開放給正式社員', exact: true }).waitFor();
  assert.equal(await page.locator('.meeting-list article, .meeting-filters').count(), 0);
  assert.equal(state.reads.filter(path => path === (endpoint === 'meeting' ? '/v1/meetings' : '/v1/meeting-categories')).length, 1);
  await clean(page, state);
});
for (const status of [401, 403]) test(`管理員類別修改 ${status} 後清除管理草稿及歷史顯示`, async t => {
  const { page, state, admin } = await fixture(t); await admin(); await page.locator('.meeting-category-panel > summary').click();
  const row = page.getByRole('article', { name: '類別 社務會議', exact: true }); await row.getByRole('button', { name: '編輯', exact: true }).click(); await row.getByLabel('類別名稱', { exact: true }).fill('不得殘留的管理草稿');
  state.patchFailure = status; await row.getByRole('button', { name: '儲存類別', exact: true }).click();
  await page.getByText(status === 401 ? '社務管理只向管理者開放。' : '會議管理權限已失效', { exact: true }).waitFor();
  assert.equal(await page.locator('.meeting-category-row, .meeting-admin-card, .meeting-filters').count(), 0); assert.equal(await page.locator('input').evaluateAll(inputs => inputs.some(input => input.value === '不得殘留的管理草稿')), false);
  await clean(page, state);
});
test('類別服務失敗不捏造選項，阻擋建立並可重試', async t => {
  const { page, state, admin } = await fixture(t, { categoryFailure: 503 }); await admin(); await page.locator('.meeting-category-panel > summary').click(); await page.getByText('會議類別無法讀取', { exact: true }).waitFor();
  await page.getByRole('button', { name: '建立會議', exact: true }).click(); assert.equal(await page.getByRole('button', { name: '確認建立會議', exact: true }).isEnabled(), false);
  state.categoryFailure = 0; await page.getByRole('button', { name: '重新讀取類別', exact: true }).click(); await page.getByRole('form', { name: '新增會議類別', exact: true }).waitFor(); assert.equal(await page.getByLabel('建立會議類別', { exact: true }).locator('option[value="affairs"]').count(), 1); assert.equal(state.writes.length, 0); await clean(page, state);
});
test('訪客只能登入，不能讀取會議資料', async t => {
  const { page, state, member } = await fixture(t, { role: 'guest' }); await member(); await page.getByRole('heading', { name: '登入後查看各項會議', exact: true }).waitFor();
  assert.ok(!state.reads.some(path => ['/v1/meetings', '/v1/meeting-categories'].includes(path))); assert.equal(state.writes.length, 0); await clean(page, state);
});
test('無啟用類別時可看歷史但不能建立，啟用後恢復選項', async t => {
  const { page, state, admin } = await fixture(t); for (const category of state.categories) category.is_active = false;
  await admin(); await page.getByRole('button', { name: '建立會議', exact: true }).click(); await page.getByText('沒有啟用的會議類別，請先新增或啟用類別。', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '確認建立會議', exact: true }).isEnabled(), false); assert.equal(await page.locator('.meeting-admin-card').count(), 2);
  await page.locator('.meeting-category-panel > summary').click(); const row = page.getByRole('article', { name: '類別 社務會議', exact: true }); await row.getByRole('button', { name: '編輯', exact: true }).click();
  await row.getByLabel('類別狀態', { exact: true }).selectOption('active'); await row.getByRole('button', { name: '儲存類別', exact: true }).click(); await page.getByText('會議類別已更新', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('建立會議類別', { exact: true }).locator('option[value="affairs"]').count(), 1); assert.equal(state.writes.length, 1); await clean(page, state);
});

for (const status of [401, 403]) {
  test(`管理員類別首次 GET ${status} 隱藏父層已讀取的會議歷史與出席決議`, async t => {
    const { page, state } = await fixture(t, { categoryFailure: status });
    await page.goto(`${base}/admin/social`);
    await page.getByRole('tab', { name: '會議管理', exact: true }).click();
    await page.getByText(status === 401 ? '社務管理只向管理者開放。' : '會議管理權限已失效', { exact: true }).waitFor();
    assert.ok(state.reads.includes('/v1/admin/meetings'));
    assert.equal(await page.locator('.meeting-category-row, .meeting-admin-card, .meeting-filters').count(), 0);
    assert.equal(await page.getByRole('button', { name: '出席與決議', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '建立會議', exact: true }).count(), 0);
    assert.equal(state.reads.filter(path => path === '/v1/admin/meeting-categories').length, 1);
    assert.equal(state.writes.length, 0); await clean(page, state);
  });
  test(`管理員類別快取重新讀取 ${status} 清除父層會議資料及管理入口`, async t => {
    const { page, state, admin } = await fixture(t);
    await page.clock.install(); await admin();
    await page.locator('.meeting-category-panel > summary').click();
    await page.getByRole('article', { name: '類別 社務會議', exact: true }).waitFor();
    await page.getByRole('tab', { name: '審核工作', exact: true }).click();
    const before = state.reads.filter(path => path === '/v1/admin/meeting-categories').length;
    state.categoryFailure = status;
    await page.clock.fastForward(31_000);
    await page.getByRole('tab', { name: '會議管理', exact: true }).click();
    await page.getByText(status === 401 ? '社務管理只向管理者開放。' : '會議管理權限已失效', { exact: true }).waitFor();
    assert.equal(await page.locator('.meeting-category-row, .meeting-admin-card, .meeting-filters').count(), 0);
    assert.equal(await page.getByRole('button', { name: '出席與決議', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '建立會議', exact: true }).count(), 0);
    assert.equal(state.reads.filter(path => path === '/v1/admin/meeting-categories').length - before, 1);
    assert.equal(state.writes.length, 0); await clean(page, state);
  });
}
