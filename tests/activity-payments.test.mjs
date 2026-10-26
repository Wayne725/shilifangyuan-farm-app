import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
const ts=createRequire(new URL('../web/package.json',import.meta.url))('typescript');
class ApiError extends Error {constructor(status){super();this.status=status;}}
const exports={};
const source=await readFile(new URL('../web/src/lib/activities.ts',import.meta.url),'utf8');
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports,require:()=>({ApiError}),Date,Intl});
const now=Date.parse('2030-01-02T02:00:00Z');
test('社員與見習社員使用社員價格，身分標示保留差異',()=>{
 const activity={member_price:100,non_member_price:200};
 assert.equal(exports.activityPrice(activity,'member'),100);
 assert.equal(exports.activityPrice(activity,'trainee'),100);
 assert.equal(exports.activityPrice(activity,'nonmember'),200);
 assert.equal(exports.activityPrice(activity),200);
 assert.equal(exports.activityMembershipLabel('trainee'),'見習社員');
 assert.equal(exports.activityPrice({},'member'),0);
});
test('付款按鈕同時要求API授權與有效期限，截止當下停止',()=>{
 const charge={expires_at:'2030-01-02T02:00:00',available_actions:['pay']};
 assert.equal(exports.canPayActivity(charge,now-1),true);
 assert.equal(exports.canPayActivity(charge,now),false);
 assert.equal(exports.canPayActivity({...charge,available_actions:[]},now-1),false);
 assert.equal(exports.canPayActivity({...charge,expires_at:'bad'},now-1),false);
});
test('無offset時間視為UTC，呈現固定台灣時區',()=>{
 assert.equal(exports.activityTime('2030-01-02T02:00:00'),now);
 assert.equal(exports.activityTime('2030-01-02T10:00:00+08:00'),now);
 assert.match(exports.activityDate('2030-01-02T02:00:00'),/10:00/);
 assert.equal(exports.activityDate(null),'—');
 assert.equal(exports.activityDate('bad'),'—');
});
test('401與403不重試，短暫服務失敗至多重試一次',()=>{
 for(const status of [401,403]){assert.equal(exports.activityAccessDenied(new ApiError(status)),true);assert.equal(exports.retryActivityRead(0,new ApiError(status)),false);}
 assert.equal(exports.activityAccessDenied(new Error('failed')),false);
 assert.equal(exports.retryActivityRead(0,new ApiError(503)),true);
 assert.equal(exports.retryActivityRead(1,new ApiError(503)),false);
});
test('活動歷史查詢結合狀態與活動名稱／應收編號／聯絡資料',()=>{
 const rows=[{id:'one',activity_title:'永續走讀',status:'registered',charge:{number:'ACT-001'},contact_name:'報名者',contact_email:'Person@example.test'},{id:'two',activity_title:'園藝課',status:'cancelled'}];
 for(const q of ['走讀',' act-001 ','報名者','person@EXAMPLE']) assert.equal(exports.filterActivityRegistrations(rows,q,'registered')[0].id,'one');
 assert.equal(exports.filterActivityRegistrations(rows,'走讀','cancelled').length,0);
 assert.equal(exports.filterActivityRegistrations(rows,'','').length,2);
});
test('付款／報名／退款狀態完整中文，金流已退不顯示人工核准',()=>{
 for(const status of ['pending','confirming','paid','expired','failed','refund_pending','refunded','late_paid_refund_required'])assert.notEqual(exports.activityPaymentLabel(status),status);
 for(const status of ['pending_payment','registered','waitlisted','cancelled','attended','no_show'])assert.notEqual(exports.activityRegistrationLabel(status),status);
 for(const status of ['pending_review','approved','rejected','externally_refunded'])assert.notEqual(exports.activityRefundLabel(status),status);
 assert.equal(exports.activityRefundLabel('externally_refunded'),'金流已退款');
});
