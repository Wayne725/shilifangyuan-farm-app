import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4194');
if (!['localhost', '127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Use an isolated local origin');
const base = target.origin;
const option = (id, name, price_delta = 0, image_url = null) => ({ id, name, price_delta, image_url, is_active: true });
const staple = { id: 'staple', name: '主食選擇', min_selections: 1, max_selections: 1, is_active: true,
  options: [option('rice', '紫米飯'), option('bread', '麵包'), option('vegetables', '不要主食／換菜', 10), { ...option('hidden', '停售主食'), is_active: false }] };
const drink = { id: 'drink', name: '飲品加購', min_selections: 0, max_selections: 1, is_active: true,
  options: [option('soda', '茂谷汽水', 20, '/assets/meals/sandwich-salad.webp'), option('tea', '茶', 10), option('water', '水')] };
const extras = { id: 'extras', name: '配菜加購', min_selections: 0, max_selections: 2, is_active: true,
  options: [option('egg', '雞蛋', 10), option('corn', '玉米', 10), option('tofu', '豆腐', 10)] };
const sauce = { id: 'sauce', name: '醬料選擇', min_selections: 0, max_selections: 1, is_active: true,
  options: [option('soy', '醬油'), option('lemon', '檸檬醬'), option('sesame', '芝麻醬', 5)] };
const event = { id: 'compact-meal', title: '隔離便當', location: '合作社', status: 'published', service_date: '2030-01-03', meal_period: 'lunch',
  ordering_starts_at: '2030-01-02T00:00:00+08:00', ordering_ends_at: '2030-01-02T20:00:00+08:00',
  pickup_starts_at: '2030-01-03T11:00:00+08:00', pickup_ends_at: '2030-01-03T13:30:00+08:00',
  offerings: [{ id: 'dish', meal_id: 'dish', meal_name: '蔬食便當', price: 120, capacity: 20, available_quantity: 20, reserved_quantity: 0, paid_quantity: 0, position: 0, is_active: true, option_groups: [staple, drink, extras, sauce] }] };
const channels = ['home_delivery', 'seven_eleven', 'family_mart', 'hilife'];
const product = { id: 'compact-product', name: '隔離蔭油', unit: '瓶', supplier_name: '合作社', member_price: 300, nonmember_price: 300, stock_quantity: 20, is_active: true, can_ship: true, shipping_temperature: 'ambient', allowed_shipping_channels: channels, tax_type: 'taxable' };
const campaign = { id: 'compact-group', title: '隔離蔭油團購', description: '', member_price: 300, nonmember_price: 300, intake_status: 'open', decision_status: 'recruiting', paid_quantity: 0, min_paid_quantity: 10, supply_cap: 50, per_user_cap: 5, available_quantity: 50, deadline: '2030-01-04T00:00:00Z', estimated_pickup_start: '2030-01-05T00:00:00Z', can_ship: true, shipping_temperature: 'ambient', allowed_shipping_channels: channels };
const locations = [{ id: 'store', name: '合作社門市（水木書苑內左側）', address: '新竹市合作路 1 號', is_active: true }, { id: 'campus', name: '教育學院大樓', address: '新竹市教育路 2 號', is_active: true }];

async function fixture(t, width, locationState = 'ready') {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block', timezoneId: 'Asia/Taipei' });
  await context.addInitScript((item) => localStorage.setItem('slf_cart', JSON.stringify([{ product: item, quantity: 1 }])), product);
  const state = { unknown: [], writes: [], quotes: [], errors: [] };
  await context.route('**/*', (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) return route.abort();
    if (!path.startsWith('/v1/')) return request.method() === 'GET' ? route.continue() : route.abort();
    if (path === '/v1/auth/refresh') return route.fulfill({ json: { access_token: 'isolated', user: { id: 'buyer', email: 'buyer@example.test', display_name: '隔離買家', user_role: 'customer' } } });
    if (path === '/v1/platform') return route.fulfill({ json: { orders_open: true, sales_scope: 'all', shipping_enabled: true } });
    if (path === '/v1/pickup-locations') return route.fulfill({ status: locationState === 'error' ? 503 : 200, json: locationState === 'error' ? { detail: '隔離讀取失敗' } : locationState === 'empty' ? [] : locations });
    if (path === '/v1/shipping-rates') return route.fulfill({ json: channels.map((channel) => ({ channel, temperature: 'ambient', base_fee: 60, free_shipping_threshold: null, is_active: true })) });
    if (path === '/v1/meal-events/compact-meal') return route.fulfill({ json: event });
    if (path === '/v1/group-campaigns/compact-group') return route.fulfill({ json: campaign });
    if (path.endsWith('/quote')) {
      const body = request.postDataJSON(); state.quotes.push({ path, body });
      if (path.includes('meal-events')) {
        const selected = body.items[0].option_ids;
        const chosen = event.offerings[0].option_groups.flatMap((group) => group.options.filter((item) => selected.includes(item.id)).map((item) => ({ group_name: group.name, option_name: item.name, price_delta: item.price_delta })));
        const extra = chosen.reduce((sum, item) => sum + item.price_delta, 0);
        return route.fulfill({ json: { amount_total: (120 + extra) * body.items[0].quantity, items: [{ offering_id: 'dish', meal_name: '蔬食便當', quantity: body.items[0].quantity, base_price: 120, option_price: extra, unit_price: 120 + extra, subtotal: (120 + extra) * body.items[0].quantity, selections: chosen }], pickup: { location: '合作社', starts_at: event.pickup_starts_at, ends_at: event.pickup_ends_at } } });
      }
      return route.fulfill({ json: { quantity: 1, unit_price: 300, product_subtotal: 300, amount_total: 300, shipping_fee: 0, items: [{ product_id: product.id, product_name: product.name, quantity: 1, unit_price: 300, subtotal: 300, tax_type: 'taxable' }] } });
    }
    if (request.method() !== 'GET') state.writes.push(path);
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: { detail: 'Undefined isolated route' } });
  });
  const page = await context.newPage(); page.setDefaultTimeout(7000);
  page.on('pageerror', (error) => state.errors.push(error.message));
  await page.clock.setFixedTime(new Date('2030-01-02T10:00:00+08:00'));
  return { page, state };
}

async function verify(page, state) {
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.writes, []); assert.deepEqual(state.errors, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  const contained = await page.locator('main select:visible').evaluateAll((elements) => elements.every((element) => {
    const box = element.getBoundingClientRect(), parent = element.parentElement.getBoundingClientRect();
    return box.left >= parent.left - 1 && box.right <= parent.right + 1 && box.height >= 44;
  }));
  assert.equal(contained, true);
}

for (const width of [1440, 390]) {
  test(`${width}px 主食單選保留必選與加價，圖片、多選維持可見，時段與選餐可重整`, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/meals/compact-meal?pickup_at=2030-01-03T12%3A00`);
    await page.getByRole('button', { name: '增加蔬食便當', exact: true }).click();
    const choice = page.getByRole('combobox', { name: '蔬食便當 主食選擇', exact: true });
    assert.equal(await choice.getAttribute('required'), '');
    assert.equal(await choice.getAttribute('aria-invalid'), 'true');
    assert.equal(state.quotes.length, 0);
    assert.equal(await choice.getByRole('option', { name: '停售主食', exact: true }).count(), 0);
    await choice.selectOption('vegetables');
    await page.locator('.offer-total dd').getByText('$130', { exact: true }).waitFor();
    assert.deepEqual(state.quotes.at(-1).body.items[0].option_ids, ['vegetables']);
    assert.equal(await choice.getAttribute('aria-invalid'), 'false');
    assert.match(await choice.locator('option:checked').innerText(), /\+\$10/);
    assert.equal(await page.getByRole('button', { name: '茂谷汽水 +$20', exact: true }).isVisible(), true);
    assert.equal(await page.getByRole('button', { name: '雞蛋 +$10', exact: true }).isVisible(), true);
    const optional = page.getByRole('combobox', { name: '蔬食便當 醬料選擇', exact: true });
    await optional.selectOption('sesame');
    await page.locator('.offer-total dd').getByText('$135', { exact: true }).waitFor();
    await optional.selectOption('');
    await page.locator('.offer-total dd').getByText('$130', { exact: true }).waitFor();
    await page.getByText('修改取餐時間', { exact: true }).click();
    await page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true }).selectOption('2030-01-03T12:30');
    await page.waitForURL(/pickup_at=.*12%3A30/);
    await page.reload();
    assert.equal(await choice.inputValue(), 'vegetables');
    await page.getByText('修改取餐時間', { exact: true }).click();
    assert.equal(await page.getByRole('combobox', { name: '預計取餐時間（台灣時間）', exact: true }).inputValue(), '2030-01-03T12:30');
    await page.locator('.reading-menu > summary').click();
    await page.getByRole('button', { name: '放大', exact: true }).click();
    if (await page.locator('.reading-menu').getAttribute('open') !== null) await page.locator('.reading-menu > summary').click();
    await verify(page, state);
    if (width === 390 && process.env.BROWSER_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.BROWSER_SCREENSHOT_DIR}/下拉選單-便當手機驗證.png`, fullPage: true });
  });

  test(`${width}px 團購取貨地點摘要同步，與一般結帳均可切換配送通路`, async (t) => {
    const { page, state } = await fixture(t, width);
    await page.goto(`${base}/groups/compact-group`);
    await page.getByRole('combobox', { name: '取貨地點', exact: true }).selectOption('campus');
    assert.equal(await page.locator('.offer-summary').getByText('教育學院大樓', { exact: true }).count(), 1);
    assert.equal(await page.locator('.offer-summary').getByText('新竹市教育路 2 號', { exact: true }).count(), 1);
    assert.equal(await page.locator('.offer-summary').getByText('新竹市合作路 1 號', { exact: true }).count(), 0);
    await page.getByRole('combobox', { name: '取貨方式', exact: true }).selectOption('shipping');
    assert.match(await page.getByRole('combobox', { name: '取貨方式', exact: true }).locator('option:checked').innerText(), /常溫/);
    await page.getByRole('combobox', { name: '配送通路', exact: true }).selectOption('family_mart');
    await page.waitForFunction(() => document.querySelector('.shipping-form select')?.value === 'family_mart');
    await verify(page, state);
    await page.goto(`${base}/checkout`);
    await page.getByRole('combobox', { name: '取貨方式', exact: true }).selectOption('shipping');
    const channel = page.getByRole('combobox', { name: '配送通路', exact: true });
    await channel.selectOption('seven_eleven');
    assert.equal(await channel.inputValue(), 'seven_eleven');
    assert.equal(await channel.getByRole('option').count(), 5);
    await verify(page, state);
  });
}

for (const locationState of ['empty', 'error']) {
  test(`團購取貨點 ${locationState} 時停用下拉與付款`, async (t) => {
    const { page, state } = await fixture(t, 390, locationState);
    await page.goto(`${base}/groups/compact-group`);
    const place = page.getByRole('combobox', { name: '取貨地點', exact: true });
    await place.waitFor();
    if (locationState === 'error') await page.getByText('取貨點暫時無法使用，請稍後再試。', { exact: true }).waitFor();
    assert.equal(await place.isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: '參加並前往付款', exact: true }).isDisabled(), true);
    await verify(page, state);
  });
}
