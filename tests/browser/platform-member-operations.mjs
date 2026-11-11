import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import test from 'node:test';
const {chromium} = createRequire(import.meta.url)('playwright');
const target = new URL(process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4193');
if (!['127.0.0.1','localhost'].includes(target.hostname) || target.pathname !== '/') throw new Error('Use an isolated origin');
const base = target.origin;
async function fixture(t, width, {admin = false, closed = false} = {}) {
 const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE}); t.after(() => browser.close());
 const context = await browser.newContext({viewport: {width, height: 1000}, timezoneId: 'Asia/Taipei', serviceWorkers: 'block'});
 const state = {unknown: [], errors: [], writes: []};
 await context.route('**/*', async route => {
  const req=route.request(), url=new URL(req.url()), path=url.pathname;
  if (url.origin !== base) return route.abort();
  if (path === '/isolated-authorize') return route.fulfill({contentType:'text/html; charset=utf-8',body:'<h1>隔離 LINE 授權</h1>'});
  if (!path.startsWith('/v1/')) return route.continue();
  if (path === '/v1/auth/refresh') return route.fulfill(admin ? {json:{access_token:'isolated',user:{id:'admin',user_role:'admin',display_name:'管理員',email:'admin@example.test',membership_type:'member'}}} : {status:401,json:{detail:'未登入'}});
  if (req.method() === 'GET') {
   if (path === '/v1/platform') return route.fulfill({json:{orders_open:!closed,closed_message:'目前暫停接受新訂單'}});
   if (['/v1/products','/v1/meal-events/calendar','/v1/group-campaigns'].includes(path)) return route.fulfill({json:[]});
   if (path === '/v1/auth/line/status') return route.fulfill({json:{ready:true,linked:false}});
   if (path === '/v1/admin/platform') return route.fulfill({json:{orders_open:true,closed_message:'目前暫停接受新訂單',printer_brand:'',printer_model:'',printer_connection:'unknown',kitchen_sound:false,invoice_provider:'fanyu'}});
   if (path === '/v1/admin/member-register') return route.fulfill({json:{total:1,items:[{id:'member',source:'membership',member_number:'M001',legal_name:'隔離社員',email:'member@example.test',status:'active',share_count:5,share_capital_amount:5000,share_certificate_number:'SH001'}]}});
   if (path === '/v1/admin/membership-payments') return route.fulfill({json:{total:1,summary:{received:5000,refunded:0,net:5000},items:[{id:'receipt',source:'offline',member_number:'M001',name:'隔離社員',kind:'share_capital',amount:5000,received:5000,refunded:0,net:5000,status:'paid',paid_at:'2026-10-16T03:00:00Z',receipt_number:'MRC001',payment_method:'bank_transfer',reference:'12345'}]}});
   if (path === '/v1/admin/points-ledger') return route.fulfill({json:{total:1,items:[{user_id:'member',member_number:'M001',name:'隔離社員',email:'member@example.test',balance:12}]}});
   if (path === '/v1/admin/promotions') return route.fulfill({json:[]});
  }
  if (path === '/v1/auth/line/start' && req.method() === 'POST') {state.writes.push({path, body:req.postDataJSON()}); return route.fulfill({json:{authorization_url:`${base}/isolated-authorize`}});}
  if (path === '/v1/auth/register-existing-member' && req.method() === 'POST') {state.writes.push({path,body:req.postDataJSON()});return route.fulfill({status:201,json:{message:'驗證碼已寄出'}});}
  if (path === '/v1/admin/platform' && req.method() === 'PUT') {state.writes.push({path,body:req.postDataJSON()});return route.fulfill({json:req.postDataJSON()});}
  state.unknown.push(`${req.method()} ${path}`);return route.fulfill({status:404,json:{detail:'未定義的隔離請求'}});
 });
 const page=await context.newPage();page.setDefaultTimeout(6000);page.on('pageerror', e=>state.errors.push(e.message));return {page,state};
}
async function layout(page,state) {assert.deepEqual(state.unknown,[]);assert.deepEqual(state.errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1),true);}
for (const width of [1440,816,390]) {
 test(`${width}px 既有社員先填三項資料再選 Email，不要求電話`, async t=>{
  const {page,state}=await fixture(t,width);await page.goto(`${base}/register`);
  await page.getByRole('button',{name:'我是既有社員',exact:true}).click();
  assert.equal(await page.locator('input[type=password]').count(),0);
  await page.getByLabel('姓名',{exact:true}).fill('隔離社員');await page.getByLabel('社員編號',{exact:true}).fill('M001');await page.getByLabel('Email',{exact:true}).fill('member@example.test');
  await page.getByRole('button',{name:'繼續選擇註冊方式'}).click();await page.getByRole('button',{name:'使用 Email 註冊',exact:true}).click();
  assert.equal(await page.getByLabel('手機',{exact:true}).count(),0);
  await page.getByLabel('密碼',{exact:true}).fill('isolated-pass-123');await page.getByLabel('再次輸入密碼',{exact:true}).fill('isolated-pass-123');
  await page.getByRole('button',{name:'建立帳號',exact:true}).click();await page.getByRole('heading',{name:'請驗證你的 Email',exact:true}).waitFor();
  assert.equal(state.writes.length,1);assert.equal(state.writes[0].body.member_number,'M001');assert.equal('phone' in state.writes[0].body,false);
  await layout(page,state);
 });
 test(`${width}px LINE 社員入口攜帶三項資料，非社員略過社員表單`,async t=>{
  const {page,state}=await fixture(t,width);await page.goto(`${base}/register?intent=existing_member`);
  await page.getByLabel('姓名',{exact:true}).fill('隔離社員');await page.getByLabel('社員編號',{exact:true}).fill('M001');await page.getByLabel('Email',{exact:true}).fill('member@example.test');
  await page.getByRole('button',{name:'繼續選擇註冊方式'}).click();await page.getByRole('button',{name:'使用 LINE 註冊',exact:true}).click();
  await page.getByRole('checkbox',{name:'我已閱讀並同意 LINE 登入及社員資料的使用說明。',exact:true}).check();
  await page.getByRole('button',{name:'使用LINE登入',exact:true}).click();await page.getByRole('heading',{name:'隔離 LINE 授權'}).waitFor();
  assert.deepEqual(state.writes[0].body.member_claim,{member_number:'M001',legal_name:'隔離社員',email:'member@example.test'});assert.equal(state.writes[0].body.intent,'member');
  await page.goto(`${base}/register?intent=nonmember`);await page.getByRole('heading',{name:'選擇註冊方式',exact:true}).waitFor();assert.equal(await page.getByLabel('社員編號',{exact:true}).count(),0);await layout(page,state);
 });
 test(`${width}px 社員帳冊、收款與點數切換，手機不溢出`,async t=>{
  const {page,state}=await fixture(t,width,{admin:true});await page.goto(`${base}/admin/records`);
  await page.getByText('SH001',{exact:true}).waitFor();await page.locator('.reading-menu > summary').click();await page.getByRole('button',{name:'放大',exact:true}).click();await layout(page,state);
  await page.getByRole('button',{name:'入社費與股金',exact:true}).click();await page.getByRole('cell',{name:/MRC001/}).waitFor();await layout(page,state);
  await page.getByRole('button',{name:'累積點數',exact:true}).click();await page.getByRole('cell',{name:'12',exact:true}).waitFor();await layout(page,state);assert.deepEqual(state.writes,[]);
 });
 test(`${width}px 平台設定與便當促銷維持原風格及操作邊界`,async t=>{
  const {page,state}=await fixture(t,width,{admin:true});await page.goto(`${base}/admin/settings`);await page.getByText('每天晚上 20:00（台灣時間）',{exact:true}).waitFor();
  await page.getByLabel('接受新訂單',{exact:true}).uncheck();await page.getByRole('button',{name:'儲存平台設定',exact:true}).click();await page.getByText('設定已儲存',{exact:true}).waitFor();assert.equal(state.writes[0].body.orders_open,false);await layout(page,state);
  await page.goto(`${base}/admin/promotions`);await page.getByRole('button',{name:'新增促銷活動',exact:true}).click();await page.getByLabel('活動名稱',{exact:true}).fill('隔離優惠');await page.locator('.reading-menu > summary').click();await page.getByRole('button',{name:'放大',exact:true}).click();await layout(page,state);
 });
 test(`${width}px 暫停接單顯示狀態並保留原訂單入口`,async t=>{
  const {page,state}=await fixture(t,width,{closed:true});await page.goto(`${base}/shop`);await page.getByRole('heading',{name:'目前暫停接受新訂單',exact:true}).waitFor();await layout(page,state);
 });
}
