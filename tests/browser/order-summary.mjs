import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4185');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || !['http:', 'https:'].includes(target.protocol)
  || target.pathname !== '/' || target.username || target.password || target.search || target.hash) {
  throw new Error('Order-summary acceptance must use an isolated local origin');
}
const base = target.origin;
const summary_path = '/v1/admin/order-summary';
const category_name = '米・雜糧';
const rows = [
  ...Array.from({ length: 27 }, (_, index) => ({
    key: `rice-${index}`, order_id: `rice-order-${Math.floor(index / 3)}`, created_on: '2030-01-02',
    sales_channel: 'regular', state: 'paid', category: category_name,
    name: `隔離白米選品 ${String(index + 1).padStart(2, '0')}`, unit: '袋', quantity: 2,
  })),
  { key: 'pending-rice', order_id: 'cash-pending', created_on: '2030-01-02', sales_channel: 'regular', state: 'pending', category: category_name, name: '隔離白米現場付款', unit: '袋', quantity: 7 },
  { key: 'meal', order_id: 'meal-order', created_on: '2030-01-02', sales_channel: 'meal_preorder', state: 'paid', category: '便當', name: '隔離香菇便當', unit: '份', quantity: 4 },
  { key: 'bundle', order_id: 'bundle-order', created_on: '2030-01-02', sales_channel: 'group', state: 'inactive', category: '共同購買組合', name: '隔離白米組合', unit: '組', quantity: 3 },
  { key: 'old-rice', order_id: 'old-order', created_on: '2029-12-31', sales_channel: 'regular', state: 'paid', category: category_name, name: '隔離白米前期品項', unit: '袋', quantity: 99 },
];

function summarize(query) {
  const filters = {
    starts_on: query.starts_on, ends_on: query.ends_on, sales_channel: query.sales_channel || 'all',
    order_category: query.order_category || 'all', status: query.status || 'active', search: query.search || '', category: query.category || '',
  };
  const matching = rows.filter((row) => row.created_on >= filters.starts_on && row.created_on <= filters.ends_on
    && (filters.sales_channel === 'all' || row.sales_channel === filters.sales_channel)
    && (filters.order_category === 'all' || (filters.order_category === 'meal' ? row.sales_channel === 'meal_preorder' : row.sales_channel !== 'meal_preorder'))
    && (filters.status === 'all' || (filters.status === 'active' ? row.state !== 'inactive' : row.state === filters.status))
    && row.name.includes(filters.search));
  const selected = matching.filter((row) => !filters.category || row.category === filters.category);
  const count_orders = (items) => new Set(items.map((row) => row.order_id)).size;
  const quantity = (items) => items.reduce((total, row) => total + row.quantity, 0);
  const grouped = (field) => [...new Set(selected.map((row) => row[field]))].map((value) => {
    const items = selected.filter((row) => row[field] === value);
    return { [field]: value, order_count: count_orders(items), quantity: quantity(items) };
  });
  const products = selected.toSorted((a, b) => b.quantity - a.quantity || a.name.localeCompare(b.name))
    .map(({ key, sales_channel, category, name, unit, quantity }) => ({ key, sales_channel, category, name, unit, quantity, order_count: 1 }));
  const limit = Number(query.limit || 25), offset = Number(query.offset || 0);
  return {
    ...filters,
    summary: { order_count: count_orders(selected), quantity: quantity(selected), product_count: products.length,
      ...Object.fromEntries(['paid', 'pending', 'inactive'].map((state) => [`${state}_order_count`, count_orders(selected.filter((row) => row.state === state))])) },
    categories: grouped('category'), channels: grouped('sales_channel'),
    category_options: [...new Set(matching.map((row) => row.category))].sort(),
    products: { items: products.slice(offset, offset + limit), total: products.length, limit, offset },
  };
}

async function fixture(t, { width = 882, role = 'admin', failing = false, orderRows = [] } = {}) {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, timezoneId: 'Asia/Taipei', locale: 'zh-TW', serviceWorkers: 'block', reducedMotion: 'reduce' });
  const state = { reads: [], summaries: [], orderQueries: [], issueQueries: [], writes: [], unknown: [], errors: [], failing };
  await context.route('**/*', (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (url.origin !== base) return route.abort();
    if (path === '/v1/auth/refresh' && method === 'POST') {
      return route.fulfill({ status: role === 'guest' ? 401 : 200, json: role === 'guest' ? { detail: '隔離訪客' } : {
        access_token: 'isolated-summary-token', user: { id: `isolated-${role}`, display_name: '隔離驗收帳號', email: 'summary@example.test', user_role: role, membership_type: 'nonmember' },
      } });
    }
    if (method !== 'GET') { state.writes.push(`${method} ${path}`); return route.abort(); }
    if (!path.startsWith('/v1/')) return route.continue();
    state.reads.push(path);
    if (path === summary_path) {
      const query = Object.fromEntries(url.searchParams), report = summarize(query);
      state.summaries.push({ query, report, failed: state.failing });
      return route.fulfill(state.failing ? { status: 503, json: { detail: '隔離匯總服務暫時不可用' } } : { json: report });
    }
    if (path === '/v1/admin/order-search') {
      const query = Object.fromEntries(url.searchParams); state.orderQueries.push(query);
      const matched = orderRows.filter((row) => (query.order_category === 'all' || (query.order_category === 'meal' ? row.sales_channel === 'meal_preorder' : row.sales_channel !== 'meal_preorder'))
        && (!query.q || `${row.order_number} ${row.contact_email} ${row.items.map((item) => item.product_name).join(' ')}`.includes(query.q))
        && (!query.payment_status || row.payment_status === query.payment_status));
      const offset = Number(query.offset), limit = Number(query.limit);
      return route.fulfill({ json: { items: matched.slice(offset, offset + limit), total: matched.length, limit, offset } });
    }
    if (path === '/v1/admin/failed-jobs') { state.issueQueries.push(Object.fromEntries(url.searchParams)); return route.fulfill({ json: { items: [], total: 0, limit: 10, offset: 0 } }); }
    if (path === '/v1/members/me') return route.fulfill({ json: { membership_type: 'nonmember', membership: null } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true } });
    if (path === '/v1/notifications') return route.fulfill({ json: [] });
    state.unknown.push(`${method} ${path}`);
    return route.fulfill({ status: 404, json: { detail: '未定義的隔離 API' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  return { page, state };
}

async function verify_read_only(page, state) {
  assert.deepEqual(state.writes, []);
  assert.deepEqual(state.unknown, []);
  assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, '頁面不能整頁橫向溢出');
}

async function screenshot(page, name) {
  if (!process.env.BROWSER_SCREENSHOT_DIR) return;
  await mkdir(process.env.BROWSER_SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({ path: join(process.env.BROWSER_SCREENSHOT_DIR, name), fullPage: true });
}

async function wait_for_summary(page) {
  await page.getByRole('heading', { name: '訂單彙整', level: 1, exact: true }).waitFor();
  await page.locator('.admin-order-summary > [aria-busy="false"]').waitFor();
}

async function summary_values(page) {
  return page.locator('dl[aria-label="查詢結果統計"]').evaluate((list) => Object.fromEntries(
    [...list.children].map((row) => [row.querySelector('dt').textContent, Number(row.querySelector('dd').textContent.replace(/[^\d]/g, ''))]),
  ));
}

async function verify_summary(page, expected) {
  assert.deepEqual(await summary_values(page), {
    訂單數: expected.order_count, 品項數量: expected.quantity, 品項種類: expected.product_count,
  });
}

for (const width of [882, 390]) {
  test(`${width}px 訂單營運可進入匯總，篩選與分頁共用完整統計，大字表格獨立橫捲`, { timeout: 40000 }, async (t) => {
    const { page, state } = await fixture(t, { width });
    await page.goto(`${base}/admin`);
    await page.getByRole('heading', { name: '訂單、付款與發票', exact: true }).waitFor();
    await page.getByText('沒有符合條件的訂單。', { exact: true }).waitFor();
    assert.equal(state.reads.includes('/v1/admin/order-search'), true);
    await page.getByRole('navigation', { name: '訂單營運子頁面', exact: true }).getByRole('link', { name: '訂單彙整', exact: true }).click();
    await wait_for_summary(page);
    assert.equal(new URL(page.url()).searchParams.get('section'), 'summary');
    assert.equal(await page.getByRole('navigation', { name: '訂單營運子頁面', exact: true }).getByRole('link', { name: '訂單彙整', exact: true }).getAttribute('aria-current'), 'page');
    assert.equal(state.summaries[0].query.sales_channel, 'all');
    assert.equal(state.summaries[0].query.status, 'active');
    assert.equal(state.summaries[0].query.order_category, 'meal');
    await verify_summary(page, state.summaries.at(-1).report.summary);

    const summary = page.getByRole('region', { name: '訂單品項彙整', exact: true });
    await summary.getByRole('button', { name: '一般訂單', exact: true }).click();
    await wait_for_summary(page);
    const before_draft = state.summaries.length;
    await summary.getByLabel('下單日期起（台灣）', { exact: true }).fill('2030-01-01');
    await summary.getByLabel('下單日期迄（台灣）', { exact: true }).fill('2030-01-03');
    await summary.getByRole('combobox', { name: '銷售類型', exact: true }).selectOption('regular');
    await summary.getByRole('combobox', { name: '訂單狀態', exact: true }).selectOption('paid');
    assert.equal(await summary.getByRole('combobox', { name: '品類', exact: true }).count(), 0);
    await summary.getByLabel('品項名稱', { exact: true }).fill('  隔離白米  ');
    assert.equal(state.summaries.length, before_draft, '尚未送出篩選時不能查詢新條件');
    await summary.getByRole('button', { name: '查詢', exact: true }).click();
    await page.getByRole('status').getByText('共 27 種品項', { exact: true }).waitFor();
    await wait_for_summary(page);
    const expected_query = { starts_on: '2030-01-01', ends_on: '2030-01-03', sales_channel: 'regular', order_category: 'general', status: 'paid', category: '', search: '隔離白米', limit: '25', offset: '0' };
    assert.deepEqual(state.summaries.at(-1).query, expected_query);
    const expected_summary = { order_count: 9, quantity: 54, product_count: 27 };
    await verify_summary(page, expected_summary);
    const products = page.getByRole('region', { name: '商品明細表格', exact: true });
    assert.equal(await products.locator('tbody tr').count(), 25);
    assert.equal(await products.getByRole('rowheader', { name: '隔離白米現場付款', exact: true }).count(), 0);
    assert.equal(await page.getByRole('region', { name: '品類匯總表格', exact: true }).count(), 0);
    assert.equal(await page.getByRole('region', { name: '銷售類型匯總表格', exact: true }).count(), 0);
    assert.deepEqual(await products.locator('thead th').allTextContents(), ['品項', '訂單數', '數量／單位']);

    const pagination = page.getByRole('navigation', { name: '商品明細分頁', exact: true });
    assert.equal(await pagination.getByRole('button', { name: '上一頁', exact: true }).isDisabled(), true);
    await pagination.getByRole('button', { name: '下一頁', exact: true }).click();
    await pagination.getByText('第 2／2 頁 · 共 27 種', { exact: true }).waitFor();
    await wait_for_summary(page);
    assert.deepEqual(state.summaries.at(-1).query, { ...expected_query, offset: '25' });
    assert.equal(await products.locator('tbody tr').count(), 2);
    await verify_summary(page, expected_summary);
    assert.equal(await pagination.getByRole('button', { name: '下一頁', exact: true }).isDisabled(), true);
    assert.equal(await summary.getByRole('combobox', { name: '品類', exact: true }).count(), 0);
    await verify_read_only(page, state);
    await screenshot(page, `order-summary-${width}-standard.png`);

    await page.locator('.reading-menu > summary').click();
    await page.getByRole('group', { name: '文字大小', exact: true }).getByRole('button', { name: '放大', exact: true }).click();
    await page.waitForFunction(() => getComputedStyle(document.documentElement).fontSize === '20px');
    await page.evaluate(async () => { await document.fonts.ready; await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    await verify_read_only(page, state);
    const dimensions = await products.evaluate((region) => ({ width: region.clientWidth, content: region.scrollWidth, tabIndex: region.tabIndex, overflow: getComputedStyle(region).overflowX }));
    assert.equal(dimensions.tabIndex, 0);
    assert.equal(dimensions.overflow, 'auto');
    assert.equal(await products.locator('table').evaluate((table) => table.classList.contains('compact')), true);
    if (dimensions.content > dimensions.width) {
      await products.focus();
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('[aria-label="商品明細表格"]').scrollLeft > 0);
    }
    await verify_summary(page, expected_summary);
    await verify_read_only(page, state);
    await screenshot(page, `order-summary-${width}-large.png`);
  });
}

test('直接開啟匯總不讀取訂單清單，通路與狀態查詢及日期驗證正確', { timeout: 25000 }, async (t) => {
  const { page, state } = await fixture(t);
  await page.goto(`${base}/admin?section=summary`);
  await wait_for_summary(page);
  assert.equal(state.reads.includes('/v1/admin/order-search'), false);
  const summary = page.getByRole('region', { name: '訂單品項彙整', exact: true });
  await page.getByRole('group', { name: '訂單分類', exact: true }).getByRole('button', { name: '一般訂單', exact: true }).click();
  await wait_for_summary(page);
  const requests = state.summaries.length;
  await summary.getByLabel('下單日期起（台灣）', { exact: true }).fill('2030-02-01');
  await summary.getByRole('button', { name: '查詢', exact: true }).click();
  await page.getByRole('alert').getByText('請確認下單日期，結束日期不能早於開始日期。', { exact: true }).waitFor();
  assert.equal(state.summaries.length, requests);
  await summary.getByLabel('下單日期起（台灣）', { exact: true }).fill('2030-01-01');
  await summary.getByRole('combobox', { name: '銷售類型', exact: true }).selectOption('group');
  await summary.getByRole('combobox', { name: '訂單狀態', exact: true }).selectOption('inactive');
  await summary.getByRole('button', { name: '查詢', exact: true }).click();
  await page.getByRole('region', { name: '商品明細表格', exact: true }).getByRole('rowheader', { name: '隔離白米組合', exact: true }).waitFor();
  await wait_for_summary(page);
  assert.equal(state.summaries.at(-1).query.sales_channel, 'group');
  assert.equal(state.summaries.at(-1).query.status, 'inactive');
  await verify_summary(page, { order_count: 1, quantity: 3, product_count: 1 });
  assert.equal(state.reads.includes('/v1/admin/order-search'), false);
  await verify_read_only(page, state);
});

test('匯總服務失敗不顯示假零筆，重新連線恢復，空查詢顯示真正零筆', { timeout: 30000 }, async (t) => {
  const { page, state } = await fixture(t, { failing: true, width: 390 });
  await page.goto(`${base}/admin?section=summary`);
  await page.getByRole('heading', { name: '訂單彙整無法讀取', exact: true }).waitFor();
  assert.equal(await page.locator('dl[aria-label="查詢結果統計"]').count(), 0);
  assert.equal(await page.getByRole('heading', { name: '沒有符合條件的訂單', exact: true }).count(), 0);
  assert.equal(state.summaries.every((request) => request.failed), true);
  state.failing = false;
  await page.getByRole('button', { name: '重新連線', exact: true }).click();
  await wait_for_summary(page);
  await verify_summary(page, state.summaries.at(-1).report.summary);
  const summary = page.getByRole('region', { name: '訂單品項彙整', exact: true });
  await summary.getByLabel('品項名稱', { exact: true }).fill('找不到的隔離品項');
  await summary.getByRole('button', { name: '查詢', exact: true }).click();
  await page.getByRole('heading', { name: '沒有符合條件的訂單', exact: true }).waitFor();
  await verify_summary(page, { order_count: 0, quantity: 0, product_count: 0 });
  assert.equal(await page.getByRole('region', { name: '商品明細表格', exact: true }).count(), 0);
  assert.equal(await page.getByRole('navigation', { name: '商品明細分頁', exact: true }).count(), 0);
  assert.equal(state.reads.includes('/v1/admin/order-search'), false);
  await verify_read_only(page, state);
});

for (const role of ['guest', 'customer']) {
  test(`${role === 'guest' ? '訪客' : '非管理員'}直接開啟匯總不查管理 API`, { timeout: 15000 }, async (t) => {
    const { page, state } = await fixture(t, { role, width: 390 });
    await page.goto(`${base}/admin?section=summary`);
    await page.waitForFunction((signed_in) => {
      const account = document.querySelector('.site-header .account-button');
      return signed_in ? account?.tagName === 'A' : account?.tagName === 'BUTTON' && !account.disabled;
    }, role !== 'guest');
    await page.getByRole('heading', { name: '管理工作台', level: 1, exact: true }).waitFor();
    assert.equal(await page.getByRole('region', { name: '訂單品項彙整', exact: true }).count(), 0);
    assert.equal(state.reads.some((path) => path.startsWith('/v1/admin/')), false);
    assert.deepEqual(state.summaries, []);
    await verify_read_only(page, state);
  });
}


test('一般與便當分類重設分頁和互斥條件，查詢保持分類且統計不混合', { timeout: 30000 }, async (t) => {
  const { page, state } = await fixture(t, { width: 390 });
  await page.goto(`${base}/admin?section=summary`);
  await wait_for_summary(page);
  const summary = page.getByRole('region', { name: '訂單品項彙整', exact: true });
  const categories = summary.getByRole('group', { name: '訂單分類', exact: true });
  assert.equal(await categories.getByRole('button').count(), 2);
  assert.equal(await categories.getByRole('button', { name: '便當訂單', exact: true }).getAttribute('aria-pressed'), 'true');
  assert.equal(await summary.getByRole('combobox', { name: '訂單分類', exact: true }).count(), 0);
  await categories.getByRole('button', { name: '一般訂單', exact: true }).click();
  await wait_for_summary(page);
  await verify_summary(page, { order_count: 10, quantity: 61, product_count: 28 });
  assert.equal(await summary.getByRole('combobox', { name: '銷售類型', exact: true }).locator('option[value="meal_preorder"]').count(), 0);
  const pagination = page.getByRole('navigation', { name: '商品明細分頁', exact: true });
  await pagination.getByRole('button', { name: '下一頁', exact: true }).click();
  await wait_for_summary(page);
  assert.equal(state.summaries.at(-1).query.offset, '25');
  await categories.getByRole('button', { name: '便當訂單', exact: true }).press('Enter');
  await wait_for_summary(page);
  assert.equal(state.summaries.at(-1).query.offset, '0');
  assert.equal(state.summaries.at(-1).query.order_category, 'meal');
  assert.equal(await summary.getByRole('combobox', { name: '銷售類型', exact: true }).count(), 0);
  assert.equal(await summary.getByRole('combobox', { name: '品類', exact: true }).count(), 0);
  await verify_summary(page, { order_count: 1, quantity: 4, product_count: 1 });
  await summary.getByLabel('品項名稱', { exact: true }).fill('白米');
  await summary.getByRole('button', { name: '查詢', exact: true }).click();
  await wait_for_summary(page);
  await verify_summary(page, { order_count: 0, quantity: 0, product_count: 0 });
  assert.equal(state.summaries.at(-1).query.order_category, 'meal');
  await verify_read_only(page, state);
});


test('管理員訂單及問題回報保留查詢與分類，手機無溢出', { timeout: 30000 }, async (t) => {
  const { page, state } = await fixture(t, { width: 390 });
  await page.goto(`${base}/admin`);
  await page.getByText('沒有符合條件的訂單。', { exact: true }).waitFor();
  await page.getByRole('combobox', { name: '訂單分類', exact: true }).selectOption('meal');
  await page.getByLabel('搜尋訂單', { exact: true }).fill('隔離買家');
  await page.getByRole('combobox', { name: '付款狀態', exact: true }).selectOption('paid');
  await page.getByRole('button', { name: '搜尋', exact: true }).click();
  await page.getByText('沒有符合條件的訂單。', { exact: true }).waitFor();
  assert.equal(state.orderQueries.at(-1).order_category, 'meal');
  assert.equal(state.orderQueries.at(-1).payment_status, 'paid');
  assert.equal(state.orderQueries.at(-1).q, '隔離買家');
  await verify_read_only(page, state);
  await page.goto(`${base}/admin?section=issues`);
  await page.getByRole('heading', { name: '待處理問題', exact: true }).waitFor();
  await page.getByRole('combobox', { name: '訂單分類', exact: true }).selectOption('system');
  await page.getByLabel('搜尋問題', { exact: true }).fill('逾時');
  await page.getByRole('button', { name: '查詢問題', exact: true }).click();
  await page.getByText('沒有符合條件的問題。', { exact: true }).waitFor();
  assert.equal(state.issueQueries.at(-1).order_category, 'system');
  assert.equal(state.issueQueries.at(-1).q, '逾時');
  await verify_read_only(page, state);
});


for (const width of [1440, 390]) {
  test(`${width}px 管理員訂單分類與商品關鍵字跨頁查詢，類別和付款狀態不混用`, { timeout: 30000 }, async (t) => {
    const orderRows = Array.from({ length: 15 }, (_, index) => ({
      id: `isolated-order-${index}`, order_number: `CATEGORY-${String(index).padStart(2, '0')}`,
      sales_channel: index >= 13 ? 'meal_preorder' : index === 12 ? 'group' : 'regular',
      contact_email: 'buyer@example.com', payment_status: index === 14 ? 'paid' : 'pending',
      payment_method: 'online', fulfillment_method: 'cooperative_pickup', fulfillment_status: 'pending_confirmation',
      invoice_status: 'not_eligible', amount_total: 100, tax_amount: 0, member_tax_exempt_snapshot: false,
      available_actions: [], created_at: '2030-01-02T02:00:00Z',
      items: [{ product_name: index === 12 ? '共同購買白米' : index >= 13 ? '蔬食便當' : '日常白米', quantity: 1, unit_label: '份', subtotal: 100, unit_price: 100, tax_type: 'tax_exempt' }],
    }));
    const { page, state } = await fixture(t, { width, orderRows });
    await page.goto(`${base}/admin`);
    const pagination = page.getByRole('navigation', { name: '訂單分頁', exact: true });
    await pagination.getByText('共 15 筆 · 第 1 頁', { exact: true }).waitFor();
    await pagination.getByRole('button', { name: '下一頁', exact: true }).click();
    await pagination.getByText('共 15 筆 · 第 2 頁', { exact: true }).waitFor();
    const categories = page.getByRole('combobox', { name: '訂單分類', exact: true });
    await categories.selectOption('general');
    await pagination.getByText('共 13 筆 · 第 1 頁', { exact: true }).waitFor();
    assert.equal(state.orderQueries.at(-1).offset, '0');
    await page.getByLabel('搜尋訂單', { exact: true }).fill('共同購買白米');
    await page.getByRole('button', { name: '搜尋', exact: true }).click();
    await pagination.getByText('共 1 筆 · 第 1 頁', { exact: true }).waitFor();
    await page.locator('.operation-title strong').getByText('CATEGORY-12', { exact: true }).waitFor();
    assert.equal(state.orderQueries.at(-1).order_category, 'general');
    await categories.selectOption('meal');
    await page.getByText('沒有符合條件的訂單。', { exact: true }).waitFor();
    assert.equal(state.orderQueries.at(-1).q, '共同購買白米');
    await page.getByRole('button', { name: '清除查詢', exact: true }).click();
    await pagination.getByText('共 2 筆 · 第 1 頁', { exact: true }).waitFor();
    await page.getByRole('combobox', { name: '付款狀態', exact: true }).selectOption('paid');
    await pagination.getByText('共 1 筆 · 第 1 頁', { exact: true }).waitFor();
    await page.locator('.operation-title strong').getByText('CATEGORY-14', { exact: true }).waitFor();
    assert.equal(state.orderQueries.at(-1).order_category, 'meal');
    assert.equal(state.orderQueries.at(-1).payment_status, 'paid');
    await verify_read_only(page, state);
  });
}
