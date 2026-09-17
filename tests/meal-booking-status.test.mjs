import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
const source = await readFile(new URL("../web/src/lib/meal-time.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  reportDiagnostics: true,
});
assert.deepEqual(compiled.diagnostics.filter((diagnostic) => diagnostic.category === ts.DiagnosticCategory.Error), []);
const {
  mealBookingStatus, mealServiceDate, pickupTimeOptions, taipeiDate, shiftMealDate, formatMealDateTime,
} = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);

const NOON = Date.parse("2026-09-17T12:00:00+08:00");

function mealEvent(overrides = {}) {
  return {
    id: "isolated-lunch", title: "午餐", location: "隔離測試取餐區", status: "published",
    service_date: "2026-09-17", meal_period: "lunch", schedule_template_id: null,
    ordering_starts_at: "2026-09-16T00:00:00+08:00", ordering_ends_at: "2026-09-17T13:00:00+08:00",
    pickup_starts_at: "2026-09-17T11:00:00+08:00", pickup_ends_at: "2026-09-17T14:00:00+08:00",
    offerings: [{ id: "offering", is_active: true, available_quantity: 10 }],
    ...overrides,
  };
}

test("已發布但尚未開始接單，不能標可預訂", () => {
  const event = mealEvent({ ordering_starts_at: "2026-09-17T12:00:00.001+08:00" });
  assert.equal(mealBookingStatus(event, NOON), "尚未開放");
  assert.equal(mealBookingStatus(event, NOON + 1), "可預訂");
});

for (const status of ["published", "pickup_open"]) {
  test(`${status} 在 13:00 截單前可預訂，精確到點立即已截止`, () => {
    const event = mealEvent({ status });
    const cutoff = Date.parse(event.ordering_ends_at);
    assert.equal(mealBookingStatus(event, NOON), "可預訂");
    assert.equal(mealBookingStatus(event, cutoff - 1), "可預訂");
    assert.equal(mealBookingStatus(event, cutoff), "已截止");
    assert.equal(mealBookingStatus(event, cutoff + 1), "已截止");
  });
}

test("手動停止接單不會因時間仍在窗口內而顯示可預訂", () => {
  assert.equal(mealBookingStatus(mealEvent({ status: "ordering_closed" }), NOON), "已截止");
});

test("取餐結束即使收到過時場次狀態，也顯示已截止", () => {
  const event = mealEvent();
  assert.equal(mealBookingStatus(event, Date.parse(event.pickup_ends_at)), "已截止");
});

test("全部有效餐點暫無份數才顯示已售完，仍有一款可售就可預訂", () => {
  const event = mealEvent({ offerings: [
    { id: "sold-a", is_active: true, available_quantity: 0 },
    { id: "sold-b", is_active: true, available_quantity: 0 },
  ] });
  assert.equal(mealBookingStatus(event, NOON), "已售完");
  event.offerings[1].available_quantity = 1;
  assert.equal(mealBookingStatus(event, NOON), "可預訂");
});

test("空菜單與所有餐點停用都是菜單待公告，不是已售完", () => {
  assert.equal(mealBookingStatus(mealEvent({ offerings: [] }), NOON), "菜單待公告");
  assert.equal(mealBookingStatus(mealEvent({ offerings: [
    { id: "inactive", is_active: false, available_quantity: 99 },
  ] }), NOON), "菜單待公告");
});

test("停用餐點尚有份數不會讓已售完場次重新標示可預訂", () => {
  const event = mealEvent({ offerings: [
    { id: "inactive", is_active: false, available_quantity: 99 },
    { id: "sold", is_active: true, available_quantity: 0 },
  ] });
  assert.equal(mealBookingStatus(event, NOON), "已售完");
});

test("沒有未來十分鐘取餐選項時，即使尚未截單有庫存也不可標可預訂", () => {
  const event = mealEvent({ ordering_ends_at: "2026-09-17T13:55:00+08:00" });
  const lastSlot = Date.parse("2026-09-17T13:50:00+08:00");
  assert.equal(mealBookingStatus(event, lastSlot - 1), "可預訂");
  assert.equal(pickupTimeOptions(event, lastSlot).length, 0);
  assert.equal(mealBookingStatus(event, lastSlot), "已截止");
  assert.equal(mealBookingStatus(event, lastSlot + 1), "已截止");
});

test("截止判斷優先於售完與空菜單，不把截止場次誤標尚有預購窗口", () => {
  for (const offerings of [[], [{ id: "sold", is_active: true, available_quantity: 0 }]]) {
    const event = mealEvent({ offerings });
    assert.equal(mealBookingStatus(event, Date.parse(event.ordering_ends_at)), "已截止");
  }
});

test("週日手動場次與超過七天的場次，不因日期而限制可預訂", () => {
  for (const date of ["2026-09-20", "2026-10-04"]) {
    const event = mealEvent({
      service_date: date,
      ordering_ends_at: `${date}T13:00:00+08:00`,
      pickup_starts_at: `${date}T11:00:00+08:00`,
      pickup_ends_at: `${date}T14:00:00+08:00`,
    });
    assert.equal(mealServiceDate(event), date);
    assert.equal(mealBookingStatus(event, NOON), "可預訂");
  }
});

test("跨午夜餐次保留前一日服務日期，次日可取餐時刻仍可預訂", () => {
  const event = mealEvent({
    service_date: "2026-09-19", meal_period: "dinner", status: "pickup_open",
    ordering_ends_at: "2026-09-20T00:10:00+08:00",
    pickup_starts_at: "2026-09-19T23:30:00+08:00", pickup_ends_at: "2026-09-20T00:30:00+08:00",
  });
  const now = Date.parse("2026-09-20T00:01:00+08:00");
  assert.equal(taipeiDate(now), "2026-09-20");
  assert.equal(mealServiceDate(event), "2026-09-19");
  assert.equal(mealServiceDate({ ...event, service_date: null }), "2026-09-19");
  assert.equal(mealBookingStatus(event, now), "可預訂");
  const options = pickupTimeOptions(event, now);
  assert.deepEqual(options.map((option) => option.value), ["2026-09-20T00:10", "2026-09-20T00:20"]);
  assert.ok(options.every((option) => option.label.startsWith("次日 ")));
});

test("台灣日期與星期不受 UTC 前一日影響，跨月位移保持台灣日曆", () => {
  const earlySunday = "2026-09-19T16:10:00Z";
  assert.equal(taipeiDate(earlySunday), "2026-09-20");
  assert.ok(formatMealDateTime(earlySunday).includes("週日"));
  assert.equal(shiftMealDate("2026-09-30", 1), "2026-10-01");
  assert.equal(shiftMealDate("2026-10-01", -1), "2026-09-30");
});
