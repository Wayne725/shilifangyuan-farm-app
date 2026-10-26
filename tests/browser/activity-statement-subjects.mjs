import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import test from 'node:test';
const {chromium} = createRequire(import.meta.url)('playwright');
const base = process.env.BROWSER_TEST_URL || 'http://127.0.0.1:4197';
if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use an isolated origin');
const batches = [
  {id:'payments',source:'newebpay',filename:'收款明細.csv',row_count:4,skipped_rows:0,created_at:'2030-01-02T04:00:00Z'},
  {id:'invoices',source:'invoice',filename:'發票明細.xlsx',row_count:1,skipped_rows:0,created_at:'2030-01-02T04:00:00Z'},
];
const seed = [
  ['regular','order','general','SLF-10','ORDER-10'],
  ['meal_preorder','order','meal','MEAL-20','MEAL-20'],
  [null,'activity','charge','ACT-42','ACT-TRADE'],
  [null,null,null,null,'UNKNOWN'],
].map(([sales_channel,subject_type,subject_id,subject_number,reference],i) => ({
  sales_channel,subject_type,subject_id,subject_number,reference,row_number:i+2,external_id:`0000000000000000000${i}`,
  kind:'receipt',amount:'300',fee:null,payout:null,occurred_at:'2030-01-02 12:00:00',
  order_id:subject_type==='order'?subject_id:null,order_number:subject_type==='order'?subject_number:null,
  expected_amount:subject_id?'300':null,difference:subject_id?'0':null,result:subject_id?'matched':'unmatched',
  reasons:subject_id?[]:['尚未連結網站訂單或活動應收'],manual_order_id:null,manual_subject_id:null,
}));
async function fixture(t,width,path='/admin/statements') {
  const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||chromium.executablePath()});
  t.after(()=>browser.close());
  const context=await browser.newContext({viewport:{width,height:1000},timezoneId:'Asia/Taipei',serviceWorkers:'block'});
  const state={rows:structuredClone(seed),reads:[],writes:[],downloads:[],unknown:[],errors:[],deny:0};
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url()),p=url.pathname,params=Object.fromEntries(url.searchParams);
    if(url.origin!==base)return route.abort();
    if(!p.startsWith('/v1/'))return route.continue();
    if(p==='/v1/auth/refresh')return route.fulfill({json:{access_token:'isolated',user:{id:'admin',user_role:'admin',display_name:'管理員',email:'admin@example.test',membership_type:'member'}}});
    if(['/v1/products','/v1/meal-events','/v1/group-campaigns'].includes(p))return route.fulfill({json:[]});
    if(p==='/v1/platform')return route.fulfill({json:{orders_open:true}});
    if(p==='/v1/auth/line/status')return route.fulfill({json:{ready:true,linked:false}});
    if(p==='/v1/admin/financial-statements')return route.fulfill({json:{items:params.source?[{...batches[0],id:'sample',filename:`${params.source}明細`}]:batches,total:params.source?1:2}});
    if(p.endsWith('/accounting-export')||p.endsWith('/reconciliation.csv')) {
      state.downloads.push(params);
      if(state.deny)return route.fulfill({status:state.deny,json:{detail:'權限已失效'}});
      return route.fulfill({body:'來源,金額\n活動,300',contentType:'text/csv'});
    }
    if(['/v1/admin/financial-statements/payments','/v1/admin/financial-statements/invoices'].includes(p)) {
      state.reads.push(params);
      if(state.deny)return route.fulfill({status:state.deny,json:{detail:'權限已失效'}});
      const invoice=p.endsWith('/invoices'),batch=invoice?batches[1]:batches[0];
      const rows=(invoice?[state.rows[0]]:state.rows).filter(row=>(!params.subject_type||params.subject_type==='all'||row.subject_type===params.subject_type)
        &&(!params.order_category||params.order_category==='all'||(params.order_category==='meal'?row.sales_channel==='meal_preorder':['regular','group'].includes(row.sales_channel)))
        &&(!params.q||[row.subject_number,row.reference,row.external_id].join(' ').includes(params.q))
        &&(!params.result||params.result==='all'||params.result===row.result));
      return route.fulfill({json:{...batch,items:rows,total:rows.length,summary:Object.fromEntries(['matched','difference','unmatched'].map(result=>[result,rows.filter(row=>row.result===result).length]))}});
    }
    if(p.endsWith('/order-options'))return route.fulfill({json:[{id:'general',order_number:'SLF-10',sales_channel:'regular',amount:300,created_at:'2030-01-02T04:00:00Z'}]});
    if(p.endsWith('/subject-options')) {
      state.reads.push(params);
      return route.fulfill({json:[{id:'charge',subject_type:'activity',subject_number:'ACT-42',sales_channel:null,amount:300,created_at:'2030-01-02T04:00:00Z'}]});
    }
    if(p.endsWith('/rows/5/match')&&req.method()==='PUT') {
      const body=req.postDataJSON();state.writes.push(body);
      state.rows[3]=body.subject_id?{...seed[3],subject_type:'activity',subject_id:'charge',subject_number:'ACT-42',manual_subject_id:'charge',expected_amount:'300',difference:'0',result:'matched',reasons:[]}:structuredClone(seed[3]);
      return route.fulfill({json:body});
    }
    state.unknown.push(`${req.method()} ${p}`);return route.fulfill({status:404,json:{detail:'未定義的隔離請求'}});
  });
  const page=await context.newPage();page.setDefaultTimeout(8000);page.on('pageerror',error=>state.errors.push(error.message));
  await page.goto(base+path);
  return {page,state};
}
async function clean(page,state){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);assert.deepEqual(state.unknown,[]);assert.deepEqual(state.errors,[]);}

for(const width of [1440,816,390]) {
  test(`${width}px 對帳來源、一般便當、活動連結與發票隔離`,async t=>{
    const {page,state}=await fixture(t,width);
    await page.getByText('全部來源 · 共 4 筆',{exact:true}).waitFor();
    await page.getByLabel('對帳來源',{exact:true}).selectOption('order');
    await page.getByRole('combobox',{name:'訂單分類',exact:true}).selectOption('meal');
    await page.getByText('便當訂單 · 共 1 筆',{exact:true}).waitFor();
    await page.getByLabel('對帳來源',{exact:true}).selectOption('activity');
    await page.getByText('活動應收 · 共 1 筆',{exact:true}).waitFor();
    assert.equal(await page.getByRole('combobox',{name:'訂單分類',exact:true}).count(),0);
    await page.getByLabel('查詢對帳明細',{exact:true}).fill('ACT-42');
    const searched=page.waitForResponse(r=>r.url().includes('q=ACT-42'));
    await page.getByRole('button',{name:'查詢',exact:true}).click();await searched;
    const download=page.waitForEvent('download');await page.getByRole('button',{name:'匯出對帳結果 CSV'}).click();await download;
    assert.deepEqual(state.downloads.at(-1),{result:'all',order_category:'all',q:'ACT-42',subject_type:'activity'});
    await page.getByRole('button',{name:'清除篩選',exact:true}).click();
    await page.getByText('全部來源 · 共 4 筆',{exact:true}).waitFor();
    await page.getByRole('row').filter({hasText:'UNKNOWN'}).getByRole('button',{name:'連結訂單',exact:true}).click();
    const editor=page.getByRole('region',{name:'連結網站資料'});
    await editor.getByLabel('網站訂單',{exact:true}).selectOption('general');
    await editor.getByLabel('連結來源',{exact:true}).selectOption('activity');
    assert.equal(await editor.getByRole('button',{name:'儲存連結並核對'}).isDisabled(),true);
    await editor.getByLabel('搜尋活動應收編號',{exact:true}).fill('ACT-42');
    await editor.getByRole('button',{name:'搜尋',exact:true}).click();
    await editor.getByLabel('活動應收',{exact:true}).selectOption('charge');
    await editor.getByRole('button',{name:'儲存連結並核對'}).click();
    await page.getByRole('status').filter({hasText:'活動應收連結已更新'}).waitFor();
    assert.deepEqual(state.writes[0],{subject_type:'activity',subject_id:'charge'});
    await page.getByRole('row').filter({hasText:'UNKNOWN'}).getByRole('button',{name:'連結活動應收'}).click();
    await page.getByRole('button',{name:'還原自動核對'}).click();
    assert.deepEqual(state.writes.at(-1),{subject_type:'activity',subject_id:null});
    await page.getByLabel('匯入紀錄',{exact:true}).selectOption('invoices');
    await page.getByText('全部訂單 · 共 1 筆',{exact:true}).waitFor();
    assert.deepEqual(await page.getByLabel('對帳來源',{exact:true}).locator('option').evaluateAll(options=>options.map(o=>o.value)),['order']);
    await page.getByRole('button',{name:'連結訂單',exact:true}).click();
    assert.deepEqual(await page.getByLabel('連結來源',{exact:true}).locator('option').evaluateAll(options=>options.map(o=>o.value)),['order']);
    await page.locator('.reading-menu > summary').click();await page.getByRole('button',{name:'放大',exact:true}).click();
    await clean(page,state);
  });
  test(`${width}px 會計來源與格式同步且活動不產生商品發票`,async t=>{
    const {page,state}=await fixture(t,width,'/admin/accounting');
    const form=page.locator('.accounting-export');
    await form.getByRole('heading',{name:'會計格式匯出'}).waitFor();
    assert.deepEqual(await form.getByLabel('匯出來源',{exact:true}).locator('option').evaluateAll(options=>options.map(o=>o.value)),['order']);
    await form.getByLabel('匯出格式',{exact:true}).selectOption('newebpay');
    await form.getByRole('combobox',{name:'訂單分類',exact:true}).selectOption('meal');
    await form.getByLabel('匯出來源',{exact:true}).selectOption('activity');
    assert.equal(await form.getByRole('combobox',{name:'訂單分類',exact:true}).count(),0);
    await form.getByLabel('交易日期起',{exact:true}).fill('2030-01-01');await form.getByLabel('交易日期迄',{exact:true}).fill('2030-01-31');
    await form.getByLabel('資料來源',{exact:true}).selectOption('sample');
    let download=page.waitForEvent('download');await form.getByRole('button',{name:'匯出會計檔案'}).click();await download;
    assert.equal(state.downloads.at(-1).subject_type,'activity');assert.equal(state.downloads.at(-1).order_category,'all');
    await form.getByLabel('匯出格式',{exact:true}).selectOption('invoice');
    assert.equal(await form.getByLabel('資料來源',{exact:true}).inputValue(),'');
    assert.equal(await form.getByLabel('匯出來源',{exact:true}).inputValue(),'order');
    download=page.waitForEvent('download');await form.getByRole('button',{name:'匯出會計檔案'}).click();await download;
    assert.equal(state.downloads.at(-1).source,'invoice');assert.equal(state.downloads.at(-1).subject_type,'order');assert.equal(state.downloads.at(-1).batch_id,undefined);
    await page.locator('.reading-menu > summary').click();await page.getByRole('button',{name:'放大',exact:true}).click();
    await clean(page,state);
  });
}
for(const status of [401,403])test(`對帳${status}不保留活動交易快取或手動連結入口`,async t=>{
  const {page,state}=await fixture(t,390);await page.getByText('全部來源 · 共 4 筆',{exact:true}).waitFor();state.deny=status;
  await page.getByRole('button',{name:'重新核對'}).click();await page.getByText('對帳權限已失效',{exact:true}).waitFor();
  assert.equal(await page.getByText('ACT-42',{exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'連結活動應收'}).count(),0);await clean(page,state);
});
test('會計匯出403移除匯出操作',async t=>{
  const {page,state}=await fixture(t,390,'/admin/accounting');await page.getByRole('heading',{name:'會計格式匯出'}).waitFor();state.deny=403;
  await page.getByRole('button',{name:'匯出會計檔案'}).click();await page.getByText('會計匯出權限已失效',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'匯出會計檔案'}).count(),0);await clean(page,state);
});
