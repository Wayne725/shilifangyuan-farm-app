import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import test from 'node:test';

const { chromium, webkit } = createRequire(import.meta.url)('playwright');
const engine = process.env.BROWSER_ENGINE === 'webkit' ? webkit : chromium;
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['127.0.0.1', 'localhost'].includes(target.hostname)) throw new Error('Use isolated local fixtures only');
const base = target.origin;
const original = {id: 'job-1', order_id: 'order-1', order_number: 'SLF-PRINT-001', status: 'unknown', mode: 'live', kind: 'order', reprint_of_id: null, attempts: 1, can_query: true, last_error: 'transport_result_unknown', pickup_at: '2030-01-03 11:30', created_at: '2030-01-02T02:00:00Z'};

async function fixture(t, width, dryRun = true, admin = true) {
  const browser = await engine.launch({headless: true, ...(engine === chromium ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
  t.after(() => browser.close());
  const context = await browser.newContext({viewport: {width, height: 900}, timezoneId: 'Asia/Taipei', serviceWorkers: 'block'});
  const state = {jobs: [structuredClone(original)], reads: [], writes: [], unknown: [], external: [], errors: [], reprintConflict: false};
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
    if (path === '/v1/admin/printers/jobs/job-1') return route.fulfill({json: {...state.jobs[0], preview: `十里方圓 合作社\n${state.jobs[0].kind === 'amendment' ? '數量修訂單' : '訂餐單'}\n姓名：測試同學\n繁體中文餐點\n付款方式：現場付款\n付款狀態：未付款`}});
    if (path === '/v1/admin/printers/jobs/job-1/reprint') {
      const body = request.postDataJSON(); state.writes.push({path, body});
      if (state.reprintConflict) return route.fulfill({status: 409, json: {detail: '訂單數量已更新，請選擇最新版本補印'}});
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
  for (const value of ['lunch', 'dinner', 'unspecified']) {
    const changed = page.waitForResponse(response => response.url().includes('/meal-kitchen?') && new URL(response.url()).searchParams.get('meal_period') === value);
    await page.getByRole('combobox', {name: '備餐時段', exact: true}).selectOption(value);
    await changed;
    const read = state.reads.filter(url => url.pathname.endsWith('/meal-kitchen')).at(-1);
    assert.equal(read.searchParams.get('search'), '王小明');
    assert.equal(read.searchParams.get('fulfillment_status'), 'preparing');
    assert.equal(read.searchParams.get('offset'), '0');
  }
  await page.getByRole('combobox', {name: '備餐時段', exact: true}).selectOption('all');
  assert.equal(await page.getByRole('combobox', {name: '備餐時段', exact: true}).inputValue(), 'all');
  await kitchenForm.getByRole('button', {name: '清除', exact: true}).click();
  assert.equal(await kitchenForm.getByRole('searchbox').inputValue(), '');
  assert.deepEqual(state.writes, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});

for (const width of [320, 390, 899, 1542]) {
  test(`${width}px 列印搜尋與備餐控制同組對齊，設備工具獨立且可操作`, async t => {
    const {page, state} = await fixture(t, width);
    await page.goto(`${base}/admin/kitchen`);
    await page.getByText('模擬模式', {exact: true}).waitFor();
    for (const large of [false, true]) {
      await page.locator('.reading-menu > summary').click();
      await page.getByRole('group', {name: '文字大小'}).getByRole('button', {name: large ? '放大' : '標準', exact: true}).click();
      await page.waitForFunction(value => document.documentElement.dataset.textSize === (value ? 'large' : 'standard'), large);
      await page.evaluate(() => document.fonts.ready);
      const form = page.getByRole('form', {name: '列印紀錄查詢', exact: true});
      const tools = page.locator('.cloud-print-tools');
      const search = await form.getByRole('searchbox').boundingBox();
      const select = await form.getByLabel('列印狀態', {exact: true}).boundingBox();
      const query = await form.getByRole('button', {name: '查詢', exact: true}).boundingBox();
      const clear = await form.getByRole('button', {name: '清除', exact: true}).boundingBox();
      const device = await tools.getByRole('button', {name: '查詢設備', exact: true}).boundingBox();
      const help = await tools.getByRole('link', {name: '出單操作說明', exact: true}).boundingBox();
      const period = await page.getByRole('combobox', {name: '備餐時段', exact: true}).boundingBox();
      const date = await page.getByLabel('取餐日期', {exact: true}).boundingBox();
      const refresh = await page.getByRole('button', {name: '更新清單', exact: true}).boundingBox();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      if (process.env.CLOUD_PRINT_LAYOUT_EVIDENCE_DIR) {
        const dir = process.env.CLOUD_PRINT_LAYOUT_EVIDENCE_DIR;
        await mkdir(dir, {recursive: true});
        const name = `${process.env.BROWSER_ENGINE || 'chromium'}-${width}-${large ? 'large' : 'standard'}`;
        await writeFile(`${dir}/${name}.json`, JSON.stringify({width, large, search, select, query, clear, device, help, date, period, refresh, overflow}, null, 2));
        await page.screenshot({path: `${dir}/${name}.png`, fullPage: true});
      }
      assert.ok(Math.abs(search.height - select.height) < 1, `搜尋／狀態高度 ${search.height}/${select.height}`);
      assert.ok(Math.abs(query.height - clear.height) < 1, `查詢／清除高度 ${query.height}/${clear.height}`);
      for (const [name, box] of Object.entries({search, select, query, clear, device, help})) assert.ok(box.height >= 48, `${name}操作高度 ${box.height}`);
      assert.equal(await form.getByRole('button', {name: '查詢設備', exact: true}).count(), 0);
      assert.ok(query.y >= Math.max(search.y + search.height, select.y + select.height), '查詢／清除位於查詢欄位之後');
      assert.ok(device.y >= query.y + query.height && help.y >= query.y + query.height, '設備／說明與搜尋操作分組');
      const overlapWidth = Math.min(device.x + device.width, help.x + help.width) - Math.max(device.x, help.x);
      const overlapHeight = Math.min(device.y + device.height, help.y + help.height) - Math.max(device.y, help.y);
      assert.ok(overlapWidth <= 0 || overlapHeight <= 0, '設備與說明操作不得重疊');
      if (width <= 600) {
        assert.ok(select.y >= search.y + search.height, '手機搜尋與狀態為單欄順序');
        assert.ok(Math.abs(search.x - select.x) < 1 && Math.abs(search.width - select.width) < 1, '手機欄位同寬對齊');
      } else {
        assert.ok(Math.abs(search.y + search.height - select.y - select.height) < 1, '桌面搜尋與狀態底部對齊');
      }
      assert.ok(Math.abs(period.height - date.height) < 1);
      assert.ok(Math.abs(period.height - refresh.height) < 1);
      if (width >= 1200) assert.ok(Math.abs(period.y + period.height - refresh.y - refresh.height) < 1);
      assert.ok(overflow <= 1, `頁面水平溢出 ${overflow}`);
    }
    assert.deepEqual(state.writes, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
  });
}

for (const reprint of [false, true]) {
  test(`修訂出單${reprint ? '補印' : '原票'}顯示正確種類與版本`, async t => {
    const {page, state} = await fixture(t, 390);
    Object.assign(state.jobs[0], {kind: 'amendment', revision: 2, reprint_of_id: reprint ? 'earlier-job' : null});
    await page.goto(`${base}/admin/kitchen`);
    const jobs = page.locator('.cloud-print-jobs'); await jobs.getByRole('button', {name: '查看／補印', exact: true}).waitFor();
    assert.match(await jobs.innerText(), reprint ? /數量修訂單補印 · 第 2 版/ : /數量修訂單 · 第 2 版/);
    assert.doesNotMatch(await jobs.innerText(), /訂餐單/);
    await jobs.getByRole('button', {name: '查看／補印', exact: true}).click();
    assert.match(await page.locator('.cloud-print-preview h3').innerText(), /數量修訂單.*第 2 版/);
    assert.deepEqual(state.writes, []); assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  });
}

test('舊版本停止發送顯示可讀原因，補印409保留原因且不宣稱成功', async t => {
  const {page, state} = await fixture(t, 390);
  Object.assign(state.jobs[0], {status: 'failed', revision: 1, last_error: 'order_revision_superseded'}); state.reprintConflict = true;
  await page.goto(`${base}/admin/kitchen`);
  await page.getByText('數量已更新，舊單已停止發送', {exact: true}).waitFor();
  await page.getByRole('button', {name: '查看／補印', exact: true}).click();
  await page.getByLabel('補印原因', {exact: true}).fill('核對最新數量');
  await page.getByRole('button', {name: '建立模擬補印', exact: true}).click();
  await page.getByText('訂單數量已更新，請選擇最新版本補印', {exact: true}).waitFor();
  assert.equal(await page.getByLabel('補印原因', {exact: true}).inputValue(), '核對最新數量');
  assert.equal(state.jobs.length, 1); assert.equal(state.writes.length, 1);
  assert.equal(await page.getByText('已建立模擬補印工作：等待發送', {exact: true}).count(), 0);
  assert.deepEqual(state.unknown, []); assert.deepEqual(state.errors, []);
});
