import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
async function moduleUrl(path) {
  let source = await readFile(new URL(path, import.meta.url), "utf8");
  if (source.includes('from "./meal-time"')) {
    source = source.replace('from "./meal-time"', `from "${await moduleUrl("../web/src/lib/meal-time.ts")}"`);
  }
  if (source.includes('from "./browser-storage"')) {
    source = source.replace('from "./browser-storage"', `from "${await moduleUrl("../web/src/lib/browser-storage.ts")}"`);
  }
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  });
  return `data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`;
}
const { emptyMealDraft, readMealDraft, writeMealDraft, clearMealDraft, reconcileMealDraft, mealDraftWasAdjusted } = await import(await moduleUrl("../web/src/lib/meal-draft.ts"));
const NOW = Date.parse("2026-10-08T10:00:00+08:00");

function storage() {
  const entries = new Map();
  return {
    entries,
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value),
    removeItem: (key) => entries.delete(key),
  };
}

function event(overrides = {}) {
  return {
    id: "lunch", service_date: "2026-10-09",
    pickup_starts_at: "2026-10-09T11:00:00+08:00", pickup_ends_at: "2026-10-09T13:00:00+08:00",
    offerings: [{
      id: "chicken", is_active: true, available_quantity: 8,
      option_groups: [{
        id: "main", is_active: true, min_selections: 1, max_selections: 1,
        options: [{ id: "rice", is_active: true }, { id: "vegetables", is_active: true }, { id: "retired", is_active: false }],
      }],
    }],
    ...overrides,
  };
}

function selection(overrides = {}) {
  return {
    quantities: { chicken: 2 }, selections: { chicken: { main: ["rice"] } },
    pickupAt: "2026-10-09T11:30", pickupLocationId: "", needsUtensils: false, ...overrides,
  };
}

test("離開後重新讀取，保留餐點份數、客製選項與取餐時間", () => {
  const browserStorage = storage();
  const original = selection();
  writeMealDraft("lunch", original, browserStorage);
  original.quantities.chicken = 7;
  original.selections.chicken.main[0] = "vegetables";
  const restored = reconcileMealDraft(readMealDraft("lunch", browserStorage), event(), NOW);
  assert.deepEqual(restored, selection());
});

test("會餐草稿重整保留訂餐方式，清空後回到一般且不保存統編資料", () => {
  const browserStorage = storage();
  writeMealDraft("lunch", { ...selection(), mealBookingType: "business", buyerTaxId: "15989995" }, browserStorage);
  const restored = reconcileMealDraft(readMealDraft("lunch", browserStorage), event(), NOW);
  assert.equal(restored.mealBookingType, "business");
  assert.equal(Object.hasOwn(restored, "buyerTaxId"), false);
  clearMealDraft("lunch", browserStorage);
  assert.equal(readMealDraft("lunch", browserStorage).mealBookingType, undefined);
});

test("午晚餐及不同日期各自保存，清除一場不影響其他場次", () => {
  const browserStorage = storage();
  writeMealDraft("lunch", selection(), browserStorage);
  const dinner = selection({ pickupAt: "2026-10-09T17:30" });
  writeMealDraft("dinner", dinner, browserStorage);
  clearMealDraft("lunch", browserStorage);
  assert.deepEqual(readMealDraft("lunch", browserStorage), emptyMealDraft());
  assert.deepEqual(readMealDraft("dinner", browserStorage), dinner);
  assert.deepEqual(readMealDraft("tomorrow-lunch", browserStorage), emptyMealDraft());
});

test("返回時按目前庫存調整份數，移除停用或已刪除的餐點及選項", () => {
  const menu = event();
  menu.offerings[0].available_quantity = 1;
  menu.offerings.push({ id: "retired-meal", is_active: false, available_quantity: 9, option_groups: [] });
  const draft = selection({
    quantities: { chicken: 8, "retired-meal": 2, "deleted-meal": 1 },
    selections: { chicken: { main: ["rice", "rice", "retired", "missing", "vegetables"], deleted: ["missing"] } },
  });
  assert.deepEqual(reconcileMealDraft(draft, menu, NOW), selection({ quantities: { chicken: 1 } }));
  menu.offerings[0].available_quantity = 0;
  assert.deepEqual(reconcileMealDraft(draft, menu, NOW), selection({ quantities: {}, selections: {} }));
});

test("跨到取餐當天、時段更改或不在半小時清單時，需要重新選時間", () => {
  const draft = selection();
  const timePassed = reconcileMealDraft(draft, event(), Date.parse("2026-10-09T00:00:00+08:00"));
  assert.equal(timePassed.pickupAt, "");
  assert.deepEqual(timePassed.quantities, draft.quantities);
  const moved = event({ pickup_starts_at: "2026-10-09T12:00:00+08:00" });
  assert.equal(reconcileMealDraft(draft, moved, NOW).pickupAt, "");
  assert.equal(reconcileMealDraft(selection({ pickupAt: "2026-10-09T11:31" }), event(), NOW).pickupAt, "");
  assert.equal(reconcileMealDraft(selection({ pickupAt: "invalid" }), event(), NOW).pickupAt, "");
});

test("空草稿會移除暫存，訂單成立後清除再返回不恢復舊選餐", () => {
  const browserStorage = storage();
  writeMealDraft("lunch", selection(), browserStorage);
  writeMealDraft("lunch", emptyMealDraft(), browserStorage);
  assert.equal(browserStorage.entries.size, 0);
  writeMealDraft("lunch", selection(), browserStorage);
  clearMealDraft("lunch", browserStorage);
  assert.deepEqual(readMealDraft("lunch", browserStorage), emptyMealDraft());
});

test("只保存選餐欄位，不保存發票或報價；損壞資料及禁用儲存不阻止選餐", () => {
  const browserStorage = storage();
  writeMealDraft("lunch", { ...selection(), invoicePreference: { buyerEmail: "private@example.test" }, quote: { amount_total: 1 } }, browserStorage);
  const saved = JSON.parse([...browserStorage.entries.values()][0]);
  assert.deepEqual(Object.keys(saved).sort(), ["needsUtensils", "pickupAt", "pickupLocationId", "quantities", "selections"]);
  const key = [...browserStorage.entries.keys()][0];
  browserStorage.setItem(key, "bad JSON");
  assert.deepEqual(readMealDraft("lunch", browserStorage), emptyMealDraft());
  browserStorage.setItem(key, JSON.stringify({ quantities: { chicken: 1.5, beef: "2", tofu: -1, rice: 999 }, selections: { rice: { main: ["plain", 9, "plain"], bad: 1 }, bad: [] }, pickupAt: 10 }));
  assert.deepEqual(readMealDraft("lunch", browserStorage), {
    quantities: { rice: 99 }, selections: { rice: { main: ["plain"] } }, pickupAt: "", pickupLocationId: "", needsUtensils: false,
  });
  const blocked = {
    getItem() { throw new Error("storage disabled"); },
    setItem() { throw new Error("storage disabled"); },
    removeItem() { throw new Error("storage disabled"); },
  };
  assert.deepEqual(readMealDraft("lunch", blocked), emptyMealDraft());
  assert.doesNotThrow(() => writeMealDraft("lunch", selection(), blocked));
  assert.doesNotThrow(() => clearMealDraft("lunch", blocked));
});

test("取餐點與餐具需求返回後保留，清單載入前不丟失原選擇", () => {
  const browserStorage = storage();
  const original = selection({ pickupLocationId: "west", needsUtensils: true });
  writeMealDraft("lunch", original, browserStorage);
  const restored = readMealDraft("lunch", browserStorage);
  assert.deepEqual(reconcileMealDraft(restored, event(), NOW), original);
  assert.deepEqual(reconcileMealDraft(restored, event(), NOW, [{ id: "west", is_active: true }]), original);
  assert.equal(mealDraftWasAdjusted(original, original), false);
});

test("停用或移除的取餐點切換至啟用點並提示，無啟用點回到場次地點", () => {
  const original = selection({ pickupLocationId: "west", needsUtensils: true });
  const locations = [{ id: "west", is_active: false }, { id: "east", is_active: true }];
  const adjusted = reconcileMealDraft(original, event(), NOW, locations);
  assert.equal(adjusted.pickupLocationId, "east");
  assert.equal(adjusted.needsUtensils, true);
  assert.equal(mealDraftWasAdjusted(original, adjusted), true);
  const fallback = reconcileMealDraft(original, event(), NOW, []);
  assert.equal(fallback.pickupLocationId, "");
  assert.equal(fallback.needsUtensils, true);
  assert.equal(reconcileMealDraft(selection(), event(), NOW, locations).pickupLocationId, "east");
});

test("舊草稿與格式錯誤的新增欄位使用不提供餐具預設值", () => {
  const browserStorage = storage();
  browserStorage.setItem("slf_meal_draft_v1:lunch", JSON.stringify({ quantities: {}, selections: {}, pickupAt: "" }));
  assert.deepEqual(readMealDraft("lunch", browserStorage), emptyMealDraft());
  browserStorage.setItem("slf_meal_draft_v1:lunch", JSON.stringify({ pickupLocationId: 17, needsUtensils: "false" }));
  assert.deepEqual(readMealDraft("lunch", browserStorage), emptyMealDraft());
  writeMealDraft("lunch", { ...emptyMealDraft(), needsUtensils: true }, browserStorage);
  assert.equal(readMealDraft("lunch", browserStorage).needsUtensils, true);
});

test("菜單或時段變更會提示調整，正常移除餐點及取消選項不誤報", () => {
  const original = selection();
  assert.equal(mealDraftWasAdjusted(original, reconcileMealDraft(original, event(), NOW)), false);
  assert.equal(mealDraftWasAdjusted(original, selection({ quantities: { chicken: 1 } })), true);
  assert.equal(mealDraftWasAdjusted(original, selection({ selections: {} })), true);
  assert.equal(mealDraftWasAdjusted(original, selection({ pickupAt: "" })), true);
  const removed = selection({ quantities: { chicken: 0 } });
  assert.equal(mealDraftWasAdjusted(removed, reconcileMealDraft(removed, event(), NOW)), false);
  const deselected = selection({ selections: { chicken: { main: [] } } });
  assert.equal(mealDraftWasAdjusted(deselected, reconcileMealDraft(deselected, event(), NOW)), false);
});
