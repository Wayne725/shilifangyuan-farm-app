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
  mealBookingStatus, mealOrderingOpen, mealServiceDate, pickupTimeOptions, mealPickupChoices,
  validPickupTime, taipeiDate, shiftMealDate, formatMealDateTime, mealBookingDates, mealMenuAvailability,
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
  assert.equal(mealOrderingOpen(event, midnight - 1), false);
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

test("精簡日期摘要與完整菜單維持相同售完、空菜單及已截止判斷", () => {
  for (const [overrides, expected] of [
    [{}, "可預訂"],
    [{ offerings: [] }, "菜單待公告"],
    [{ offerings: [{ is_active: false, available_quantity: 2 }, { is_active: true, available_quantity: 0 }] }, "已售完"],
    [{ status: "ordering_closed", offerings: [] }, "已截止"],
    [{ booking_starts_at: "2026-09-17T12:00:00.001+08:00" }, "尚未開放"],
  ]) {
    const { offerings, ...timing } = mealEvent(overrides);
    assert.equal(mealBookingStatus({ ...timing, ...mealMenuAvailability(offerings) }, NOW), expected);
    assert.equal(mealBookingStatus({ ...timing, offerings }, NOW), expected);
  }
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

test("取餐選單合併午晚時段，保留各自場次、地點與售完狀態", () => {
  const lunch = mealEvent();
  const dinner = mealEvent({ id: "dinner", meal_period: "dinner", pickup_starts_at: "2026-09-18T16:30:00+08:00", pickup_ends_at: "2026-09-18T18:30:00+08:00", offerings: [{ id: "dinner-dish", is_active: true, available_quantity: 0 }] });
  const choices = mealPickupChoices([dinner, lunch, mealEvent({ id: "hidden", status: "draft" })], "2026-09-18", NOW);
  assert.deepEqual(choices.map((row) => row.label), ["11:00", "11:30", "12:00", "12:30", "13:00", "16:30", "17:00", "17:30", "18:00"]);
  assert.equal(choices[0].eventId, lunch.id);
  assert.equal(choices[0].status, "可預訂");
  assert.equal(choices.at(-1).eventId, dinner.id);
  assert.equal(choices.at(-1).status, "已售完");
  assert.deepEqual(mealPickupChoices([lunch, dinner], "2026-09-17", NOW), []);
});

for (const status of ["published", "pickup_open"]) {
  test(`${status} 台灣 20:00 起停止隔日單，舊場次較晚截止也不能繞過`, () => {
    const event = mealEvent({ status, ordering_ends_at: "2026-09-18T12:00:00+08:00" });
    assert.equal(mealBookingStatus(event, Date.parse("2026-09-17T19:59:59+08:00")), "可預訂");
    assert.equal(mealBookingStatus(event, Date.parse("2026-09-17T20:00:00+08:00")), "已截止");
    assert.equal(mealBookingStatus(event, Date.parse("2026-09-17T20:00:00.001+08:00")), "已截止");
  });
}

function workdayMeal(date = "2026-11-09", overrides = {}) {
  return mealEvent({ booking_policy: "workday", service_date: date,
    booking_starts_at: "2026-11-06T00:00:00+08:00",
    booking_ends_at: `${shiftMealDate(date, -1)}T20:00:00+08:00`,
    pickup_starts_at: `${date}T11:00:00+08:00`, pickup_ends_at: `${date}T13:30:00+08:00`,
    ...overrides });
}

for (const date of ["2026-11-06", "2026-11-07", "2026-11-08"]) {
  test(`工作日場次在 ${date} 可預訂週一，不受明天限制`, () => {
    const now = Date.parse(`${date}T12:00:00+08:00`);
    const event = workdayMeal();
    assert.equal(mealBookingStatus(event, now), "可預訂");
    assert.equal(validPickupTime("2026-11-09T12:00", event, now), true);
  });
}

test("週一國定假日後週二場次採後端窗口，週一20:00精確截止", () => {
  const event = workdayMeal("2026-10-27", { booking_starts_at: "2026-10-23T00:00:00+08:00" });
  for (const date of ["2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"]) {
    assert.equal(mealBookingStatus(event, Date.parse(`${date}T12:00:00+08:00`)), "可預訂");
  }
  assert.equal(mealBookingStatus(event, Date.parse("2026-10-26T19:59:59+08:00")), "可預訂");
  assert.equal(mealBookingStatus(event, Date.parse("2026-10-26T20:00:00+08:00")), "已截止");
  assert.equal(mealBookingStatus(event, Date.parse("2026-10-27T00:00:00+08:00")), "已截止");
});

test("特殊週日場次尊重管理員開放時間，未開放與已停止都不能下單", () => {
  const event = workdayMeal("2026-11-08", { booking_policy: "special", booking_starts_at: "2026-11-05T10:00:00+08:00" });
  assert.equal(mealBookingStatus(event, Date.parse("2026-11-05T09:59:59+08:00")), "尚未開放");
  assert.equal(mealBookingStatus(event, Date.parse("2026-11-05T10:00:00+08:00")), "可預訂");
  assert.equal(mealBookingStatus({ ...event, status: "ordering_closed" }, Date.parse("2026-11-05T12:00:00+08:00")), "已截止");
});

test("供餐日期依實際公告排序，不虛構週末，保留特殊日期並排除取消草稿", () => {
  const monday = workdayMeal();
  const special = workdayMeal("2026-11-07", { booking_policy: "special" });
  const dates = mealBookingDates([monday, special, { ...monday, id: "dinner" },
    workdayMeal("2026-11-08", { status: "cancelled" }), workdayMeal("2026-11-10", { status: "draft" })], Date.parse("2026-11-06T12:00:00+08:00"));
  assert.deepEqual(dates, [{ date: "2026-11-07", status: "可預訂" }, { date: "2026-11-09", status: "可預訂" }]);
});

test("跨年場次與較晚開放日皆由後端窗口判斷", () => {
  const event = workdayMeal("2027-01-04", { booking_starts_at: "2026-12-31T00:00:00+08:00" });
  assert.equal(mealBookingStatus(event, Date.parse("2026-12-30T23:59:59+08:00")), "尚未開放");
  assert.equal(mealBookingStatus(event, Date.parse("2026-12-31T00:00:00+08:00")), "可預訂");
  assert.equal(mealBookingStatus(event, Date.parse("2027-01-03T20:00:00+08:00")), "已截止");
});

test("後端暫停舊假日場次的新訂單時，日期與時段不再供選且不能報價", () => {
  const blocked = mealEvent({ new_orders_blocked_reason: "本場次不開放新的預訂" });
  assert.equal(mealOrderingOpen(blocked, NOW), false);
  assert.equal(mealBookingStatus(blocked, NOW), "暫停預訂");
  assert.deepEqual(pickupTimeOptions(blocked, NOW), []);
  assert.deepEqual(mealPickupChoices([blocked], "2026-09-18", NOW), []);
  assert.deepEqual(mealBookingDates([blocked], NOW), []);
  assert.deepEqual(mealBookingDates([blocked, mealEvent({ id: "allowed", new_orders_blocked_reason: null })], NOW), [{ date: "2026-09-18", status: "可預訂" }]);
});

test("手動當日訂餐只保留備餐時限前的一般便當時段，會餐沿用前一日截單", () => {
  const date = "2026-11-07";
  const at = (clock) => Date.parse(`${date}T${clock}+08:00`);
  const event = workdayMeal(date, {
    booking_policy: "special",
    booking_ends_at: `${date}T11:45:00+08:00`,
    same_day_preparation_minutes: 60,
  });
  const ordinaryEvent = workdayMeal(date, { id: "ordinary" });

  assert.equal(validPickupTime(`${date}T11:30`, event, at("10:29:59")), true);
  assert.equal(validPickupTime(`${date}T11:30`, event, at("10:30:00")), false);
  assert.deepEqual(pickupTimeOptions(event, at("10:30:00")).map(({ label }) => label), ["12:00", "12:30", "13:00"]);
  assert.equal(mealBookingStatus(event, at("10:30:00")), "可預訂");
  assert.equal(mealOrderingOpen(event, Date.parse("2026-11-06T19:59:59+08:00"), "business"), true);
  assert.equal(mealOrderingOpen(event, Date.parse("2026-11-06T20:00:00+08:00"), "business"), false);
  assert.equal(mealBookingStatus(event, at("10:30:00"), "business"), "已截止");
  assert.deepEqual(mealBookingDates([ordinaryEvent, event], at("10:30:00")), [{ date, status: "可預訂" }]);
  assert.deepEqual(mealPickupChoices([ordinaryEvent, event], date, at("10:30:00")).map(({ eventId, label }) => [eventId, label]), [
    [event.id, "12:00"], [event.id, "12:30"], [event.id, "13:00"],
  ]);

  assert.equal(mealBookingStatus(event, at("11:45:00")), "已截止");
  assert.deepEqual(pickupTimeOptions(event, at("11:45:00")), []);
  assert.deepEqual(mealBookingDates([ordinaryEvent, event], at("11:45:00")), []);
  assert.deepEqual(mealBookingDates([{ ...event, offerings: [{ is_active: true, available_quantity: 0 }] }], at("10:30:00")), []);
});

test("週六會餐限定場次依實際截止顯示，無一般或當日預訂", () => {
  const date = "2026-11-07";
  const event = workdayMeal(date, { booking_policy: "special", business_only: true, booking_ends_at: `${date}T08:00:00+08:00` });
  const cutoff = Date.parse("2026-11-06T20:00:00+08:00");
  const before = cutoff - 1;
  assert.equal(mealOrderingOpen(event, before, "standard"), false);
  assert.deepEqual(pickupTimeOptions(event, before, "standard"), []);
  assert.equal(mealOrderingOpen(event, before, "business"), true);
  assert.equal(mealOrderingOpen(event, cutoff, "business"), false);
  assert.equal(mealOrderingOpen({ ...event, booking_ends_at: "2026-11-06T18:00:00+08:00" }, before, "business"), false);
  assert.ok(mealPickupChoices([event], date, before).every(choice => choice.status === "可預訂" && choice.businessOnly));
  assert.deepEqual(mealBookingDates([event], cutoff), [{ date, status: "已截止" }]);
  assert.deepEqual(mealBookingDates([event], Date.parse(`${date}T09:00:00+08:00`)), []);
});
