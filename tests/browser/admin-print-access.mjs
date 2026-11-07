import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {extname,resolve} from 'node:path';
import test from 'node:test';
const {chromium,webkit}=createRequire(import.meta.url)('playwright');
const engine=process.env.BROWSER_ENGINE==='webkit'?webkit:chromium;
const base='http://127.0.0.1:4251';
const root=resolve(process.env.BROWSER_TEST_DIST||resolve(import.meta.dirname,'../../web/dist'));
const types={'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.png':'image/png','.webp':'image/webp'};
const privateProduct='PRIVATE-SUMMARY-PRODUCT';
const summary='/v1/admin/order-summary',print='/v1/admin/printers/orders/isolated-order';
const order={id:'isolated-order',order_number:'PRIVATE-ORDER-001',sales_channel:'meal_preorder',fulfillment_method:'event_pickup',payment_method:'online',payment_status:'paid',fulfillment_status:'pending',invoice_status:'issued',paid_at:'2030-01-02T01:00:00Z',cancelled_at:null,created_at:'2030-01-02T01:00:00Z',amount_total:100,tax_amount:5,revision:1,available_actions:[],items:[{id:'item',product_name:'隔離主餐',quantity:1,unit_label:'份',unit_price:100,subtotal:100,tax_type:'taxable'}]};
async function fixture(t,kind,width=390){
 const browser=await engine.launch({headless:true,...(engine===chromium?{executablePath:process.env.BROWSER_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width,height:1000},timezoneId:'Asia/Taipei',serviceWorkers:'block'});
 const state={reads:[],writes:[],errors:[],unknown:[],external:[],code:200,codes:[],hold:false,pending:[],heldDone:[],refreshes:0,batch:null,job:null};
 let markHeld;const held=new Promise(resolve=>{markHeld=resolve;});
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method();
  if(['fonts.googleapis.com','fonts.gstatic.com'].includes(url.hostname))return route.abort();
  if(url.origin!==base){state.external.push(url.origin);return route.abort();}
  if(!path.startsWith('/v1/')){const file=extname(path)?resolve(root,`.${path}`):resolve(root,'index.html');assert.ok(file.startsWith(root+'/'));return route.fulfill({contentType:types[extname(file)]||'application/octet-stream',body:await readFile(file)});}
  if(path==='/v1/auth/refresh'){state.refreshes+=1;return route.fulfill({json:{access_token:`isolated-${state.refreshes}`,user:{id:'isolated-admin',display_name:'隔離管理員',email:'admin@example.test',user_role:'admin',membership_type:'nonmember'}}});}
  if(path==='/v1/platform')return route.fulfill({json:{orders_open:true}});
  if(path==='/v1/members/me')return route.fulfill({json:{membership_type:'nonmember',membership:null}});
  if(path==='/v1/notifications')return route.fulfill({json:[]});
  if(path==='/v1/admin/order-search')return route.fulfill({json:{items:[order],total:1,offset:0,limit:12}});
  if(path===summary){state.reads.push(path);return route.fulfill({json:{...Object.fromEntries(url.searchParams),summary:{order_count:1,quantity:1,product_count:1,paid_order_count:1,pending_order_count:0,inactive_order_count:0},categories:[{category:'便當',order_count:1,quantity:1}],channels:[{sales_channel:'meal_preorder',order_count:1,quantity:1}],category_options:['便當'],products:{items:[{key:'private-product',name:privateProduct,category:'便當',sales_channel:'meal_preorder',unit:'份',quantity:1,order_count:1}],total:1,limit:25,offset:0}}});}
  if(path===summary+'/print-preview'||path===print+'/preview'){
   state.reads.push(path);const value=kind==='summary'?{fingerprint:'a'.repeat(64),filters:{...Object.fromEntries(url.searchParams),status:'paid'},summary:{order_count:1,quantity:1,product_count:1},pages:['PRIVATE-SUMMARY-PREVIEW'],page_count:1,mode:'live'}:{preview:'PRIVATE-ORDER-CONTACT',preview_token:'a'.repeat(64),kind:'order',revision:1,mode:'live',can_print:true,blocked_reason:null,duplicate_risk:false,jobs:[]};return route.fulfill({json:value});
  }
  if((path===summary+'/print-jobs'||path===print+'/print')&&method==='POST'){
   const body=request.postDataJSON();state.writes.push({path,body});
   state.batch={request_id:body.request_id,fingerprint:'a'.repeat(64),mode:'live',created_at:'2030-01-02T01:00:00Z',filters:body.filters,summary:{order_count:1,quantity:1,product_count:1},pages:[{id:'private-page',page:1,status:'pending',last_error:'PRIVATE-BATCH-DETAIL'}]};
   state.job={id:'isolated-job',status:'pending',mode:'live',kind:'order',created_at:'2030-01-02T01:00:00Z'};
   return route.fulfill({status:201,json:kind==='summary'?state.batch:state.job});
  }
  if((path.startsWith(summary+'/print-jobs/')||path.startsWith(print+'/print-requests/'))&&method==='GET'){
   state.reads.push(path);const code=state.codes.shift()??state.code;
   let finished;if(state.hold){state.hold=false;state.heldRequest=request;state.heldDone.push(new Promise(resolve=>{finished=resolve;}));await new Promise(resolve=>{state.pending.push(resolve);markHeld();});}
   try{return await route.fulfill(code===200?{json:kind==='summary'?state.batch:state.job}:{status:code,json:{detail:code===403?'管理權限已撤銷':code===401?'登入續期':'隔離暫時無法查詢'}});}finally{finished?.();}
  }
  state.unknown.push(`${method} ${path}`);return route.fulfill({status:404,json:{detail:'Undefined isolated print route'}});
 });
 const page=await context.newPage();page.setDefaultTimeout(6000);page.on('pageerror',error=>state.errors.push(error.message));await page.clock.install({time:new Date('2030-01-02T10:00:00+08:00')});
 t.after(async()=>{state.pending.splice(0).forEach(resolve=>resolve());await Promise.all(state.heldDone);if(process.env.BROWSER_ARTIFACT_DIR){const dir=process.env.BROWSER_ARTIFACT_DIR;await mkdir(dir,{recursive:true});const name=createHash('sha256').update(t.name).digest('hex').slice(0,12);await writeFile(`${dir}/${name}.txt`,await page.locator('body').innerText());await page.screenshot({path:`${dir}/${name}.png`,fullPage:true});}await browser.close();assert.deepEqual(state.errors,[]);assert.deepEqual(state.unknown,[]);assert.deepEqual(state.external,[]);});
 await page.goto(`${base}/admin?section=${kind==='summary'?'summary':'orders'}`);
 await page.getByRole('button',{name:kind==='summary'?'列印彙總單':'再次出單',exact:true}).waitFor();
 await page.getByRole('button',{name:kind==='summary'?'列印彙總單':'再次出單',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:kind==='summary'?'彙總單列印':'訂單出單／補印',exact:true});await dialog.locator('pre').waitFor();
 if(kind==='manual')await dialog.getByLabel('出單原因',{exact:true}).fill('隔離核對後出單');
 await dialog.getByRole('button',{name:kind==='summary'?'確認列印 1 張':'確認送至出單機',exact:true}).click();
 await dialog.getByText(kind==='summary'?'彙總單列印紀錄 · 1 筆訂單 · 1 張':'已建立出單工作：等待發送',{exact:true}).waitFor();
 assert.equal(state.writes.length,1);assert.match(state.writes[0].body.request_id,/^[0-9a-f-]{36}$/);
 const privateWidget=page.locator(kind==='summary'?'.order-summary-print':'.admin-order-print');
 async function hideCheck(){assert.equal(await privateWidget.count(),0,'確認撤權後須移除私人列印資料');assert.equal(await dialog.count(),0);if(kind==='summary')assert.equal(await page.getByText(privateProduct,{exact:true}).count(),0,'同一workspace報表也須隱藏');}
 async function lookupResponse(code,action){const response=page.waitForResponse(r=>r.request().method()==='GET'&&r.url().includes(kind==='summary'?'/print-jobs/':'/print-requests/')&&r.status()===code);await action();await(await response).finished();await page.clock.runFor(100);}
 async function deniedResponse(action){await lookupResponse(403,action);await page.getByRole('button',{name:'重新驗證管理權限',exact:true}).waitFor();}
 async function storedAttempt(){return page.evaluate(key=>sessionStorage.getItem(key),kind==='summary'?'order-summary-print:isolated-admin':'admin-order-print:isolated-admin:isolated-order');}
 return{page,state,dialog,privateWidget,held,hideCheck,deniedResponse,lookupResponse,storedAttempt};
}

test('彙總列印confirmed403後隱藏批次及同workspace報表並停止輪詢',async t=>{
 const f=await fixture(t,'summary');await f.dialog.getByText('PRIVATE-BATCH-DETAIL',{exact:true}).waitFor();f.state.code=403;
 await f.deniedResponse(()=>f.page.clock.runFor(4100));await f.hideCheck();const before=f.state.reads.length;await f.page.clock.runFor(8100);assert.equal(f.state.reads.length,before);assert.equal(f.state.writes.length,1);
});

test('單筆列印confirmed403後隱藏既有工作與停止輪詢',async t=>{
 const f=await fixture(t,'manual');f.state.code=403;
 await f.deniedResponse(()=>f.page.clock.runFor(4100));await f.hideCheck();const before=f.state.reads.length;await f.page.clock.runFor(8100);assert.equal(f.state.reads.length,before);assert.equal(f.state.writes.length,1);
});

for (const kind of ['summary','manual']) {
 test(`${kind}撤權後晚到200不得復活，重新驗證保留原送印識別`,async t=>{
  const f=await fixture(t,kind,1440),original=await f.storedAttempt();
  f.state.hold=true;await f.page.clock.runFor(4100);await f.held;
  assert.equal(f.state.heldRequest.method(),'GET');f.state.code=403;
  await f.deniedResponse(()=>f.dialog.getByRole('button',{name:'查詢送印結果',exact:true}).click());await f.hideCheck();
  f.state.pending.splice(0).forEach(release=>release());await Promise.all(f.state.heldDone);
  const late=await f.state.heldRequest.response();assert.equal(late?.status(),200);await late.finished();
  await f.page.clock.runFor(100);await f.hideCheck();assert.equal(await f.storedAttempt(),original);
  const count=f.state.reads.length;await f.page.clock.runFor(8100);assert.equal(f.state.reads.length,count);assert.equal(f.state.writes.length,1);
  f.state.code=200;await f.page.getByRole('button',{name:'重新驗證管理權限',exact:true}).click();
  await f.page.getByRole('button',{name:kind==='summary'?'查看彙總送印結果':'再次出單',exact:true}).click();
  await f.page.getByRole('dialog').getByText(kind==='summary'?'彙總單列印紀錄 · 1 筆訂單 · 1 張':'已建立出單工作：等待發送',{exact:true}).waitFor();
  assert.equal(await f.storedAttempt(),original);assert.equal(f.state.writes.length,1);
 });
 test(`${kind}503保留既有批次且401同帳號續期仍可正常查詢`,async t=>{
  const f=await fixture(t,kind),original=await f.storedAttempt();f.state.code=503;
  await f.lookupResponse(503,()=>f.dialog.getByRole('button',{name:'查詢送印結果',exact:true}).click());
  assert.equal(await f.privateWidget.count(),1);assert.equal(await f.dialog.count(),1);assert.equal(await f.storedAttempt(),original);
  await f.dialog.getByRole('alert').getByText('隔離暫時無法查詢',{exact:true}).waitFor();
  f.state.code=200;f.state.codes=[401,200];
  await f.lookupResponse(200,()=>f.dialog.getByRole('button',{name:'查詢送印結果',exact:true}).click());
  assert.ok(f.state.refreshes>=2);assert.equal(await f.privateWidget.count(),1);assert.equal(await f.dialog.count(),1);
  assert.equal(await f.page.getByRole('button',{name:'重新驗證管理權限',exact:true}).count(),0);assert.equal(await f.storedAttempt(),original);assert.equal(f.state.writes.length,1);
 });
}
