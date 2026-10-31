import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { extname, resolve } from 'node:path';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const base = 'http://127.0.0.1:4263';
const root = resolve(process.env.BROWSER_DIST_ROOT || resolve(import.meta.dirname, '../../web/dist'));
const contentTypes = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp' };
const makeOrder = index => ({ id: `history-${index}`, order_number: `HISTORY-${index}`, order_kind: 'regular', sales_channel: 'regular',
  fulfillment_method: 'cooperative_pickup', membership_type_snapshot: 'nonmember', amount_total: 100, tax_amount: 5,
  payment_status: 'paid', invoice_status: 'issued', fulfillment_status: 'picked_up', available_actions: [],
  created_at: new Date(Date.UTC(2030, 0, index)).toISOString(), fulfillment: { method: 'cooperative_pickup', status: 'picked_up', pickup_location: index === 1 ? '遠端門市' : '合作社門市' },
  items: [{ id: `item-${index}`, product_name: `歷史商品${index}`, quantity: 1, unit_price: 100, subtotal: 100, unit_label: '份', tax_type: 'taxable' }] });
const makeMeal = index => ({ ...makeOrder(index), sales_channel: 'meal_preorder', fulfillment_method: 'event_pickup',
  meal_event_id: `event-${index}`, meal_event_title: `午餐場次${index}`, venue_name: index === 1 ? '偏遠餐廳' : '合作社餐廳', pickup_start: '2030-02-01T03:00:00Z',
  pickup_end: '2030-02-01T05:00:00Z', pickup_at: '2030-02-01T04:00:00Z',
  items: [{ offering_id: `offering-${index}`, order_item_id: `item-${index}`, meal_id: `dish-${index}`, meal_name: `歷史餐點${index}`, quantity: 1, base_price: 100, option_price: 0, unit_price: 100, subtotal: 100, tax_type: 'taxable', selections: [] }] });
const normalize = value => value.normalize('NFKC').toLowerCase();

async function fixture(t, { count = 35, kind = 'general', requested = count, clock = false, legacy = false, active = [], pending = false, adminForeign = false, mealReturn = false } = {}) {
  const browser = await engine.launch({ headless: true, ...(engine === chromium ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: Number(process.env.BROWSER_WIDTH || 390), height: 1000 }, serviceWorkers: 'block' });
  const state = { orders: Array.from({ length: count }, (_, index) => (kind === 'meal' ? makeMeal : makeOrder)(count - index)), reads: [], details: [], detailQueries: [], credentials: [], unknown: [], errors: [], moreFailures: 0, duplicateNext: false };
  if (mealReturn) state.orders.find(order => order.id === `history-${requested}`).sales_channel = 'meal_preorder';
  if (pending) state.orders.forEach(order => Object.assign(order, { payment_status: 'pending', invoice_status: 'not_eligible', fulfillment_status: 'pending_confirmation' }));
  for (const index of active) Object.assign(state.orders.find(order => order.id === `history-${index}`), { fulfillment_status: 'ready_for_pickup', pickup_code: kind === 'meal' ? '123456' : undefined });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) {
      const file = extname(path) ? resolve(root, `.${path}`) : resolve(root, 'index.html');
      assert.ok(file.startsWith(`${root}/`));
      return route.fulfill({ contentType: contentTypes[extname(file)] || 'application/octet-stream', body: await readFile(file) });
    }
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'history-fixture', user: { id: 'history-buyer', email: 'buyer@example.test', display_name: '歷史買家', membership_type: 'nonmember', user_role: adminForeign ? 'admin' : 'customer' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === (kind === 'meal' ? '/v1/orders' : '/v1/meal-orders')) return route.fulfill({ json: [] });
    if (path === (kind === 'meal' ? '/v1/meal-orders' : '/v1/orders')) {
      state.reads.push({ path, ...Object.fromEntries(url.searchParams) });
      let items = state.orders;
      if (kind === 'general' && url.searchParams.get('history_scope') === 'general') items = items.filter(order => order.sales_channel !== 'meal_preorder');
      const q = url.searchParams.get('q');
      if (q) items = items.filter(order => {
        const haystack = normalize([order.order_number, order.meal_event_title, order.venue_name, order.fulfillment?.pickup_location, ...order.items.map(item => item.product_name || item.meal_name)].join(' '));
        return normalize(q).split(/\s+/u).filter(Boolean).every(term => haystack.includes(term));
      });
      if (url.searchParams.get('payment_status')) items = items.filter(order => order.payment_status === url.searchParams.get('payment_status'));
      if (url.searchParams.has('before_id') && state.moreFailures > 0) { state.moreFailures--; return route.fulfill({ status: 503, json: { detail: '隔離更多訂單讀取失敗' } }); }
      if (url.searchParams.has('before_id')) items = items.slice(items.findIndex(item => item.id === url.searchParams.get('before_id')) + 1);
      const limit = Number(url.searchParams.get('limit'));
      const page = !legacy && limit ? items.slice(0, limit) : items;
      if (state.duplicateNext && url.searchParams.has('before_id')) page.unshift(state.orders[0]);
      return route.fulfill({ json: page });
    }
    if (/^\/v1\/meal-orders\/history-\d+\/pickup-credential$/.test(path)) {
      state.credentials.push(path); return route.fulfill({ json: { order_id: path.split('/')[3], pickup_code: '123456', qr_token: 'isolated-history-credential' } });
    }
    if (/^\/v1\/(meal-orders|orders)\/history-\d+$/.test(path)) {
      state.details.push(path);
      state.detailQueries.push({ path, ...Object.fromEntries(url.searchParams) });
      let order = state.orders.find(item => item.id === path.split('/').at(-1));
      if (mealReturn && path.startsWith('/v1/meal-orders/')) order = makeMeal(requested);
      if (adminForeign && path === '/v1/orders/history-0' && url.searchParams.get('own_only') !== 'true') order = { ...makeOrder(0), items: [{ ...makeOrder(0).items[0], product_name: '另一帳號私密商品' }] };
      return route.fulfill({ status: order ? 200 : 404, json: order || { detail: '找不到訂單' } });
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated history route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  page.on('pageerror', error => state.errors.push(error.message));
  if (clock) await page.clock.install();
  await page.goto(`${base}/${kind === 'meal' ? 'meal-orders' : 'orders'}?order_id=history-${requested}`);
  if (adminForeign) await page.locator('.order-index button').filter({ hasText: `HISTORY-${count}` }).waitFor();
  else if (kind === 'meal' || mealReturn) await page.locator('.order-detail-head .order-number').filter({ hasText: `HISTORY-${requested}` }).waitFor();
  else await page.getByRole('heading', { name: `HISTORY-${requested}`, exact: true }).waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  t.after(() => {
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
    assert.ok(state.detailQueries.filter(read => read.path.startsWith('/v1/orders/')).every(read => read.own_only === 'true'));
  });
  return { page, state };
}

test('一般訂單首頁只讀25筆且限本人一般歷史，已在首頁的返回單不額外GET', async t => {
  const { page, state } = await fixture(t);
  assert.equal(state.reads[0].limit, '25');
  assert.equal(state.reads[0].own_only, 'true');
  assert.equal(state.reads[0].history_scope, 'general');
  assert.equal(await page.locator('.order-index button').count(), 25);
  assert.deepEqual(state.details, []);
});

test('全部終結的歷史不再每5秒整批輪詢', async t => {
  const { page, state } = await fixture(t, { count: 1, clock: true });
  await page.bringToFront();
  const reads = state.reads.length;
  await page.clock.runFor(5100);
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.reads.length, reads);
});

test('完整雙cursor載入下一頁並依id去重，不將已載筆數宣稱total', async t => {
  const { page, state } = await fixture(t);
  state.duplicateNext = true;
  await page.getByRole('button', { name: '載入更多訂單', exact: true }).click();
  await page.getByText('已載入 35 筆訂單', { exact: true }).waitFor();
  assert.equal(await page.locator('.order-index button').count(), 35);
  assert.equal(state.reads[1].before_id, 'history-11');
  assert.equal(state.reads[1].before_created_at, makeOrder(11).created_at);
  assert.equal(state.reads[1].limit, '25');
  assert.equal(await page.getByRole('button', { name: '載入更多訂單', exact: true }).count(), 0);
});

test('載入更多503保留已讀25筆，重試仍用原cursor成功', async t => {
  const { page, state } = await fixture(t);
  state.moreFailures = 3;
  await page.getByRole('button', { name: '載入更多訂單', exact: true }).click();
  await page.getByRole('alert').getByText('更多訂單暫時無法讀取：隔離更多訂單讀取失敗', { exact: true }).waitFor();
  assert.equal(await page.locator('.order-index button').count(), 25);
  await page.getByRole('button', { name: '重試載入更多', exact: true }).click();
  await page.getByText('已載入 35 筆訂單', { exact: true }).waitFor();
  assert.ok(state.reads.slice(1).every(read => read.before_id === 'history-11'));
});

test('搜尋送到server涵蓋未載頁，NFKC多詞與付款狀態重設cursor', async t => {
  const { page, state } = await fixture(t);
  await page.getByRole('searchbox', { name: '查詢訂單', exact: true }).fill(' ＨＩＳＴＯＲＹ－１  商品１　遠端門市 ');
  await page.getByText('已載入 1 筆訂單', { exact: true }).waitFor();
  assert.equal(state.reads.at(-1).q, 'history-1 商品1 遠端門市');
  assert.equal(state.reads.at(-1).before_id, undefined);
  assert.equal(await page.locator('.order-index button').count(), 1);
  await page.getByRole('combobox', { name: '付款狀態', exact: true }).selectOption('refunded');
  await page.getByText('沒有符合條件的一般訂單', { exact: true }).waitFor();
  assert.equal(state.reads.at(-1).payment_status, 'refunded');
  assert.equal(state.reads.at(-1).before_id, undefined);
});

test('返回較舊order_id不在首頁才singleGET，保留原單明細', async t => {
  const { page, state } = await fixture(t, { requested: 1 });
  assert.deepEqual(state.details, ['/v1/orders/history-1']);
  await page.getByText('歷史商品1', { exact: true }).waitFor();
  assert.equal(state.reads.length, 1);
});

test('legacy忽略limit返回35筆仍能讀取，不再重複cursor載入更多', async t => {
  const { page } = await fixture(t, { legacy: true });
  assert.equal(await page.locator('.order-index button').count(), 35);
  assert.equal(await page.getByRole('button', { name: '載入更多訂單', exact: true }).count(), 0);
});

test('單頁active保留5秒list更新；完成後停止，仍可手動更新', async t => {
  const { page, state } = await fixture(t, { count: 1, active: [1], clock: true });
  await page.bringToFront();
  await page.clock.runFor(5100); await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.reads.length, 2);
  state.orders[0].fulfillment_status = 'picked_up';
  await page.clock.runFor(5100); await new Promise(resolve => setTimeout(resolve, 100));
  const settledReads = state.reads.length;
  await page.clock.runFor(5100); await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.reads.length, settledReads);
  await page.getByRole('button', { name: '更新訂單', exact: true }).click();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.reads.length, settledReads + 1);
  await page.clock.runFor(31000); await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.reads.length, settledReads + 1);
  await page.evaluate(() => window.dispatchEvent(new Event('visibilitychange')));
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.reads.length, settledReads + 2);
});

test('選中單付款狀態改變時局部更新頁面並離開原付款篩選，不整批重新抓頁', async t => {
  const { page, state } = await fixture(t, { pending: true, clock: true });
  await page.bringToFront();
  await page.getByRole('combobox', { name: '付款狀態', exact: true }).selectOption('pending');
  await page.getByRole('button', { name: '載入更多訂單', exact: true }).click();
  await page.getByText('已載入 35 筆訂單', { exact: true }).waitFor();
  await new Promise(resolve => setTimeout(resolve, 100));
  const listReads = state.reads.length;
  Object.assign(state.orders[0], { payment_status: 'paid', fulfillment_status: 'picked_up', invoice_status: 'issued' });
  await page.clock.runFor(5100); await new Promise(resolve => setTimeout(resolve, 100));
  await page.getByText('已載入 34 筆訂單', { exact: true }).waitFor();
  assert.equal(await page.locator('.order-index button').filter({ hasText: 'HISTORY-35' }).count(), 0);
  assert.equal(state.reads.length, listReads);
});

test('多頁只更新目前選中active單，換選立即取新狀態且不輪詢所有pages', async t => {
  const { page, state } = await fixture(t, { active: [35, 34], clock: true });
  await page.bringToFront();
  await page.getByRole('button', { name: '載入更多訂單', exact: true }).click();
  await page.getByText('已載入 35 筆訂單', { exact: true }).waitFor();
  await page.clock.runFor(5100); await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.reads.length, 2);
  assert.ok(state.details.length >= 2);
  assert.ok(state.details.every(path => path === '/v1/orders/history-35'));
  await page.locator('.order-index button').filter({ hasText: 'HISTORY-34' }).click();
  await page.getByRole('heading', { name: 'HISTORY-34', exact: true }).waitFor();
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.details.at(-1), '/v1/orders/history-34');
});

test('餐點也以25筆分頁，場次未載頁搜尋與舊ID回復仍可用', async t => {
  const { page, state } = await fixture(t, { kind: 'meal', requested: 1 });
  assert.equal(state.reads[0].limit, '25');
  assert.deepEqual(state.details, ['/v1/meal-orders/history-1']);
  await page.getByRole('searchbox', { name: '查詢訂單', exact: true }).fill('午餐場次1 偏遠餐廳');
  await page.getByText('已載入 1 筆訂單', { exact: true }).waitFor();
  assert.equal(state.reads.at(-1).q, '午餐場次1 偏遠餐廳');
});

test('已付待取餐保留credential輪詢，取餐完成後不再顯示有效QR', async t => {
  const { page, state } = await fixture(t, { kind: 'meal', count: 1, active: [1], clock: true });
  await page.getByLabel('訂單 HISTORY-1 取餐 QR Code', { exact: true }).waitFor();
  await page.bringToFront();
  await page.clock.runFor(5100); await new Promise(resolve => setTimeout(resolve, 100));
  assert.ok(state.credentials.length >= 2);
  state.orders[0].fulfillment_status = 'picked_up';
  await page.clock.runFor(5100); await new Promise(resolve => setTimeout(resolve, 100));
  await page.getByText('已完成取餐', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('訂單 HISTORY-1 取餐 QR Code', { exact: true }).count(), 0);
});

test('管理員我的歷史singleGET也帶own_only，舊URL他人單不得混成本人單', async t => {
  const { page, state } = await fixture(t, { requested: 0, adminForeign: true });
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.detailQueries.find(read => read.path === '/v1/orders/history-0')?.own_only, 'true');
  assert.equal(await page.getByText('另一帳號私密商品', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('heading', { name: 'HISTORY-0', exact: true }).count(), 0);
});

test('切到餐點分頁後不再輪詢背景的一般訂單', async t => {
  const { page, state } = await fixture(t, { count: 1, active: [1], clock: true });
  await page.getByRole('tab', { name: '餐點訂單', exact: true }).click();
  await page.getByText('目前沒有便當訂單', { exact: true }).waitFor();
  const reads = state.reads.length;
  await page.bringToFront();
  await page.clock.runFor(5100); await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.reads.length, reads);
});

test('搜尋排除原返回單後不繼續輪詢隱藏原單', async t => {
  const { page, state } = await fixture(t, { active: [35, 34], clock: true });
  await page.bringToFront();
  await page.getByRole('searchbox', { name: '查詢訂單', exact: true }).fill('HISTORY-34');
  await page.clock.runFor(300); await new Promise(resolve => setTimeout(resolve, 100));
  await page.getByRole('heading', { name: 'HISTORY-34', exact: true }).waitFor();
  const oldDetailReads = state.details.filter(path => path === '/v1/orders/history-35').length;
  await page.clock.runFor(5100); await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(state.details.filter(path => path === '/v1/orders/history-35').length, oldDetailReads);
});

test('付款返回較舊便當單不在general頁，經原單detail仍導向正確餐點頁', async t => {
  const { page, state } = await fixture(t, { requested: 1, mealReturn: true });
  await page.waitForURL(url => url.pathname === '/meal-orders' && url.searchParams.get('order_id') === 'history-1');
  await page.getByText('歷史餐點1', { exact: true }).waitFor();
  assert.ok(state.details.includes('/v1/orders/history-1'));
  assert.ok(state.details.includes('/v1/meal-orders/history-1'));
});
