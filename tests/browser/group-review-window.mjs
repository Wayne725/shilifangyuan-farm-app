import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
const { chromium }=createRequire(import.meta.url)('playwright');
const target=new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197');
if (!['localhost','127.0.0.1'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password) throw new Error('Only isolated local tests');
const base=target.origin;
const now='2030-01-02T10:00:00+08:00';
const product={id:'product',name:'原始團購白米',category:'蔬果',description:'隔離商品',unit:'袋',member_price:90,nonmember_price:100,stock_quantity:30,is_active:true,can_ship:false,allowed_shipping_channels:[]};
const pending={id:'campaign',title:'截止審核隔離共同購買',description:'隔離測試',target_type:'product',target_id:'product',member_price:90,nonmember_price:100,min_paid_quantity:10,supply_cap:30,per_user_cap:5,paid_quantity:12,reserved_quantity:0,available_quantity:18,deadline:'2030-01-01T04:00:00Z',confirmation_deadline:'2030-01-04T04:00:00Z',estimated_pickup_start:'2030-01-06T04:00:00Z',estimated_pickup_end:'2030-01-07T04:00:00Z',decision_status:'pending_confirmation',intake_status:'closed',threshold_version:2,updated_at:'2030-01-01T05:00:00Z',review_overdue:false,can_confirm:true,can_edit_review_deadline:true,confirmation_blocked_reason:null,can_ship:false,allowed_shipping_channels:[],core_locked_at:'2030-01-01T00:00:00Z'};
const recruiting={...pending,decision_status:'recruiting',intake_status:'open',deadline:'2030-01-03T04:00:00Z',confirmation_deadline:'2030-01-06T04:00:00Z',estimated_pickup_start:'2030-01-07T04:00:00Z',estimated_pickup_end:'2030-01-08T04:00:00Z',can_confirm:false};
async function fixture(t,{role='admin',width=1280,path='/admin/groups',campaign=pending,proposal=false,proposalStatus='pending_review',timezone='Asia/Taipei'}={}) {
  const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width,height:950},timezoneId:timezone,serviceWorkers:'block',reducedMotion:'reduce'});
  const state={campaign:structuredClone(campaign),reads:[],readUrls:[],quotes:[],writes:[],unknown:[],errors:[],failGet:null,failWrite:null,changeOnConflict:null};
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),endpoint=url.pathname,method=request.method();
    if(url.origin!==base)return route.abort();
    if(!endpoint.startsWith('/v1/'))return method==='GET'?route.continue():route.abort();
    if(endpoint==='/v1/auth/refresh')return route.fulfill({status:role==='guest'?401:200,json:role==='guest'?{detail:'訪客'}:{access_token:'isolated',user:{id:'review-user',email:'review@example.test',display_name:'隔離管理者',user_role:role,membership_type:'nonmember'}}});
    if(method==='GET') {state.reads.push(endpoint);state.readUrls.push(url.href);if(state.failGet?.path===endpoint)return route.fulfill({status:state.failGet.status,json:{detail:'隔離權限失效'}});}
    if(endpoint==='/v1/platform')return route.fulfill({json:{orders_open:true,regular_orders_enabled:true,group_orders_enabled:true,shipping_enabled:false}});
    if(endpoint==='/v1/products')return route.fulfill({json:[product]});
    if(endpoint==='/v1/group-bundles')return route.fulfill({json:[]});
    if(endpoint==='/v1/vote-proposals')return route.fulfill({json:proposal?[{id:'proposal',target_type:'product',target_id:'product',target_name:'提案隔離白米',status:proposalStatus,threshold:10,vote_count:0,estimated_quantity:0}]:[]});
    if(endpoint==='/v1/group-campaigns'&&method==='GET')return route.fulfill({json:url.searchParams.get('include_hidden')==='true'||state.campaign.is_public!==false?[state.campaign]:[]});
    if(endpoint==='/v1/group-campaigns/campaign'&&method==='GET')return route.fulfill({json:state.campaign});
    if(endpoint==='/v1/pickup-locations')return route.fulfill({json:[{id:'store',name:'隔離取貨點',is_active:true}]});
    if(endpoint==='/v1/group-campaigns/campaign/quote'){state.quotes.push(request.postDataJSON());return route.fulfill({json:{confirmation_fingerprint:'a'.repeat(64),quantity:1,unit_price:100,product_subtotal:100,shipping_fee:0,amount_total:100,membership_type:'nonmember'}});}
    if((endpoint.startsWith('/v1/group-campaigns')||endpoint.endsWith('/admin/open-direct')||endpoint.endsWith('/admin/approve'))&&['POST','PATCH'].includes(method)) {
      const body=request.postDataJSON();state.writes.push({path:endpoint,method,body});
      if(state.failWrite){const failure=state.failWrite;if(failure.status!==401)state.failWrite=null;if(state.changeOnConflict)Object.assign(state.campaign,state.changeOnConflict);return route.fulfill({status:failure.status,json:{detail:failure.message}});}
      if(endpoint.endsWith('/admin/visibility')){assert.equal(body.expected_updated_at,state.campaign.updated_at);state.campaign.is_public=body.is_public;state.campaign.updated_at=new Date(Date.parse(state.campaign.updated_at)+1000).toISOString();}
      if(endpoint.endsWith('/admin/confirm')){assert.equal(body.expected_threshold_version,state.campaign.threshold_version);if(state.campaign.paid_quantity<state.campaign.min_paid_quantity){assert.equal(body.allow_below_threshold,true);}Object.assign(state.campaign,{decision_status:'confirmed',can_confirm:false,can_edit_review_deadline:false,final_pickup_at:body.final_pickup_at,confirmed_paid_quantity:state.campaign.paid_quantity,confirmed_min_paid_quantity:state.campaign.min_paid_quantity,confirmed_threshold_version:state.campaign.threshold_version,confirmation_exception_reason:body.reason||null});}
      if(endpoint.endsWith('/admin/review-deadline')){assert.equal(body.expected_confirmation_deadline,state.campaign.confirmation_deadline);assert.equal(body.expected_updated_at,state.campaign.updated_at);Object.assign(state.campaign,body,{review_overdue:false});}
      return route.fulfill({json:state.campaign});
    }
    state.unknown.push(`${method} ${endpoint}`);return route.fulfill({status:404,json:{detail:'未定義的隔離API'}});
  });
  const page=await context.newPage();page.setDefaultTimeout(9000);page.on('pageerror',error=>state.errors.push(error.message));await page.clock.install({time:new Date(now)});
  const visit=async()=>{await page.goto(`${base}${path}`);if(path==='/admin/groups'&&role==='admin'&&!proposal&&!state.failGet)await page.getByRole('tab',{name:'正式共同購買',exact:true}).click();};
  return {page,state,visit};
}
const card=page=>page.getByRole('article',{name:'共同購買 截止審核隔離共同購買',exact:true});
async function clean(page,state){assert.deepEqual(state.unknown,[]);assert.deepEqual(state.errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);}
async function waitForAdminDenied(page,status){if(status===401)await page.getByRole('heading',{name:'共同購買管理只向管理者開放。',exact:true}).waitFor();else await page.getByText('共同購買管理權限已失效').waitFor();}
async function openReview(page){await card(page).getByRole('button',{name:'審核成團',exact:true}).click();return card(page).getByRole('form',{name:'確認共同購買成團'});}

test('建立共同購買的處理提醒日預設付款截止+72h，台灣日期不受瀏覽器時區影響',async t=>{
  const {page,state,visit}=await fixture(t,{timezone:'America/New_York'});await visit();await page.getByRole('button',{name:'直接建立正式共同購買'}).click();
  const form=page.locator('.admin-create-block .admin-master-form').first();await form.getByLabel(/^共同購買項目/).selectOption('product');await form.locator('details.campaign-reminder-settings > summary').click();
  assert.equal(await form.getByLabel('付款截止（台灣）').inputValue(),'2030-01-09T10:00');assert.equal(await form.getByLabel('管理員處理提醒日（台灣）').inputValue(),'2030-01-12T10:00');
  await form.getByLabel('付款截止（台灣）').fill('2030-01-08T12:30');assert.equal(await form.getByLabel('管理員處理提醒日（台灣）').inputValue(),'2030-01-11T12:30');
  await form.getByLabel('管理員處理提醒日（台灣）').fill('2030-01-10T15:00');await form.getByLabel('付款截止（台灣）').fill('2030-01-07T12:30');assert.equal(await form.getByLabel('管理員處理提醒日（台灣）').inputValue(),'2030-01-10T15:00');assert.deepEqual(state.writes,[]);
  await form.getByRole('button',{name:'確認開放共同購買',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('.admin-create-block .admin-master-form'));
  assert.equal(state.writes[0].body.confirmation_deadline,'2030-01-10T07:00:00.000Z');assert.equal(state.writes[0].body.deadline,'2030-01-07T04:30:00.000Z');await clean(page,state);
});
test('提案直接開團也必須送處理提醒日，無效日期不能提交',async t=>{
  const {page,state,visit}=await fixture(t,{proposal:true});await visit();await page.getByRole('button',{name:'直接確認並開團'}).click();
  const form=page.locator('.admin-create-block .admin-master-form');await form.locator('details.campaign-reminder-settings > summary').click();await form.getByLabel('管理員處理提醒日（台灣）').fill('2030-01-09T10:00');assert.equal(await form.getByRole('button',{name:'確認開放共同購買'}).isDisabled(),true);assert.deepEqual(state.writes,[]);
  await form.getByLabel('管理員處理提醒日（台灣）').fill('2030-01-12T10:00');await form.getByRole('button',{name:'確認開放共同購買'}).click();await page.waitForFunction(()=>!document.querySelector('.admin-create-block .admin-master-form'));
  assert.equal(state.writes[0].path,'/v1/vote-proposals/proposal/admin/open-direct');assert.ok(state.writes[0].body.confirmation_deadline);await clean(page,state);
});
for(const decision of ['recruiting','confirmed'])test(`${decision} 截止前達標可繼續集單，舊已確認活動相容`,async t=>{
  const {page,state,visit}=await fixture(t,{role:'customer',path:'/groups/campaign',campaign:{...recruiting,decision_status:decision}});await visit();
  const button=page.getByRole('button',{name:'參加並前往付款',exact:true});await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='參加並前往付款'&&!b.disabled));assert.equal(await button.isEnabled(),true);assert.deepEqual(state.writes,[]);await clean(page,state);
});
test('頁面開著到截止當下即停用新加入，不再送新報價',async t=>{
  const {page,state,visit}=await fixture(t,{role:'customer',path:'/groups/campaign',campaign:{...recruiting,deadline:'2030-01-02T02:00:02Z'}});await visit();await page.getByRole('button',{name:'參加並前往付款'}).waitFor();await page.clock.runFor(2100);await page.getByText('共同購買已截止',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'參加並前往付款'}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'增加數量'}).isDisabled(),true);assert.equal(await page.locator('.offer-total dd').innerText(),'—');assert.deepEqual(state.writes,[]);await clean(page,state);
});
for(const status of ['pending_confirmation','settling'])test(`${status} 截止後顧客不可新報價與付款`,async t=>{
  const {page,state,visit}=await fixture(t,{role:'customer',path:'/groups/campaign',campaign:status==='settling'?{...pending,decision_status:'recruiting',intake_status:'settling'}:pending});await visit();await page.getByRole('button',{name:'參加並前往付款'}).waitFor();assert.equal(await page.getByRole('button',{name:'參加並前往付款'}).isDisabled(),true);assert.deepEqual(state.quotes,[]);assert.deepEqual(state.writes,[]);await clean(page,state);
});
test('達標正常確認送出看到的門檻版本與最終取貨時間',async t=>{
  const {page,state,visit}=await fixture(t);await visit();const form=await openReview(page);assert.deepEqual(state.writes,[]);await form.getByRole('button',{name:'確認成團',exact:true}).click();await page.getByText('共同購買已確認成團',{exact:true}).waitFor();assert.equal(state.writes.length,1);assert.equal(state.writes[0].body.expected_threshold_version,2);assert.equal(state.writes[0].body.allow_below_threshold,false);assert.equal(state.campaign.min_paid_quantity,10);await clean(page,state);
});
for(const width of [1280,390])test(`未達標例外仍需勾選，原因可略過 ${width}px`,async t=>{
  const {page,state,visit}=await fixture(t,{width,campaign:{...pending,paid_quantity:6}});await visit();const form=await openReview(page);const submit=form.getByRole('button',{name:'例外確認成團'});assert.equal(await submit.isDisabled(),true);await form.getByLabel('未達原門檻，仍確認成團').check();assert.equal(await submit.isEnabled(),true);if(width===1280)await form.getByLabel('例外成團原因').fill('供應方同意六份出貨');assert.equal(await submit.isEnabled(),true);assert.deepEqual(state.writes,[]);
  if(process.env.BROWSER_ARTIFACT_DIR){await page.evaluate(()=>window.scrollTo({top:0,behavior:"instant"}));await page.screenshot({path:`${process.env.BROWSER_ARTIFACT_DIR}/group-review-${width}.png`,fullPage:true});}
  await submit.click();await page.getByText('共同購買已確認成團',{exact:true}).waitFor();assert.equal(state.writes[0].body.allow_below_threshold,true);assert.equal(state.writes[0].body.reason,width===1280?'供應方同意六份出貨':'');assert.equal(state.campaign.min_paid_quantity,10);await clean(page,state);
});
for(const [label,patch] of [['0付款',{paid_quantity:0,can_confirm:false}],['結算未知',{decision_status:'recruiting',intake_status:'settling',can_confirm:false}],['截止前已達標',{...recruiting}]])test(`${label} 不允許確認成團`,async t=>{
  const {page,state,visit}=await fixture(t,{campaign:{...pending,...patch}});await visit();await card(page).waitFor();assert.equal(await card(page).getByRole('button',{name:'審核成團'}).isDisabled(),true);assert.equal(await page.getByRole('form',{name:'確認共同購買成團'}).count(),0);assert.deepEqual(state.writes,[]);await clean(page,state);
});
test('成團版本409保留取貨與原因，重讀後須確認新數量再例外成團',async t=>{
  const {page,state,visit}=await fixture(t);await visit();let form=await openReview(page);await form.getByLabel('最終取貨時間（台灣）').fill('2030-01-07T15:00');state.failWrite={status:409,message:'數量資料已更新'};state.changeOnConflict={paid_quantity:8,threshold_version:3};await form.getByRole('button',{name:'確認成團',exact:true}).click();await form.getByRole('button',{name:'重新核對成團資料'}).waitFor();assert.equal(await form.getByLabel('最終取貨時間（台灣）').inputValue(),'2030-01-07T15:00');assert.equal(state.writes.length,1);
  await form.getByRole('button',{name:'重新核對成團資料'}).click();await form.getByLabel('已核對最新付款數量與原門檻').waitFor();const submit=form.getByRole('button',{name:'例外確認成團'});assert.equal(await submit.isDisabled(),true);await form.getByLabel('已核對最新付款數量與原門檻').check();await form.getByLabel('未達原門檻，仍確認成團').check();await form.getByLabel('例外成團原因').fill('核對最新八份仍出貨');await submit.click();await page.getByText('共同購買已確認成團',{exact:true}).waitFor();assert.equal(state.writes[1].body.expected_threshold_version,3);await clean(page,state);
});
test('過期僅提示；付款截止後可用專用端點調整審核與取貨日期',async t=>{
  const {page,state,visit}=await fixture(t,{campaign:{...pending,review_overdue:true,confirmation_deadline:'2030-01-02T01:00:00Z'}});await visit();await page.getByText('已到處理提醒日，仍待管理員處理').waitFor();assert.deepEqual(state.writes,[]);await card(page).getByRole('button',{name:'調整處理提醒日'}).click();const form=page.getByRole('form',{name:'調整共同購買處理提醒日'});await form.getByLabel('管理員處理提醒日（台灣）').fill('2030-01-08T12:00');await form.getByLabel('預計取貨開始（台灣）').fill('2030-01-09T12:00');await form.getByLabel('預計取貨結束（台灣）').fill('2030-01-10T12:00');await form.getByLabel('調整原因').fill('確認供應時程');await form.getByRole('button',{name:'儲存處理提醒日'}).click();await page.getByText('處理提醒日已更新',{exact:true}).waitFor();assert.equal(state.writes.length,1);assert.equal(state.writes[0].path,'/v1/group-campaigns/campaign/admin/review-deadline');assert.equal(state.writes[0].body.expected_confirmation_deadline,'2030-01-02T01:00:00Z');assert.equal(state.writes[0].body.estimated_pickup_start,'2030-01-09T04:00:00.000Z');await clean(page,state);
});
test('處理提醒日409保留草稿，重讀與核對後才可儲存',async t=>{
  const {page,state,visit}=await fixture(t);await visit();await card(page).getByRole('button',{name:'調整處理提醒日'}).click();const form=page.getByRole('form',{name:'調整共同購買處理提醒日'});await form.getByLabel('管理員處理提醒日（台灣）').fill('2030-01-05T15:00');await form.getByLabel('調整原因').fill('延長核對時間');state.failWrite={status:409,message:'期限已更新'};state.changeOnConflict={confirmation_deadline:'2030-01-05T04:00:00Z',updated_at:'2030-01-02T02:00:01Z'};await form.getByRole('button',{name:'儲存處理提醒日'}).click();await form.getByRole('button',{name:'重新讀取處理提醒日'}).waitFor();assert.equal(await form.getByLabel('管理員處理提醒日（台灣）').inputValue(),'2030-01-05T15:00');assert.equal(await form.getByLabel('調整原因').inputValue(),'延長核對時間');await form.getByRole('button',{name:'重新讀取處理提醒日'}).click();await form.getByLabel('已核對最新提醒日，確認儲存目前輸入').waitFor();assert.equal(await form.getByRole('button',{name:'儲存處理提醒日'}).isDisabled(),true);await form.getByLabel('已核對最新提醒日，確認儲存目前輸入').check();await form.getByRole('button',{name:'儲存處理提醒日'}).click();await page.getByText('處理提醒日已更新',{exact:true}).waitFor();assert.equal(state.writes[1].body.expected_confirmation_deadline,'2030-01-05T04:00:00Z');await clean(page,state);
});
for(const status of [401,403])for(const cached of [false,true])test(`管理端GET ${status} ${cached?'快取後失權':'初次失權'}整區隱藏並停止重試`,async t=>{
  const {page,state,visit}=await fixture(t);if(!cached)state.failGet={path:'/v1/group-campaigns',status};await visit();if(cached){await card(page).waitFor();await openReview(page);state.failGet={path:'/v1/group-campaigns',status};await page.clock.runFor(30100);}await waitForAdminDenied(page,status);assert.equal(await page.getByText('截止審核隔離共同購買',{exact:true}).count(),0);assert.equal(await page.getByRole('form',{name:'確認共同購買成團'}).count(),0);const reads=state.reads.filter(path=>path!=='/v1/platform').length;await page.clock.runFor(60100);assert.equal(state.reads.filter(path=>path!=='/v1/platform').length,reads);assert.deepEqual(state.writes,[]);await clean(page,state);
});
for(const status of [401,403])test(`管理端送審${status}隱藏全部既有資料及草稿`,async t=>{
  const {page,state,visit}=await fixture(t);await visit();const form=await openReview(page);state.failWrite={status,message:'權限失效'};if(status===401)state.failGet={path:'/v1/group-campaigns',status};await form.getByRole('button',{name:'確認成團',exact:true}).click();await waitForAdminDenied(page,status);assert.equal(await page.getByText('截止審核隔離共同購買',{exact:true}).count(),0);await clean(page,state);
});
for(const role of ['guest','customer'])test(`${role}不可讀取管理資料`,async t=>{const {page,state,visit}=await fixture(t,{role});await visit();await page.getByRole('heading',{name:'共同購買管理只向管理者開放。'}).waitFor();assert.equal(state.reads.some(path=>path.includes('group-campaigns')||path.includes('vote-proposals')),false);assert.deepEqual(state.writes,[]);await clean(page,state);});

for(const intake of ['open','settling'])test(`${intake} 管理員可略過原因取消，按下取消才送出`,async t=>{
  const {page,state,visit}=await fixture(t,{campaign:{...recruiting,intake_status:intake}});await visit();const cancel=card(page).getByRole('button',{name:'取消共同購買',exact:true});assert.equal(await cancel.isEnabled(),true);assert.deepEqual(state.writes,[]);if(intake==='settling')await card(page).getByLabel('取消／拒絕原因').fill('供應方取消交貨');await cancel.click();await page.getByText('審核結果已儲存').waitFor();assert.equal(state.writes[0].path,'/v1/group-campaigns/campaign/admin/cancel');assert.equal(state.writes[0].body.reason,intake==='settling'?'供應方取消交貨':'');await clean(page,state);
});
test('只調整取貨視窗也帶共同版本，409不覆蓋另一位管理員的新日期',async t=>{
  const {page,state,visit}=await fixture(t);await visit();await card(page).getByRole('button',{name:'調整處理提醒日'}).click();const form=page.getByRole('form',{name:'調整共同購買處理提醒日'});await form.getByLabel('預計取貨開始（台灣）').fill('2030-01-06T15:00');await form.getByLabel('調整原因').fill('調整出貨');state.failWrite={status:409,message:'取貨視窗版本已更新'};state.changeOnConflict={estimated_pickup_end:'2030-01-08T04:00:00Z',updated_at:'2030-01-02T02:01:00Z'};await form.getByRole('button',{name:'儲存處理提醒日'}).click();await form.getByRole('button',{name:'重新讀取處理提醒日'}).waitFor();assert.equal(state.writes[0].body.expected_updated_at,'2030-01-01T05:00:00Z');assert.equal(await form.getByLabel('預計取貨開始（台灣）').inputValue(),'2030-01-06T15:00');assert.equal(await form.getByRole('button',{name:'儲存處理提醒日'}).isDisabled(),true);assert.equal(state.writes.length,1);await clean(page,state);
});
test('審核服務失敗保留例外原因與取貨時間，可明確重試',async t=>{
  const {page,state,visit}=await fixture(t,{campaign:{...pending,paid_quantity:6}});await visit();const form=await openReview(page);await form.getByLabel('未達原門檻，仍確認成團').check();await form.getByLabel('例外成團原因').fill('六份出貨');state.failWrite={status:503,message:'隔離暫時無法儲存'};await form.getByRole('button',{name:'例外確認成團'}).click();await form.getByText('隔離暫時無法儲存',{exact:true}).waitFor();assert.equal(await form.getByLabel('例外成團原因').inputValue(),'六份出貨');assert.equal(state.writes.length,1);assert.equal(state.campaign.decision_status,'pending_confirmation');await form.getByRole('button',{name:'例外確認成團'}).click();await page.getByText('共同購買已確認成團',{exact:true}).waitFor();assert.equal(state.writes.length,2);await clean(page,state);
});
for(const width of [1280,390])test(`顧客截止後審核狀態 ${width}px`,async t=>{
  const {page,state,visit}=await fixture(t,{role:'customer',width,path:'/groups/campaign'});await visit();await page.getByText('已截止，等待管理員審核',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'參加並前往付款'}).isDisabled(),true);assert.equal(await page.locator('.offer-total dd').innerText(),'—');if(process.env.BROWSER_ARTIFACT_DIR){await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:`${process.env.BROWSER_ARTIFACT_DIR}/group-customer-closed-${width}.png`,fullPage:true});}assert.deepEqual(state.writes,[]);await clean(page,state);
});

test('提案投票與正式共同購買的時間輸入都固定台灣時區',async t=>{
  const {page,state,visit}=await fixture(t,{proposal:true,timezone:'America/New_York'});await visit();await page.getByLabel('投票截止（台灣）').fill('2030-01-09T15:00');const response=page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/admin/approve'));await page.getByRole('button',{name:'通過並開放投票'}).click();await response;assert.equal(state.writes[0].body.deadline,'2030-01-09T07:00:00.000Z');await clean(page,state);
});

for(const path of ['/admin/groups','/group-votes'])test(`${path} 歷史未達投票門檻顯示中文並保留原資料`,async t=>{
  const {page,state,visit}=await fixture(t,{path,proposal:true,proposalStatus:'ended_unmet'});await visit();await page.getByText('未達投票門檻',{exact:true}).waitFor();assert.equal(await page.getByText('ended_unmet',{exact:true}).count(),0);assert.deepEqual(state.writes,[]);await clean(page,state);
});
test('歷史失敗共同購買即使有鎖定日期與零付款，仍顯示已結束原因',async t=>{
  const {page,state,visit}=await fixture(t,{campaign:{...pending,decision_status:'failed_unmet',paid_quantity:0,can_confirm:false,can_edit_review_deadline:false}});await visit();await page.getByText('此團已結束，無法修改數量設定。',{exact:true}).waitFor();assert.equal(await page.getByText('已有付款紀錄，限購與成團數量已鎖定。',{exact:true}).count(),0);assert.deepEqual(state.writes,[]);await clean(page,state);
});

for(const width of [390,1280])test(`${width}px共同購買管理含未公開資料，停止與恢復不更動成團金流`,async t=>{
 const{page,state,visit}=await fixture(t,{width});await visit();const saved={decision:state.campaign.decision_status,intake:state.campaign.intake_status,paid:state.campaign.paid_quantity,reserved:state.campaign.reserved_quantity};
 assert.ok(state.readUrls.some(value=>new URL(value).pathname==='/v1/group-campaigns'&&new URL(value).searchParams.get('include_hidden')==='true'));
 await card(page).getByRole('button',{name:'停止公開',exact:true}).click();await card(page).getByRole('button',{name:'恢復公開',exact:true}).waitFor();
 await card(page).getByRole('button',{name:'恢復公開',exact:true}).click();await card(page).getByRole('button',{name:'停止公開',exact:true}).waitFor();
 assert.deepEqual({decision:state.campaign.decision_status,intake:state.campaign.intake_status,paid:state.campaign.paid_quantity,reserved:state.campaign.reserved_quantity},saved);
 assert.equal(state.writes.length,2);assert.ok(state.writes.every(row=>row.path==='/v1/group-campaigns/campaign/admin/visibility'&&row.method==='PATCH'));assert.notEqual(state.writes[0].body.expected_updated_at,state.writes[1].body.expected_updated_at);await clean(page,state);
});
test('既有參與者可查看未公開團安排，但無法新增參加',async t=>{
 const{page,state,visit}=await fixture(t,{role:'customer',path:'/groups/campaign',campaign:{...recruiting,is_public:false}});await visit();await page.getByText('此共同購買已停止公開',{exact:true}).first().waitFor();
 assert.equal(await page.getByRole('button',{name:'參加並前往付款',exact:true}).isDisabled(),true);assert.deepEqual(state.quotes,[]);assert.deepEqual(state.writes,[]);await clean(page,state);
});
