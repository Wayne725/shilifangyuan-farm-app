import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium } = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;
const original = {id: 'job-1', order_id: 'order-1', order_number: 'SLF-PRINT-001', status: 'unknown', mode: 'live', kind: 'order', reprint_of_id: null, attempts: 1, can_query: true, last_error: 'transport_result_unknown', pickup_at: '2030-01-03 11:30', created_at: '2030-01-02T02:00:00Z'};

async function fixture(t, width, dryRun = true, admin = true) {
  const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE});
  t.after(() => browser.close());
  const context = await browser.newContext({viewport: {width, height: 900}, timezoneId: 'Asia/Taipei', serviceWorkers: 'block'});
  const state = {jobs: [structuredClone(original)], reads: [], writes: [], unknown: [], external: [], errors: []};
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (url.origin !== base) {state.external.push(url.origin); return route.abort();}
    if (!path.startsWith('/v1/')) return route.continue();
    if (path === '/v1/auth/refresh') return route.fulfill({json: {access_token: 'fixture', user: {id: 'fixture-user', email: 'fixture@example.test', display_name: '測試管理員', user_role: admin ? 'admin' : 'customer'}}});
    if (path === '/v1/platform') return route.fulfill({json: {orders_open: true, closed_message: ''}});
    if (path === '/v1/admin/platform') return route.fulfill({json: {kitchen_sound: false}});
    if (path === '/v1/admin/meal-kitchen') {state.reads.push(url); return route.fulfill({json: {items: [], total: 0, meal_quantity: 0, products: []}});}
    if (path === '/v1/admin/printers/status') return route.fulfill({json: {enabled: true, dry_run: dryRun, status: dryRun ? 'dry_run' : 'offline', error: null}});
    if (path === '/v1/admin/printers/jobs') {
      state.reads.push(url);
      const jobs = state.jobs.filter(job => (!url.searchParams.get('status') || job.status === url.searchParams.get('status')) && (!url.searchParams.get('search') || job.order_number.includes(url.searchParams.get('search'))));
      return route.fulfill({json: {items: jobs, total: jobs.length}});
    }
    if (path === '/v1/admin/printers/jobs/job-1') return route.fulfill({json: {...state.jobs[0], preview: '十里方圓 合作社\n訂餐單\n姓名：測試同學\n繁體中文餐點\n付款方式：現場付款\n付款狀態：未付款'}});
    if (path === '/v1/admin/printers/jobs/job-1/reprint') {
      const body = request.postDataJSON(); state.writes.push({path, body});
      const job = {...original, id: 'job-2', status: 'pending', mode: dryRun ? 'dry_run' : 'live', reprint_of_id: 'job-1', last_error: null};
      state.jobs.push(job);
      return route.fulfill({status: 201, json: job});
    }
    if (path === '/v1/admin/printers/jobs/job-1/query') {
      state.writes.push({path});
      return route.fulfill({json: state.jobs[0]});
    }
    if (path === '/v1/admin/printers/jobs/job-1/resolve') {
      const body = request.postDataJSON(); state.writes.push({path, body});
      state.jobs[0] = {...state.jobs[0], status: body.printed ? 'printed' : 'unknown'};
      return route.fulfill({json: state.jobs[0]});
    }
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({status: 404, json: {detail: 'Undefined isolated route'}});
  });
  const page = await context.newPage(); page.setDefaultTimeout(7000);
  page.on('pageerror', error => state.errors.push(error.message));
  return {page, state};
}

for (const width of [390, 1280]) {
  test(`${width}px 雲端出單待確認可查詢並明確確認模擬補印`, async t => {
    const {page, state} = await fixture(t, width);
    await page.goto(`${base}/admin/kitchen`);
    await page.getByText('模擬模式', {exact: true}).waitFor();
    await page.getByRole('button', {name: '查看／補印', exact: true}).click();
    await page.getByRole('region', {name: '雲端出單預覽'}).waitFor();
    await page.getByRole('button', {name: '重新查詢結果', exact: true}).click();
    await page.getByText('已安排查詢雲端結果', {exact: true}).waitFor();
    await page.getByLabel('補印原因', {exact: true}).fill('已核對紙本，測試補印');
    assert.equal(await page.getByRole('button', {name: '建立模擬補印', exact: true}).count(), 1);
    await page.getByRole('button', {name: '建立模擬補印', exact: true}).click();
    assert.equal(state.writes.length, 1, '未勾選重複確認不能補印');
    await page.getByLabel('已核對紙本，接受可能重複列印', {exact: true}).check();
    if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({path: `${process.env.BROWSER_ARTIFACT_DIR}/cloud-print-${width}.png`, fullPage: true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.getByRole('button', {name: '建立模擬補印', exact: true}).click();
    await page.getByText('已建立模擬補印工作：等待發送', {exact: true}).waitFor();
    assert.equal(state.writes.length, 2);
    assert.equal(state.writes[1].body.acknowledge_duplicate, true);
    assert.match(state.writes[1].body.request_id, /^[0-9a-f-]{36}$/);
    assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
}

test('人工核對正式工作與狀態篩選', async t => {
  const {page, state} = await fixture(t, 390, false);
  await page.goto(`${base}/admin/kitchen`);
  await page.getByText('離線', {exact: true}).waitFor();
  await page.getByRole('button', {name: '查看／補印', exact: true}).click();
  await page.getByLabel('補印原因', {exact: true}).fill('已找到紙本單號');
  await page.getByRole('button', {name: '確認紙本已印', exact: true}).click();
  await page.getByText('已記錄人工核對結果：已確認列印', {exact: true}).waitFor();
  assert.equal(state.writes[0].body.printed, true);
  await page.getByLabel('列印狀態', {exact: true}).selectOption('failed');
  await page.getByText('沒有符合的列印工作', {exact: true}).waitFor();
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('非管理員不顯示雲端出單且不讀取紀錄', async t => {
  const {page, state} = await fixture(t, 390, true, false);
  await page.goto(`${base}/admin/kitchen`);
  await page.getByRole('heading', {name: '廚房清單只向管理員開放。', exact: true}).waitFor();
  assert.equal(await page.getByRole('button', {name: '查詢設備', exact: true}).count(), 0);
  assert.deepEqual(state.writes, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

test('列印與備餐各自查詢並清除，不執行出單或更改訂單', async t => {
  const {page, state} = await fixture(t, 390);
  await page.goto(`${base}/admin/kitchen`);
  const printForm = page.getByRole('form', {name: '列印紀錄查詢'});
  await printForm.getByRole('searchbox').fill('NO-MATCH');
  await printForm.getByRole('button', {name: '查詢', exact: true}).click();
  await page.getByText('沒有符合的列印工作', {exact: true}).waitFor();
  assert.equal(state.reads.filter(url => url.pathname.endsWith('/printers/jobs')).at(-1).searchParams.get('search'), 'NO-MATCH');
  await printForm.getByRole('button', {name: '清除', exact: true}).click();
  await page.getByRole('button', {name: '查看／補印', exact: true}).waitFor();
  const kitchenForm = page.getByRole('form', {name: '備餐訂單查詢'});
  await kitchenForm.getByRole('searchbox').fill('  王小明  ');
  const searched = page.waitForResponse(response => response.url().includes('/meal-kitchen?') && new URL(response.url()).searchParams.get('search') === '王小明');
  await kitchenForm.getByRole('button', {name: '查詢', exact: true}).click();
  await searched;
  const filtered = page.waitForResponse(response => response.url().includes('/meal-kitchen?') && new URL(response.url()).searchParams.get('fulfillment_status') === 'preparing');
  await kitchenForm.getByRole('combobox', {name: '備餐狀態'}).selectOption('preparing');
  await filtered;
  const latest = state.reads.filter(url => url.pathname.endsWith('/meal-kitchen')).at(-1);
  assert.equal(latest.searchParams.get('search'), '王小明');
  assert.equal(latest.searchParams.get('offset'), '0');
  await kitchenForm.getByRole('button', {name: '清除', exact: true}).click();
  assert.equal(await kitchenForm.getByRole('searchbox').inputValue(), '');
  assert.deepEqual(state.writes, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

for (const width of [390, 899, 1542]) {
  test(`${width}px 列印查詢控制等高且桌面底部對齊`, async t => {
    const {page} = await fixture(t, width);
    await page.goto(`${base}/admin/kitchen`);
    await page.getByText('模擬模式', {exact: true}).waitFor();
    for (const large of [false, true]) {
      await page.evaluate(value => { document.documentElement.dataset.textSize = value ? 'large' : 'standard'; }, large);
      const select = await page.getByLabel('列印狀態', {exact: true}).boundingBox();
      const button = await page.getByRole('button', {name: '查詢設備', exact: true}).boundingBox();
      assert.ok(Math.abs(select.height - button.height) < 1, `高度 ${select.height}/${button.height}`);
      if (width >= 1200) assert.ok(Math.abs(select.y + select.height - button.y - button.height) < 1, `下緣 ${select.y + select.height}/${button.y + button.height}`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    }
  });
}
