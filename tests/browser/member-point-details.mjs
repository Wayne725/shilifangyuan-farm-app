import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197');
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use isolated local fixtures only');
const base = target.origin;
const regular = { id: 'regular', order_number: 'ISOLATED-REGULAR', sales_channel: 'regular' };
const group = { id: 'group', order_number: 'ISOLATED-GROUP', sales_channel: 'group' };
const meal = { id: 'meal', order_number: 'ISOLATED-MEAL', sales_channel: 'meal_preorder' };
const transactions = [
  { id: 'p-regular', amount: 3, source_type: 'purchase', reference_id: regular.id, note: '一般商品消費', source_order: regular },
  { id: 'p-group', amount: 2, source_type: 'purchase', reference_id: group.id, note: '共同購買消費', source_order: group },
  { id: 'p-meal', amount: 1, source_type: 'purchase', reference_id: meal.id, note: '便當消費', source_order: meal },
  { id: 'r-regular', amount: -3, source_type: 'refund', reference_id: 'refund-not-an-order', note: '原訂單退款沖銷', source_order: regular },
  { id: 'adjustment', amount: 5, source_type: 'admin_adjustment', reference_id: 'private-order', note: '線下調整紀錄', source_order: null },
  { id: 'orphan', amount: 2, source_type: 'purchase', reference_id: 'missing-order', note: '舊消費紀錄', source_order: null },
].map(item => ({ created_at: '2030-01-02T17:05:00Z', ...item }));
const badge = { key: 'points_100', name: '合作新芽', description: '累積 100 點', granted_at: '2030-01-02T17:05:00Z' };
function order(source) {
  return { ...source, order_kind: 'regular', revision: 1, amendments: [], created_at: '2030-01-01T00:00:00Z', amount_total: 300, tax_amount: 14,
    payment_method: 'online', payment_status: 'refunded', fulfillment_status: 'cancelled', invoice_status: 'voided', available_actions: [],
    items: [{ id: `item-${source.id}`, product_name: '隔離品項', quantity: 1, unit_label: '份', unit_price: 300, subtotal: 300, tax_type: 'taxable' }],
    fulfillment_method: 'cooperative_pickup', fulfillment: { method: 'cooperative_pickup', status: 'cancelled', pickup_location: '隔離地點' } };
}
function mealOrder() { return { ...order(meal), meal_event_title: '隔離便當', venue_name: '隔離取餐點', pickup_start: '2030-01-02T11:00:00+08:00', pickup_end: '2030-01-02T13:00:00+08:00', pickup_at: '2030-01-02T12:00:00+08:00', items: [{ order_item_id: 'meal-item', offering_id: 'offering', meal_name: '隔離餐點', quantity: 1, unit_price: 300, subtotal: 300, tax_type: 'taxable', selections: [] }] }; }
async function fixture(t, { width = 1280, membership = 'active', role = 'customer', pointsFailure = 0, badgesFailure = 0, empty = false, timezone = 'Asia/Taipei' } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: timezone, reducedMotion: 'reduce', serviceWorkers: 'block' });
  const state = { reads: [], writes: [], unknown: [], errors: [], signedIn: membership !== 'guest', membership, pointsFailure, badgesFailure,
    points: { balance: empty ? 0 : 10, transactions: empty ? [] : structuredClone(transactions) }, badges: empty ? [] : [structuredClone(badge)] };
  const user = { id: 'isolated-member', email: 'member@example.test', email_verified_at: '2030-01-01T00:00:00Z', display_name: '隔離社員', user_role: role, membership_type: 'member', membership_status: 'active' };
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return method === 'GET' ? route.continue() : route.abort();
    if (method !== 'GET' && !['/v1/auth/refresh', '/v1/auth/logout'].includes(path)) state.writes.push(`${method} ${path}`);
    if (path === '/v1/auth/refresh') return route.fulfill({ status: state.signedIn ? 200 : 401, json: state.signedIn ? { access_token: 'isolated', user } : { detail: '隔離訪客' } });
    if (path === '/v1/auth/logout') { state.signedIn = false; return route.fulfill({ status: 204, body: '' }); }
    state.reads.push(path);
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, support_phone: '' } });
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: state.membership === 'active' || state.membership === 'suspended' ? 'member' : state.membership === 'trainee' ? 'trainee' : 'nonmember', membership: { status: state.membership, member_number: 'TEST-MEMBER' } } });
    if (path === '/v1/auth/line/status') return route.fulfill({ json: { ready: true, linked: true, identity_verified: true, message: '' } });
    if (path === '/v1/me/points') return route.fulfill({ status: state.pointsFailure || 200, json: state.pointsFailure ? { detail: '隔離點數讀取失敗' } : state.points });
    if (path === '/v1/me/badges') return route.fulfill({ status: state.badgesFailure || 200, json: state.badgesFailure ? { detail: '隔離徽章讀取失敗' } : state.badges });
    if (['/v1/membership/charges', '/v1/notifications', '/v1/me/surplus-distributions', '/v1/products', '/v1/group-campaigns', '/v1/meal-events', '/v1/activities'].includes(path)) return route.fulfill({ json: [] });
    if (path === '/v1/orders') return route.fulfill({ json: [order(regular), order(group)] });
    if (path === '/v1/meal-orders') return route.fulfill({ json: [mealOrder()] });
    state.unknown.push(`${method} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', error => state.errors.push(error.message));
  return { page, state, visit: () => page.goto(`${base}/account`) };
}
async function openPoints(page) { await page.getByRole('button', { name: /^合作點數 .*查看明細$/ }).click(); await page.getByRole('tab', { name: '合作點數明細', exact: true }).waitFor(); await visibleTab(page, 'points'); }
async function openBadges(page) { await page.getByRole('button', { name: /^已獲徽章 .*查看明細$/ }).click(); await page.getByRole('tab', { name: '已獲徽章明細', exact: true }).waitFor(); await visibleTab(page, 'badges'); }
async function visibleTab(page, tab) { try { await page.waitForFunction(tab => { const box = document.getElementById(`cooperation-${tab}-tab`)?.getBoundingClientRect(); return box && box.top >= -1 && box.bottom <= innerHeight + 1; }, tab); } catch (error) { console.error(await page.evaluate(tab => ({ tab, rect: document.getElementById(`cooperation-${tab}-tab`)?.getBoundingClientRect().toJSON(), scroll: scrollY, height: innerHeight }), tab)); if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/failed-tab.png` }); throw error; } }
async function clean(page, state) { assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []); assert.deepEqual(state.writes, []); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, '不可水平溢出'); }

for (const width of [1280, 390]) {
  test(`${width}px 點數與徽章入口、查詢、分類及總點數保持一致`, async t => {
    const { page, state, visit } = await fixture(t, { width }); await visit(); await openPoints(page);
    await page.getByRole('list', { name: '點數交易紀錄' }).waitFor();
    assert.equal(await page.locator('.member-point-list > li').count(), 6);
    assert.match(await page.locator('.member-point-balance').innerText(), /10 點/);
    assert.equal(await page.locator('.member-point-amount[aria-label="扣除 3 點"]').count(), 1);
    await page.getByLabel('紀錄分類', { exact: true }).selectOption('regular'); assert.equal(await page.locator('.member-point-list > li').count(), 3);
    await page.getByLabel('紀錄分類', { exact: true }).selectOption('meal'); assert.equal(await page.locator('.member-point-list > li').count(), 1);
    await page.getByLabel('紀錄分類', { exact: true }).selectOption('other'); assert.equal(await page.locator('.member-point-list > li').count(), 2);
    assert.equal(await page.locator('.member-point-list a').count(), 0);
    await page.getByRole('button', { name: '清除查詢', exact: true }).click();
    await page.getByLabel('查詢點數紀錄', { exact: true }).fill('isolated-group'); assert.equal(await page.locator('.member-point-list > li').count(), 1);
    assert.match(await page.locator('.member-point-balance').innerText(), /10 點/);
    await page.getByLabel('查詢點數紀錄', { exact: true }).fill('找不到的關鍵字'); await page.getByText('沒有符合條件的點數紀錄', { exact: true }).waitFor();
    await page.getByRole('button', { name: '清除查詢', exact: true }).click();
    if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/member-points-${width}.png` });
    await openBadges(page);
    await page.getByRole('list', { name: '已獲徽章紀錄' }).waitFor();
    assert.match(await page.locator('.member-badge-list').innerText(), /合作新芽[\s\S]*累積 100 點[\s\S]*2030\/01\/03 01:05/);
    assert.equal(await page.locator('.member-badge-list a').count(), 0);
    if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/member-badges-${width}.png` });
    await clean(page, state);
  });
  test(`${width}px 鍵盤啟動、焦點轉移、頁籤及放大排版`, async t => {
    const { page, state, visit } = await fixture(t, { width }); await visit();
    const metric = page.getByRole('button', { name: /^合作點數 .*查看明細$/ }); await metric.focus(); await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.activeElement?.id === 'cooperation-points-tab');
    await page.keyboard.press('ArrowRight'); await page.waitForFunction(() => document.activeElement?.id === 'cooperation-badges-tab');
    assert.equal(await page.getByRole('tab', { name: '已獲徽章明細', exact: true }).getAttribute('aria-selected'), 'true');
    await page.locator('.reading-menu > summary').click(); await page.getByRole('group', { name: '文字大小', exact: true }).getByRole('button', { name: '放大', exact: true }).click();
    await openPoints(page); await clean(page, state);
  });
}

for (const [source, sourceType] of [[regular, 'purchase'], [group, 'purchase'], [meal, 'purchase'], [regular, 'refund']]) {
  test(`${source.sales_channel} ${sourceType} 來源開啟正確訂單並不建立訂單`, async t => {
    const { page, state, visit } = await fixture(t); state.points.transactions = state.points.transactions.filter(row => row.source_order?.id === source.id && row.source_type === sourceType);
    await visit(); await openPoints(page); await page.getByRole('link', { name: `查看訂單 ${source.order_number}`, exact: true }).click();
    await page.waitForURL(`${base}/${source.sales_channel === 'meal_preorder' ? 'meal-orders' : 'orders'}?order_id=${source.id}`);
    await page.locator('.order-detail-head').waitFor(); assert.match(await page.locator('.order-detail-head').innerText(), new RegExp(source.order_number));
    await clean(page, state);
  });
}
for (const membership of ['guest', 'nonmember', 'trainee', 'suspended']) {
  test(`${membership} 不顯示社員紀錄且不發出點數徽章請求`, async t => {
    const { page, state, visit } = await fixture(t, { membership }); await visit();
    await page.getByRole('heading', { name: membership === 'guest' ? '你的合作生活，集中在這裡。' : '正式社員功能尚未開放', exact: true }).waitFor();
    assert.equal(await page.locator('.member-point-details').count(), 0);
    assert.equal(state.reads.some(path => ['/v1/me/points', '/v1/me/badges'].includes(path)), false);
    await clean(page, state);
  });
}
for (const kind of ['points', 'badges']) {
  test(`${kind} 讀取失敗不顯示假零，重試恢復資料`, async t => {
    const { page, state, visit } = await fixture(t, { [`${kind}Failure`]: 503 }); await visit();
    await (kind === 'points' ? openPoints(page) : openBadges(page));
    await page.getByRole('alert').waitFor();
    assert.match(await page.locator('.account-metrics').innerText(), /無法讀取/);
    state[`${kind}Failure`] = 0;
    await page.getByRole('button', { name: kind === 'points' ? '重新讀取點數' : '重新讀取徽章', exact: true }).click();
    await page.getByRole('list', { name: kind === 'points' ? '點數交易紀錄' : '已獲徽章紀錄' }).waitFor();
    await clean(page, state);
  });
  test(`${kind} 回403時即使會員快取為active仍隱藏兩種明細`, async t => {
    const { page, state, visit } = await fixture(t, { [`${kind}Failure`]: 403 }); await visit();
    await page.getByRole('heading', { name: '正式社員功能尚未開放', exact: true }).waitFor();
    assert.equal(await page.locator('.member-point-details').count(), 0);
    assert.equal(await page.getByRole('button', { name: /查看明細$/ }).count(), 0);
    await clean(page, state);
  });
}
test('空紀錄提供明確狀態而且不產生虛構徽章', async t => {
  const { page, state, visit } = await fixture(t, { empty: true }); await visit(); await openPoints(page);
  await page.getByText('目前沒有點數紀錄', { exact: true }).waitFor(); assert.match(await page.locator('.member-point-balance').innerText(), /0 點/);
  await openBadges(page); await page.getByText('目前沒有已獲徽章', { exact: true }).waitFor(); await clean(page, state);
});
test('有時區及舊版naive UTC都以台灣完整日期顯示', async t => {
  const { page, state, visit } = await fixture(t, { timezone: 'America/Los_Angeles' });
  state.points.transactions = [state.points.transactions[0], { ...state.points.transactions[1], created_at: '2030-01-02T17:05:00' }]; state.badges[0].granted_at = '2030-01-02T17:05:00';
  await visit(); await openPoints(page); await page.getByRole('list', { name: '點數交易紀錄' }).waitFor();
  assert.deepEqual(await page.locator('.member-point-date time').allTextContents(), ['2030/01/03 01:05', '2030/01/03 01:05']);
  await openBadges(page); assert.equal(await page.locator('.member-badge-list time').innerText(), '2030/01/03 01:05'); await clean(page, state);
});
test('登出後清除會員點數與徽章明細', async t => {
  const { page, state, visit } = await fixture(t); await visit(); await openPoints(page); await page.getByRole('list', { name: '點數交易紀錄' }).waitFor();
  await page.locator('.account-identity').getByRole('button', { name: '登出', exact: true }).click();
  await page.getByRole('heading', { name: '你的合作生活，集中在這裡。', exact: true }).waitFor();
  assert.equal(await page.locator('.member-point-details').count(), 0); assert.equal(await page.getByText('線下調整紀錄', { exact: true }).count(), 0); await clean(page, state);
});
