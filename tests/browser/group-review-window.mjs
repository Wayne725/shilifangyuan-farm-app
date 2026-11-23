import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
const { chromium, webkit }=createRequire(import.meta.url)('playwright');
const engine=process.env.BROWSER_ENGINE==='webkit'?webkit:chromium;
const target=new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197');
if (!['localhost','127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Only isolated local tests');
const base=target.origin;
const now='2030-01-02T10:00:00+08:00';
const adminCampaignList='/v1/group-campaigns?include_hidden=true';
const adminWorkspaceReads=new Set(['/v1/products','/v1/group-bundles','/v1/vote-proposals',adminCampaignList]);
const product={id:'product',name:'原始團購白米',category:'蔬果',description:'隔離商品',unit:'袋',member_price:90,nonmember_price:100,stock_quantity:30,is_active:true,can_ship:false,allowed_shipping_channels:[]};
const closedCampaign={id:'campaign',title:'每月預訂隔離共同購買',description:'隔離測試',target_type:'product',target_id:'product',member_price:90,nonmember_price:100,min_paid_quantity:10,supply_cap:30,per_user_cap:5,paid_quantity:1,reserved_quantity:0,available_quantity:29,deadline:'2030-01-01T04:00:00Z',confirmation_deadline:'2030-01-04T04:00:00Z',estimated_pickup_start:'2030-01-06T04:00:00Z',estimated_pickup_end:'2030-01-07T04:00:00Z',preorder_only:true,decision_status:'confirmed',intake_status:'closed',threshold_version:2,updated_at:'2030-01-01T05:00:00Z',review_overdue:false,can_confirm:false,can_edit_review_deadline:false,confirmation_blocked_reason:null,can_ship:false,allowed_shipping_channels:[],core_locked_at:'2030-01-01T00:00:00Z'};
const openCampaign={...closedCampaign,decision_status:'confirmed',intake_status:'open',deadline:'2030-01-03T04:00:00Z',confirmation_deadline:'2030-01-06T04:00:00Z',estimated_pickup_start:'2030-01-07T04:00:00Z',estimated_pickup_end:'2030-01-08T04:00:00Z',can_confirm:false};
async function fixture(t,{role='admin',width=1280,path='/admin/groups',campaign=closedCampaign,proposal=false,proposalStatus='pending_review',proposalRows,timezone='Asia/Taipei'}={}) {
  const browser=await engine.launch({headless:true,...(engine===chromium?{executablePath:process.env.BROWSER_EXECUTABLE}:{})});t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width,height:950},timezoneId:timezone,serviceWorkers:'block',reducedMotion:'reduce'});
  const state={campaign:structuredClone(campaign),reads:[],readUrls:[],unauthenticatedAdminReads:[],quotes:[],writes:[],unknown:[],errors:[],failGet:null,failWrite:null,changeOnConflict:null};
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),endpoint=url.pathname,method=request.method();
    if(url.origin!==base)return route.abort();
    if(!endpoint.startsWith('/v1/'))return method==='GET'?route.continue():route.abort();
    if(endpoint==='/v1/auth/refresh')return route.fulfill({status:role==='guest'?401:200,json:role==='guest'?{detail:'訪客'}:{access_token:'isolated',user:{id:'review-user',email:'review@example.test',display_name:'隔離管理者',user_role:role,membership_type:'nonmember'}}});
    if(method==='GET') {
      const readPath=endpoint+url.search;
      state.reads.push(endpoint);state.readUrls.push(url.href);
      if(path==='/admin/groups'&&adminWorkspaceReads.has(readPath)&&!request.headers().authorization)state.unauthenticatedAdminReads.push(readPath);
      if(state.failGet?.path===readPath)return route.fulfill({status:state.failGet.status,json:{detail:'隔離權限失效'}});
    }
    if(endpoint==='/v1/platform')return route.fulfill({json:{orders_open:true,regular_orders_enabled:true,group_orders_enabled:true,shipping_enabled:false}});
    if(endpoint==='/v1/products')return route.fulfill({json:[product]});
    if(endpoint==='/v1/group-bundles')return route.fulfill({json:[]});
    if(endpoint==='/v1/vote-proposals/proposal'&&method==='GET')return route.fulfill({json:{id:'proposal',target_type:'product',target_id:'product',target_name:'歷史白米需求',status:'voting',threshold:999,vote_count:1,estimated_quantity:2,my_vote_quantity:2,created_at:'2030-01-01T00:00:00Z',deadline:'2030-01-03T00:00:00Z'}});
    if(endpoint==='/v1/vote-proposals')return route.fulfill({json:proposalRows ?? (proposal?[{id:'proposal',target_type:'product',target_id:'product',target_name:'提案隔離白米',status:proposalStatus,threshold:10,vote_count:0,estimated_quantity:0}]:[])});
    if(endpoint==='/v1/group-campaigns'&&method==='GET')return route.fulfill({json:url.searchParams.get('include_hidden')==='true'||state.campaign.is_public!==false?[state.campaign]:[]});
    if(endpoint==='/v1/group-campaigns/campaign'&&method==='GET')return route.fulfill({json:state.campaign});
    if(endpoint==='/v1/pickup-locations')return route.fulfill({json:[{id:'store',name:'隔離取貨點',is_active:true}]});
    if(endpoint==='/v1/group-campaigns/campaign/quote'){state.quotes.push(request.postDataJSON());return route.fulfill({json:{confirmation_fingerprint:'a'.repeat(64),quantity:1,unit_price:100,product_subtotal:100,shipping_fee:0,amount_total:100,membership_type:'nonmember'}});}
    if((endpoint.startsWith('/v1/group-campaigns')||endpoint.endsWith('/admin/open-direct')||endpoint.endsWith('/admin/approve'))&&['POST','PATCH'].includes(method)) {
      const body=request.postDataJSON();state.writes.push({path:endpoint,method,body});
      if(state.failWrite){const failure=state.failWrite;if(failure.status!==401)state.failWrite=null;if(state.changeOnConflict)Object.assign(state.campaign,state.changeOnConflict);return route.fulfill({status:failure.status,json:{detail:failure.message}});}
      if(endpoint==='/v1/group-campaigns/campaign'&&method==='PATCH'){const {expected_updated_at,...changes}=body;Object.assign(state.campaign,changes);if('estimated_pickup_start' in body||'estimated_pickup_end' in body)state.campaign.final_pickup_at=null;state.campaign.updated_at=new Date(Date.parse(state.campaign.updated_at)+1000).toISOString();}
      if(endpoint.endsWith('/admin/visibility')){assert.equal(body.expected_updated_at,state.campaign.updated_at);state.campaign.is_public=body.is_public;state.campaign.updated_at=new Date(Date.parse(state.campaign.updated_at)+1000).toISOString();}
      return route.fulfill({json:state.campaign});
    }
    state.unknown.push(`${method} ${endpoint}`);return route.fulfill({status:404,json:{detail:'未定義的隔離API'}});
  });
  const page=await context.newPage();page.setDefaultTimeout(9000);page.on('pageerror',error=>state.errors.push(error.message));await page.clock.install({time:new Date(now)});
  const visit=async()=>{await page.goto(`${base}${path}`);};
  return {page,state,visit};
}
const card=page=>page.getByRole('article',{name:'共同購買 每月預訂隔離共同購買',exact:true});
async function clean(page,state){assert.deepEqual(state.unknown,[]);assert.deepEqual(state.errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);}
async function waitForAdminDenied(page,status){if(status===401)await page.getByRole('heading',{name:'共同購買管理只向管理者開放。',exact:true}).waitFor();else await page.getByText('共同購買管理權限已失效').waitFor();}


test('手機團購提案可依標題和台灣日期查詢並分頁', async t => {
  const proposalBase = {
    target_type: 'product', target_id: 'product', status: 'rejected',
    threshold: 10, vote_count: 0, estimated_quantity: 0, created_at: '2030-01-02T02:00:00Z',
  };
  const proposalRows = [
    ...Array.from({ length: 10 }, (_, index) => ({
      ...proposalBase, id: `history-${index}`, target_name: `歷史提案 ${index + 1}`,
    })),
    { ...proposalBase, id: 'midnight', target_name: '烏龍 Tea', status: 'pending_review', created_at: '2030-01-01T16:00:00Z' },
    { ...proposalBase, id: 'before-midnight', target_name: '紅茶 Tea', status: 'voting', created_at: '2030-01-01T15:59:59Z', deadline: '2030-01-03T00:00:00Z' },
    { ...proposalBase, id: 'expired', target_name: '花茶 Tea', status: 'voting', created_at: '2029-12-31T16:00:00Z', deadline: '2030-01-01T00:00:00Z' },
  ];
  const { page, state, visit } = await fixture(t, {
    width: 390, timezone: 'America/New_York', path: '/admin/groups?tab=proposals', proposalRows,
  });
  await visit();
  const list = page.locator('.admin-review-list');
  const pagination = page.getByRole('navigation', { name: '團購提案分頁' });
  await pagination.getByText('第 1／2 頁').waitFor();
  assert.equal(await list.getByRole('article').count(), 10);
  assert.equal(await pagination.getByRole('button', { name: '上一頁' }).isDisabled(), true);
  await pagination.getByRole('button', { name: '下一頁' }).click();
  await pagination.getByText('第 2／2 頁').waitFor();
  assert.equal(await list.getByRole('article').count(), 3);

  const search = page.getByRole('form', { name: '查詢共同購買提案' });
  await search.getByRole('searchbox', { name: '提案標題' }).fill(' TEA ');
  await search.getByLabel('提案日期起').fill('2030-01-02');
  await search.getByLabel('提案日期迄').fill('2030-01-01');
  await page.getByRole('alert').getByText('結束日期不可早於開始日期').waitFor();
  assert.equal(await search.getByRole('button', { name: '查詢提案' }).isDisabled(), true);
  await search.getByLabel('提案日期迄').fill('2030-01-02');
  await search.getByRole('button', { name: '查詢提案' }).click();
  await list.getByText(/2030\/01\/02 提案/).waitFor();
  assert.deepEqual(await list.getByRole('heading', { level: 3 }).allTextContents(), ['烏龍 Tea']);
  assert.equal(await pagination.count(), 0);
  assert.equal(await list.getByRole('button', { name: '通過並開放投票' }).count(), 0);

  await search.getByRole('button', { name: '清除查詢' }).click();
  await pagination.getByText('第 1／2 頁').waitFor();
  await search.getByLabel('提案範圍').selectOption('voting');
  await list.getByRole('heading', { name: '紅茶 Tea' }).waitFor();
  assert.deepEqual(await list.getByRole('heading', { level: 3 }).allTextContents(), ['紅茶 Tea']);
  assert.deepEqual(state.writes, []);
  await clean(page, state);
});

test('已截止且有付款的共同購買可更正公開說明', async t => {
  const { page, state, visit } = await fixture(t, { width: 390 });
  const before = structuredClone(state.campaign);
  await visit();
  await card(page).getByRole('button', { name: '編輯名稱與說明' }).click();
  const editor = card(page).getByRole('form', { name: '編輯共同購買名稱與說明' });
  const save = editor.getByRole('button', { name: '儲存', exact: true });
  assert.equal(await save.isDisabled(), true);
  await editor.getByRole('textbox', { name: '說明', exact: true }).fill('到貨通知會寄到訂單 Email。');
  await save.click();
  await card(page).getByText('名稱與說明已儲存').waitFor();
  assert.deepEqual(state.writes, [{
    path: '/v1/group-campaigns/campaign', method: 'PATCH', body: { description: '到貨通知會寄到訂單 Email。' },
  }]);
  assert.deepEqual({ ...state.campaign, updated_at: before.updated_at }, { ...before, description: '到貨通知會寄到訂單 Email。' });
  await card(page).getByRole('link', { name: '查看前台' }).click();
  await page.getByText('到貨通知會寄到訂單 Email。', { exact: true }).waitFor();
  assert.deepEqual(state.quotes, []);
  await clean(page, state);
});

for (const width of [1280, 390]) test(`每月預訂建立不需要門檻與審核等待期 ${width}px`, async t => {
  const { page, state, visit } = await fixture(t, { width, timezone: 'America/New_York' });
  await visit();
  await page.getByRole('button', { name: '建立共同購買' }).click();
  const form = page.getByRole('form', { name: '建立共同購買' });
  await form.getByLabel(/^共同購買項目/).selectOption('product');
  assert.equal(await form.getByLabel(/成團所需|處理提醒日/).count(), 0);
  await form.getByLabel('付款截止（台灣）').fill('2030-01-03T12:00');
  await form.getByLabel('預計取貨開始（台灣）').fill('2030-01-03T13:00');
  await form.getByLabel('預計取貨結束（台灣）').fill('2030-01-03T14:00');
  assert.deepEqual(state.writes, []);
  if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/monthly-preorder-create-${width}.png`, fullPage: true });
  await form.getByRole('button', { name: '確認開放共同購買', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('.admin-create-block .admin-master-form'));
  const body = state.writes[0].body;
  assert.equal(state.writes[0].path, '/v1/group-campaigns');
  assert.equal(body.min_paid_quantity, undefined);
  assert.equal(body.confirmation_deadline, undefined);
  assert.equal(body.deadline, '2030-01-03T04:00:00.000Z');
  assert.equal(body.estimated_pickup_start, '2030-01-03T05:00:00.000Z');
  assert.equal(body.supply_cap, 30);
  assert.equal(body.per_user_cap, 5);
  await clean(page, state);
});

for (const width of [1280, 390]) test(`少量與零筆付款的預訂不需要成團審核 ${width}px`, async t => {
  const { page, state, visit } = await fixture(t, { width, campaign: { ...closedCampaign, paid_quantity: 0 } });
  await visit();
  await card(page).getByText('已截止', { exact: true }).waitFor();
  assert.equal(await card(page).getByRole('button', { name: /審核成團|確認成團|不成團|調整處理提醒日/ }).count(), 0);
  assert.equal(await card(page).getByText(/距離成團門檻|成團所需總數量/).count(), 0);
  assert.equal(await card(page).getByRole('progressbar').count(), 0);
  assert.equal(await card(page).getByRole('button', { name: '取消共同購買', exact: true }).isEnabled(), true);
  assert.deepEqual(state.writes, []);
  await clean(page, state);
});

for (const width of [1280, 390]) for (const ended of [false, true]) test(`買家預訂${ended ? '歷史已結束' : '開放中無門檻'} ${width}px`, async t => {
  const campaign = ended ? { ...closedCampaign, preorder_only: false, decision_status: 'failed_unmet' } : { ...openCampaign, paid_quantity: 0, min_paid_quantity: 999 };
  const { page, state, visit } = await fixture(t, { role: 'customer', width, path: '/shop?tab=groups', campaign });
  await visit();
  await page.getByRole('heading', { name: '每月共同購買', exact: true }).waitFor();
  const listing = page.locator('.editorial-card');
  await listing.getByText(ended ? '已結束' : '開放預訂', { exact: true }).waitFor();
  assert.equal(await page.locator('a[href^="/group-votes"]').count(), 0);
  await listing.getByRole('link').click();
  await page.getByRole('heading', { name: campaign.title, exact: true }).waitFor();
  assert.equal(await page.getByText(/份門檻|審核截止/).count(), 0);
  if (ended) {
    await page.getByText('此共同購買已停止接單', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '參加並前往付款' }).isDisabled(), true);
    assert.deepEqual(state.quotes, []);
  } else {
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === '參加並前往付款' && !button.disabled));
    assert.ok(state.quotes.length > 0);
  }
  if (process.env.BROWSER_ARTIFACT_DIR) await page.screenshot({ path: `${process.env.BROWSER_ARTIFACT_DIR}/monthly-preorder-${ended ? 'history' : 'open'}-${width}.png`, fullPage: true });
  assert.deepEqual(state.writes, []);
  await clean(page, state);
});

for (const intake_status of ['closed', 'settling']) test(`預訂截止 ${intake_status} 不建立新報價或付款`, async t => {
  const { page, state, visit } = await fixture(t, { role: 'customer', path: '/groups/campaign', campaign: { ...closedCampaign, intake_status } });
  await visit();
  await page.getByRole('heading', { name: closedCampaign.title }).waitFor();
  assert.equal(await page.getByRole('button', { name: '參加並前往付款' }).isDisabled(), true);
  assert.deepEqual(state.quotes, []);
  assert.deepEqual(state.writes, []);
  await clean(page, state);
});

test('頁面開著到截止當下即停用加入，不再送新報價', async t => {
  const { page, state, visit } = await fixture(t, { role: 'customer', path: '/groups/campaign', campaign: { ...openCampaign, deadline: '2030-01-02T02:00:02Z' } });
  await visit();
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === '參加並前往付款' && !button.disabled));
  const quoted = state.quotes.length;
  await page.clock.runFor(3000);
  assert.equal(await page.getByRole('button', { name: '參加並前往付款' }).isDisabled(), true);
  assert.equal(state.quotes.length, quoted);
  assert.deepEqual(state.writes, []);
  await clean(page, state);
});

for (const role of ['guest', 'customer']) test(`${role} 不可讀取管理資料`, async t => {
  const { page, state, visit } = await fixture(t, { role });
  await visit();
  await page.getByRole('heading', { name: '共同購買管理只向管理者開放。' }).waitFor();
  assert.equal(state.reads.some(path => path.includes('group-campaigns') || path.includes('vote-proposals')), false);
  assert.deepEqual(state.writes, []);
  await clean(page, state);
});

for (const status of [401, 403]) for (const cached of [false, true]) test(`管理清單 ${status} 清除既有資料與建立草稿 cached=${cached}`, async t => {
  const { page, state, visit } = await fixture(t);
  if (cached) {
    await visit();
    await page.getByRole('button', { name: '建立共同購買' }).click();
  }
  const denied = page.waitForResponse(response => response.request().method() === 'GET' && response.url() === `${base}${adminCampaignList}` && response.status() === status);
  state.failGet = { path: adminCampaignList, status };
  if (cached) await page.clock.runFor(30100); else await visit();
  await denied;
  await waitForAdminDenied(page, status);
  assert.equal(await page.getByText(closedCampaign.title, { exact: true }).count(), 0);
  assert.equal(await page.getByRole('form', { name: '建立共同購買' }).count(), 0);
  await page.clock.runFor(60100);
  assert.deepEqual(state.unauthenticatedAdminReads, []);
  assert.deepEqual(state.writes, []);
  await clean(page, state);
});

for (const status of [401, 403]) test(`管理員寫入 ${status} 隱藏既有資料與草稿`, async t => {
  const { page, state, visit } = await fixture(t);
  await visit();
  await card(page).getByRole('button', { name: '編輯名稱與說明' }).click();
  const form = card(page).getByRole('form', { name: '編輯共同購買名稱與說明' });
  await form.getByLabel('說明', { exact: true }).fill('待確認的修改');
  state.failWrite = { status, message: '權限失效' };
  if (status === 401) state.failGet = { path: adminCampaignList, status };
  await form.getByRole('button', { name: '儲存', exact: true }).click();
  await waitForAdminDenied(page, status);
  assert.equal(await page.getByText(closedCampaign.title, { exact: true }).count(), 0);
  await clean(page, state);
});

for (const intake_status of ['open', 'settling']) test(`預訂 ${intake_status} 管理員仍可取消，原因可留空`, async t => {
  const { page, state, visit } = await fixture(t, { campaign: { ...openCampaign, intake_status } });
  await visit();
  assert.deepEqual(state.writes, []);
  const request = page.waitForResponse(response => response.url().endsWith('/admin/cancel') && response.request().method() === 'POST');
  page.on('dialog', dialog => dialog.accept());
  await card(page).getByRole('button', { name: '取消共同購買', exact: true }).click();
  await request;
  assert.equal(state.writes[0].path, '/v1/group-campaigns/campaign/admin/cancel');
  assert.equal(state.writes[0].body.reason, '');
  await clean(page, state);
});

test('數量只改供應上限與限購，舊門檻不再擋住；失敗保留輸入', async t => {
  const { page, state, visit } = await fixture(t, { campaign: { ...openCampaign, core_locked_at: null, paid_quantity: 0 } });
  await visit();
  const quantities = card(page).getByRole('region', { name: '共同購買數量設定' });
  await quantities.getByRole('button', { name: '編輯數量設定' }).click();
  assert.equal(await quantities.getByLabel(/成團所需/).count(), 0);
  const limit = quantities.getByLabel(/本團供應上限/);
  await limit.fill('5');
  state.failWrite = { status: 503, message: '暫時無法儲存數量' };
  await quantities.getByRole('button', { name: '儲存數量設定' }).click();
  await quantities.getByRole('alert').filter({ hasText: '暫時無法儲存數量' }).waitFor();
  assert.equal(await limit.inputValue(), '5');
  assert.deepEqual(state.writes, [{ path: '/v1/group-campaigns/campaign', method: 'PATCH', body: { supply_cap: 5 } }]);
  await clean(page, state);
});

for (const width of [390, 1280]) test(`${width}px 停止與恢復公開不更動預訂及款項`, async t => {
  const { page, state, visit } = await fixture(t, { width });
  await visit();
  await card(page).getByRole('button', { name: '停止公開', exact: true }).waitFor();
  const before = { decision: state.campaign.decision_status, intake: state.campaign.intake_status, paid: state.campaign.paid_quantity, reserved: state.campaign.reserved_quantity };
  assert.ok(state.readUrls.some(value => new URL(value).pathname === '/v1/group-campaigns' && new URL(value).searchParams.get('include_hidden') === 'true'));
  await card(page).getByRole('button', { name: '停止公開', exact: true }).click();
  await card(page).getByRole('button', { name: '恢復公開', exact: true }).click();
  await card(page).getByRole('button', { name: '停止公開', exact: true }).waitFor();
  assert.deepEqual({ decision: state.campaign.decision_status, intake: state.campaign.intake_status, paid: state.campaign.paid_quantity, reserved: state.campaign.reserved_quantity }, before);
  assert.equal(state.writes.length, 2);
  assert.ok(state.writes.every(row => row.path === '/v1/group-campaigns/campaign/admin/visibility' && row.method === 'PATCH'));
  assert.notEqual(state.writes[0].body.expected_updated_at, state.writes[1].body.expected_updated_at);
  await clean(page, state);
});

test('歷史失敗團不復活，既有參與者仍可看未公開團安排', async t => {
  const { page, state, visit } = await fixture(t, { campaign: { ...closedCampaign, preorder_only: false, decision_status: 'failed_unmet', paid_quantity: 0 } });
  await visit();
  const quantities = card(page).getByRole('region', { name: '共同購買數量設定' });
  await quantities.getByRole('status').filter({ hasText: '共同購買已結束' }).waitFor();
  assert.equal(await quantities.getByRole('button', { name: '編輯數量設定' }).count(), 0);
  assert.equal(await card(page).getByRole('button', { name: '取消共同購買' }).count(), 0);
  assert.deepEqual(state.writes, []);
  await clean(page, state);
});

test('取貨調整使用版本更新，409保留草稿並核對後才可再送', async t => {
  const { page, state, visit } = await fixture(t, { campaign: { ...closedCampaign, final_pickup_at: '2030-01-08T04:00:00Z' } });
  await visit();
  await card(page).getByRole('button', { name: '調整取貨安排' }).click();
  const form = card(page).getByRole('form', { name: '調整共同購買取貨安排' });
  await form.getByLabel('取貨開始（台灣）').fill('2030-01-06T15:00');
  state.failWrite = { status: 409, message: '取貨安排已由另一位管理員更新' };
  state.changeOnConflict = { estimated_pickup_end: '2030-01-08T04:00:00Z', updated_at: '2030-01-02T02:01:00Z' };
  await form.getByRole('button', { name: '儲存取貨安排' }).click();
  const reload = form.getByRole('button', { name: '重新讀取取貨安排' });
  await reload.waitFor();
  assert.equal(state.writes[0].body.expected_updated_at, closedCampaign.updated_at);
  assert.equal(await form.getByLabel('取貨開始（台灣）').inputValue(), '2030-01-06T15:00');
  assert.equal(await form.getByRole('button', { name: '儲存取貨安排' }).isDisabled(), true);
  await reload.click();
  const recheck = form.getByLabel('已核對最新取貨安排，確認儲存目前輸入');
  await recheck.waitFor();
  assert.equal(await form.getByRole('button', { name: '儲存取貨安排' }).isDisabled(), true);
  await recheck.check();
  await form.getByRole('button', { name: '儲存取貨安排' }).click();
  await card(page).getByText('取貨安排已更新', { exact: true }).waitFor();
  assert.equal(state.writes.length, 2);
  assert.deepEqual(state.writes[1].body, { estimated_pickup_start: '2030-01-06T07:00:00.000Z', estimated_pickup_end: '2030-01-07T04:00:00.000Z', expected_updated_at: '2030-01-02T02:01:00Z' });
  assert.equal(state.campaign.final_pickup_at, null);
  assert.ok(state.writes.every(row => row.path === '/v1/group-campaigns/campaign' && row.method === 'PATCH'));
  await clean(page, state);
});

test('取貨安排服務失敗保留輸入，可明確重試', async t => {
  const { page, state, visit } = await fixture(t);
  await visit();
  await card(page).getByRole('button', { name: '調整取貨安排' }).click();
  const form = card(page).getByRole('form', { name: '調整共同購買取貨安排' });
  await form.getByLabel('取貨結束（台灣）').fill('2030-01-07T15:00');
  state.failWrite = { status: 503, message: '取貨安排暫時無法儲存' };
  await form.getByRole('button', { name: '儲存取貨安排' }).click();
  await form.getByRole('alert').getByText('取貨安排暫時無法儲存').waitFor();
  assert.equal(await form.getByLabel('取貨結束（台灣）').inputValue(), '2030-01-07T15:00');
  assert.equal(state.writes.length, 1);
  await form.getByRole('button', { name: '儲存取貨安排' }).click();
  await card(page).getByText('取貨安排已更新', { exact: true }).waitFor();
  assert.equal(state.writes.length, 2);
  await clean(page, state);
});

for (const width of [390, 1280]) test(`舊需求網址只顯示歷史，不再操作投票 ${width}px`, async t => {
  const { page, state, visit } = await fixture(t, { role: 'customer', width, path: '/group-votes/proposal' });
  await visit();
  await page.getByRole('heading', { name: '歷史白米需求', exact: true }).waitFor();
  await page.getByText('我的原預估數量', { exact: true }).waitFor();
  assert.equal(await page.getByText('評估門檻', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: /支持|投票|撤回|更新預估/ }).count(), 0);
  assert.equal(await page.locator('form, .group-vote-progress').count(), 0);
  assert.deepEqual(state.writes, []);
  await clean(page, state);
});
