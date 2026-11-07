import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import {extname, resolve} from 'node:path';
import test from 'node:test';

const {chromium, webkit}=createRequire(import.meta.url)('playwright');
const engine=process.env.BROWSER_ENGINE==='webkit'?webkit:chromium;
const base='http://127.0.0.1:4241';
const root=resolve(import.meta.dirname,'../../web/dist');
const contentTypes={'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.png':'image/png','.webp':'image/webp'};

async function fixture(t,{width=390,changed=true,paid=false,uncertain=false}={}) {
  const browser=await engine.launch({headless:true,...(engine===chromium?{executablePath:process.env.BROWSER_EXECUTABLE}:{})});
  t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width,height:950},serviceWorkers:'block'});
  const order={id:'old-group-order',order_number:'SLF-GROUP-HISTORY',order_kind:'group',sales_channel:'group',
    fulfillment_method:'cooperative_pickup',group_campaign_id:'original-campaign',
    group_campaign_deadline:'2099-12-01T04:00:00Z',group_contract_changed:changed,group_payment_uncertain:uncertain,
    payment_status:paid?'paid':'pending',invoice_status:'not_eligible',fulfillment_status:'pending_confirmation',
    revision:1,amendment_blocked_reason:changed?'共同購買核心條件已更新，請回共同購買頁重新確認':'隔離測試不變更數量',
    amount_total:100,tax_amount:0,created_at:'2030-01-02T04:00:00Z',
    available_actions:paid?[]:['pay'],items:[{id:'original-item',product_name:'原訂商品',quantity:1,unit_price:100,subtotal:100,unit_label:'份'}]};
  const state={writes:[],unknown:[],errors:[]};
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(url.origin!==base)return route.abort();
    if(!path.startsWith('/v1/')){
      const file=extname(path)?resolve(root,`.${path}`):resolve(root,'index.html');
      assert.ok(file.startsWith(`${root}/`));
      return route.fulfill({contentType:contentTypes[extname(file)]||'application/octet-stream',body:await readFile(file)});
    }
    if(path==='/v1/auth/refresh')return route.fulfill({json:{access_token:'isolated-group-contract',user:{id:'group-buyer',email:'buyer@example.test',display_name:'買家',membership_type:'nonmember',user_role:'customer'}}});
    if(request.method()!=='GET')state.writes.push(path);
    if(path==='/v1/platform')return route.fulfill({json:{orders_open:true}});
    if(path==='/v1/orders')return route.fulfill({json:[order]});
    if(path==='/v1/meal-orders')return route.fulfill({json:[]});
    state.unknown.push(`${request.method()} ${path}`);
    return route.fulfill({status:404,json:{detail:'Undefined isolated route'}});
  });
  const page=await context.newPage();page.setDefaultTimeout(6000);
  page.on('pageerror',error=>state.errors.push(error.message));
  await page.goto(`${base}/orders?order_id=${order.id}`);
  await page.getByRole('heading',{name:order.order_number,exact:true}).waitFor();
  t.after(()=>{assert.deepEqual(state.unknown,[]);assert.deepEqual(state.errors,[]);assert.deepEqual(state.writes,[]);});
  return {page,state};
}

for(const width of [390,1440]) {
  test(`已更換核心條件的原訂單保留歷史並提供重新確認入口 ${width}`,async t=>{
    const {page}=await fixture(t,{width});
    assert.equal(await page.getByRole('button',{name:'前往線上付款',exact:true}).count(),0);
    await page.getByText('共同購買核心條件已更新，這筆訂單無法繼續付款，請重新確認。',{exact:true}).waitFor();
    const link=page.getByRole('link',{name:'回共同購買重新確認',exact:true});
    assert.equal(await link.getAttribute('href'),'/groups/original-campaign');
    await page.getByText('原訂商品',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  });
  test(`未變更條件的共同購買保留原付款按鈕 ${width}`,async t=>{
    const {page}=await fixture(t,{width,changed:false});
    await page.getByRole('button',{name:'前往線上付款',exact:true}).waitFor();
    assert.equal(await page.getByRole('link',{name:'回共同購買重新確認',exact:true}).count(),0);
  });
  test(`已付款歷史共同購買不要求再次下單 ${width}`,async t=>{
    const {page}=await fixture(t,{width,changed:false,paid:true});
    assert.equal(await page.getByRole('button',{name:'前往線上付款',exact:true}).count(),0);
    assert.equal(await page.getByRole('link',{name:'回共同購買重新確認',exact:true}).count(),0);
    await page.getByText('原訂商品',{exact:true}).waitFor();
  });
  for(const changed of [false,true])test(`原付款未知時不引導重新下單，條件變更=${changed} ${width}`,async t=>{
    const {page}=await fixture(t,{width,changed,uncertain:true});
    assert.equal(await page.getByRole('button',{name:'前往線上付款',exact:true}).count(),0);
    assert.equal(await page.getByRole('link',{name:'回共同購買重新確認',exact:true}).count(),0);
    await page.getByText('付款結果仍待核對，請勿再次下單或付款。',{exact:true}).waitFor();
  });
}
