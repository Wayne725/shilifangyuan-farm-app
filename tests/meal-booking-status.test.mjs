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
  mealBookingStatus, mealOrderingOpen, mealServiceDate, pickupTimeOptions,
  validPickupTime, taipeiDate, shiftMealDate, formatMealDateTime,
} = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);

const NOW = Date.parse("2026-09-17T12:00:00+08:00");

function mealEvent(overrides = {}) {
  return {
    id: "isolated-lunch", title: "午餐", location: "隔離測試取餐區", status: "published",
    service_date: "2026-09-18", meal_period: "lunch", schedule_template_id: null,
    ordering_starts_at: "2026-09-17T00:00:00+08:00", ordering_ends_at: "2026-09-18T00:00:00+08:00",
    pickup_starts_at: "2026-09-18T11:00:00+08:00", pickup_ends_at: "2026-09-18T13:30:00+08:00",
    offerings: [{ id: "offering", is_active: true, available_quantity: 10 }],
    ...overrides,
  };
}

test("台灣今天只能訂明天，今日及後天場次均不接受預訂", () => {
  assert.equal(mealBookingStatus(mealEvent(), NOW), "可預訂");
  for (const date of ["2026-09-17", "2026-09-19", "2026-10-04"]) {
    const event = mealEvent({ service_date: date,
      ordering_starts_at: "2026-09-01T00:00:00+08:00", ordering_ends_at: "2026-10-10T00:00:00+08:00",
      pickup_starts_at: `${date}T11:00:00+08:00`, pickup_ends_at: `${date}T13:30:00+08:00` });
    assert.equal(mealOrderingOpen(event, NOW), false);
    assert.deepEqual(pickupTimeOptions(event, NOW), []);
    assert.equal(mealBookingStatus(event, NOW), date === "2026-09-17" ? "已截止" : "尚未開放");
  }
});

test("縮短供餐窗口後，後端時段與舊最後時段都不能超過核銷結束", () => {
  for (const pickup_slots of [undefined, ["2026-09-18T11:00:00+08:00", "2026-09-18T11:30:00+08:00", "2026-09-18T12:00:00+08:00", "2026-09-18T12:30:00+08:00", "2026-09-18T13:00:00+08:00"]]) {
    const event = mealEvent({ pickup_slot_ends_at: "2026-09-18T13:00:00+08:00", pickup_ends_at: "2026-09-18T12:00:00+08:00", pickup_slots });
    assert.deepEqual(pickupTimeOptions(event, NOW).map(({ label }) => label), ["11:00", "11:30"]);
  }
});

test("午餐固定五個半小時時段，包含13:00，不提供13:30", () => {
  const options = pickupTimeOptions(mealEvent(), NOW);
  assert.deepEqual(options.map((option) => option.label), ["11:00", "11:30", "12:00", "12:30", "13:00"]);
  assert.equal(validPickupTime("2026-09-18T13:00", mealEvent(), NOW), true);
  for (const value of ["2026-09-18T11:10", "2026-09-18T12:15", "2026-09-18T13:30", "bad", ""]) {
    assert.equal(validPickupTime(value, mealEvent(), NOW), false);
  }
});

test("晚餐使用相同邏輯，四個時段包含18:00", () => {
  const event = mealEvent({ meal_period: "dinner", pickup_starts_at: "2026-09-18T16:30:00+08:00", pickup_ends_at: "2026-09-18T18:30:00+08:00" });
  assert.deepEqual(pickupTimeOptions(event, NOW).map((option) => option.label), ["16:30", "17:00", "17:30", "18:00"]);
  assert.equal(validPickupTime("2026-09-18T18:00", event, NOW), true);
  assert.equal(validPickupTime("2026-09-18T18:30", event, NOW), false);
});

test("時段與既有場次窗口取交集，不從11:10錯排半小時", () => {
  const event = mealEvent({ pickup_starts_at: "2026-09-18T11:10:00+08:00", pickup_ends_at: "2026-09-18T12:30:00+08:00" });
  assert.deepEqual(pickupTimeOptions(event, NOW).map((option) => option.label), ["11:30", "12:00"]);
});

for (const [period, start, close, expected] of [
  ["lunch", "11:00", "13:00", ["11:00", "11:30", "12:00", "12:30"]],
  ["dinner", "16:30", "18:00", ["16:30", "17:00", "17:30"]],
]) {
  test(`${period} 舊場次的實際結束時間不提供為可選時段`, () => {
    const event = mealEvent({ meal_period: period, pickup_starts_at: `2026-09-18T${start}:00+08:00`, pickup_ends_at: `2026-09-18T${close}:00+08:00`, pickup_slot_ends_at: null });
    assert.deepEqual(pickupTimeOptions(event, NOW).map((option) => option.label), expected);
    assert.equal(validPickupTime(`2026-09-18T${close}`, event, NOW), false);
    event.pickup_slots = [`2026-09-18T${close}:00+08:00`];
    assert.deepEqual(pickupTimeOptions(event, NOW), []);
  });
}

test("官方最後取餐時段可包含端點，並限制前端備援時段", () => {
  const event = mealEvent({ pickup_slot_ends_at: "2026-09-18T13:00:00+08:00" });
  assert.equal(validPickupTime("2026-09-18T13:00", event, NOW), true);
  event.pickup_slot_ends_at = "2026-09-18T12:30:00+08:00";
  assert.deepEqual(pickupTimeOptions(event, NOW).map((option) => option.label), ["11:00", "11:30", "12:00", "12:30"]);
  assert.equal(validPickupTime("2026-09-18T13:00", event, NOW), false);
});

test("優先使用後端公布時段，空清單不擅自補出可預訂時段", () => {
  const event = mealEvent({ pickup_slots: ["2026-09-18T04:00:00Z", "2026-09-18T03:30:00Z", "2026-09-18T03:30:00Z", "2026-09-18T03:10:00Z"] });
  assert.deepEqual(pickupTimeOptions(event, NOW).map((option) => option.label), ["11:30", "12:00"]);
  assert.deepEqual(pickupTimeOptions(mealEvent({ pickup_slots: [] }), NOW), []);
  assert.equal(mealBookingStatus(mealEvent({ pickup_slots: [] }), NOW), "已截止");
});

test("跨午夜舊場次保留服務日但不提供超出固定餐別的時段", () => {
  const event = mealEvent({ service_date: "2026-09-18", meal_period: "dinner", pickup_starts_at: "2026-09-18T23:30:00+08:00", pickup_ends_at: "2026-09-19T00:30:00+08:00" });
  assert.equal(mealServiceDate(event), "2026-09-18");
  assert.equal(mealServiceDate({ ...event, service_date: null }), "2026-09-18");
  assert.deepEqual(pickupTimeOptions(event, NOW), []);
  assert.equal(mealBookingStatus(event, NOW), "已截止");
});

test("已發布但尚未開始接單，不能標可預訂", () => {
  const event = mealEvent({ booking_starts_at: "2026-09-17T12:00:00.001+08:00" });
  assert.equal(mealBookingStatus(event, NOW), "尚未開放");
  assert.equal(mealBookingStatus(event, NOW + 1), "可預訂");
});

for (const status of ["published", "pickup_open"]) {
  test(`${status} 精確到有效截單時刻立即關閉`, () => {
    const event = mealEvent({ status, booking_ends_at: "2026-09-17T13:00:00+08:00" });
    const cutoff = Date.parse(event.booking_ends_at);
    assert.equal(mealBookingStatus(event, NOW), "可預訂");
    assert.equal(mealBookingStatus(event, cutoff - 1), "可預訂");
    assert.equal(mealBookingStatus(event, cutoff), "已截止");
    assert.equal(mealBookingStatus(event, cutoff + 1), "已截止");
  });
}

test("台灣午夜切換日期後，前一天的草稿時段與預訂資格立即失效", () => {
  const midnight = Date.parse("2026-09-17T16:00:00Z");
  const event = mealEvent({ ordering_ends_at: "2026-09-18T14:00:00+08:00" });
  assert.equal(mealOrderingOpen(event, midnight - 1), true);
  assert.equal(mealOrderingOpen(event, midnight), false);
  assert.deepEqual(pickupTimeOptions(event, midnight), []);
  assert.equal(mealBookingStatus(event, midnight), "已截止");
});

test("手動停止接單不會因時間仍在窗口內而顯示可預訂", () => {
  assert.equal(mealBookingStatus(mealEvent({ status: "ordering_closed" }), NOW), "已截止");
});

test("全部有效餐點暫無份數才顯示已售完，仍有一款可售就可預訂", () => {
  const event = mealEvent({ offerings: [
    { id: "sold-a", is_active: true, available_quantity: 0 },
    { id: "sold-b", is_active: true, available_quantity: 0 },
  ] });
  assert.equal(mealBookingStatus(event, NOW), "已售完");
  event.offerings[1].available_quantity = 1;
  assert.equal(mealBookingStatus(event, NOW), "可預訂");
});

test("空菜單與所有餐點停用都是菜單待公告，不是已售完", () => {
  assert.equal(mealBookingStatus(mealEvent({ offerings: [] }), NOW), "菜單待公告");
  assert.equal(mealBookingStatus(mealEvent({ offerings: [
    { id: "inactive", is_active: false, available_quantity: 99 },
  ] }), NOW), "菜單待公告");
});

test("停用餐點尚有份數不會讓已售完場次重新標示可預訂", () => {
  const event = mealEvent({ offerings: [
    { id: "inactive", is_active: false, available_quantity: 99 },
    { id: "sold", is_active: true, available_quantity: 0 },
  ] });
  assert.equal(mealBookingStatus(event, NOW), "已售完");
});

test("截止判斷優先於售完與空菜單", () => {
  for (const offerings of [[], [{ id: "sold", is_active: true, available_quantity: 0 }]]) {
    const event = mealEvent({ offerings });
    assert.equal(mealBookingStatus(event, Date.parse(event.ordering_ends_at)), "已截止");
  }
});

test("台灣日期與星期不受UTC前一日影響，跨月位移保持台灣日曆", () => {
  const earlySunday = "2026-09-19T16:10:00Z";
  assert.equal(taipeiDate(earlySunday), "2026-09-20");
  assert.ok(formatMealDateTime(earlySunday).includes("週日"));
  assert.equal(shiftMealDate("2026-09-30", 1), "2026-10-01");
  assert.equal(shiftMealDate("2026-10-01", -1), "2026-09-30");
});
