import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
class ApiError extends Error { constructor(status) { super(); this.status = status; } }
const modules = { "./api": { ApiError } };
for (const name of ["meal-time", "group-campaigns", "order-quantity-amendments"]) {
  const source = await readFile(new URL(`../web/src/lib/${name}.ts`, import.meta.url), "utf8");
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText, { exports, require: path => modules[path], Date, Intl });
  modules[`./${name}`] = exports;
}
const { campaignTimestamp, campaignDateTime, defaultReviewDeadline, campaignOrderBlockReason, campaignStatusLabel, campaignConfirmBlockReason, campaignScheduleError, retryCampaignRead } = modules["./group-campaigns"];
const now = Date.parse("2030-01-02T02:00:00Z");
const campaign = {deadline:"2030-01-03T00:00:00Z",decision_status:"recruiting",intake_status:"open",paid_quantity:12,min_paid_quantity:10,available_quantity:8,threshold_version:2,can_confirm:false};

test("UTC無時區資料仍以UTC讀取，輸入固定台灣時間及72小時預設", () => {
  assert.equal(campaignTimestamp("2030-01-02T02:00:00"), now);
  assert.match(campaignDateTime("2030-01-02T02:00:00"), /10:00/);
  assert.equal(defaultReviewDeadline("2030-01-02T10:00"), "2030-01-05T10:00");
});
test("達標不提前停收，截止當下拒絕接單；歷史已確認仍保留原截止", () => {
  assert.equal(campaignOrderBlockReason(campaign,now), "");
  assert.equal(campaignStatusLabel(campaign,now), "已達門檻，持續集單");
  assert.equal(campaignOrderBlockReason({...campaign,decision_status:"confirmed"},now), "");
  assert.ok(campaignOrderBlockReason(campaign,campaignTimestamp(campaign.deadline)));
  assert.ok(campaignOrderBlockReason({...campaign,intake_status:"full"},now));
});
test("成團必須API授權、已截止、已結算且有有效付款", () => {
  const ready={...campaign,deadline:"2030-01-01T00:00:00Z",decision_status:"pending_confirmation",intake_status:"closed",can_confirm:true};
  assert.equal(campaignConfirmBlockReason(ready,now), "");
  for(const data of [{can_confirm:false},{paid_quantity:0},{intake_status:"settling"},{threshold_version:undefined},{deadline:campaign.deadline}]) assert.ok(campaignConfirmBlockReason({...ready,...data},now));
  assert.equal(campaignConfirmBlockReason({...ready,paid_quantity:6},now), "", "低於門檻交由明確例外流程，不偷改門檻");
});
test("專用審核期限允許付款截止已過，但新審核與取貨日期須有效", () => {
  const data=["2030-01-01T10:00","2030-01-05T10:00","2030-01-06T10:00","2030-01-07T10:00"];
  assert.equal(campaignScheduleError(...data,now,true), "");
  assert.ok(campaignScheduleError(...data,now));
  assert.ok(campaignScheduleError(data[0],"2030-01-02T09:00",data[2],data[3],now,true));
  assert.ok(campaignScheduleError(data[0],"2030-01-06T11:00",data[2],data[3],now,true));
});
test("既有共同購買訂單在FULL可依API授權改量，截止時即使舊action仍在也阻擋", () => {
  const order={available_actions:["amend_quantities"],revision:1,items:[{id:"i",product_name:"米",quantity:2}],group_campaign_deadline:campaign.deadline};
  assert.equal(modules["./order-quantity-amendments"].canAmendOrder(order,now),true);
  assert.equal(modules["./order-quantity-amendments"].canAmendOrder(order,campaignTimestamp(campaign.deadline)),false);
});
test("讀取遇到401/403不重試", () => {
  for(const status of [401,403]) assert.equal(retryCampaignRead(0,new ApiError(status)),false);
  assert.equal(retryCampaignRead(0,new ApiError(503)),true);
  assert.equal(retryCampaignRead(1,new ApiError(503)),false);
});

test("提案全部enum都有中文顯示，歷史enum不改寫",async()=>{
  const model=await readFile(new URL("../backend/app/models.py",import.meta.url),"utf8");
  const enumSource=model.match(/class ProposalStatus[\s\S]+?(?=\n\nclass)/)[0];
  const statuses=[...enumSource.matchAll(/= "([a-z_]+)"/g)].map(match=>match[1]);
  assert.equal(statuses.length,7);
  for(const status of statuses)assert.notEqual(modules["./group-campaigns"].groupProposalStatusLabel(status),status);
  assert.equal(modules["./group-campaigns"].groupProposalStatusLabel("ended_unmet"),"未達投票門檻");
  assert.equal(modules["./group-campaigns"].groupProposalStatusLabel("expired_unhandled"),"逾期未開團");
});
test("數量鎖定原因以已結束、截止、付款紀錄依序判斷",()=>{
  const reason=modules["./group-campaigns"].campaignQuantityBlockReason;
  const paid={...campaign,core_locked_at:"2030-01-01T00:00:00Z",paid_quantity:0};
  assert.equal(reason({...paid,decision_status:"failed_unmet"},now),"此團已結束，無法修改數量設定。");
  assert.equal(reason({...paid,deadline:"2030-01-01T00:00:00Z"},now),"付款已截止，無法修改數量設定。");
  assert.equal(reason(paid,now),"已有付款紀錄，限購與成團數量已鎖定。");
  assert.equal(reason(campaign,now),"");
});
