import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
const source = await readFile(new URL("../web/src/lib/meal-submission.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { mealSubmissionFingerprint } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);
const input = {
  eventId: "meal-event", couponCode: "", expectedTotal: 149,
  pickupAt: "2026-10-28T04:00:00.000Z", pickupLocationId: "store", needsUtensils: false,
  items: [{ offering_id: "meal", quantity: 1, option_ids: ["rice"] }],
  contactEmail: "buyer@example.test",
  invoicePreference: { buyerType: "personal", buyerEmail: "buyer@example.test", carrierType: "mobile_barcode", carrierValue: "/ABC1234" },
  paymentMethod: "online", kitchenNote: "",
};

test("儲存載具選項不改變便當重試識別，並相容舊版未帶選項的訂單", () => {
  const original = JSON.stringify({ userId: "buyer", ...input });
  assert.equal(mealSubmissionFingerprint("buyer", input), original);
  for (const saveCarrier of [false, true]) {
    const retry = { ...input, invoicePreference: { saveCarrier, ...input.invoicePreference } };
    assert.equal(mealSubmissionFingerprint("buyer", retry), original);
    assert.equal(mealSubmissionFingerprint("buyer", { ...input, invoicePreference: { ...input.invoicePreference, saveCarrier } }), original);
  }
});

test("餐點、取餐安排、發票及使用者改變仍產生不同送單識別", () => {
  const original = mealSubmissionFingerprint("buyer", input);
  for (const change of [
    { items: [{ offering_id: "meal", quantity: 2, option_ids: ["rice"] }] },
    { pickupAt: "2026-10-28T04:30:00.000Z" },
    { pickupLocationId: "other-store" }, { needsUtensils: true },
    { invoicePreference: { ...input.invoicePreference, carrierValue: "/DEF5678" } },
    { paymentMethod: "cash_on_pickup" },
  ]) assert.notEqual(mealSubmissionFingerprint("buyer", { ...input, ...change }), original);
  assert.notEqual(mealSubmissionFingerprint("other-buyer", input), original);
});

test("新增開票時機沿用舊送單識別，真正改開票日期才更換識別", () => {
  for (const [paymentMethod, invoiceHandling] of [["online", "on_payment"], ["cash_on_pickup", "external"]]) {
    const legacy = { ...input, paymentMethod };
    const legacyStored = JSON.stringify({ userId: "buyer", ...legacy });
    assert.equal(mealSubmissionFingerprint("buyer", { ...legacy, invoiceHandling }), legacyStored);
    assert.equal(mealSubmissionFingerprint("buyer", { ...legacy, invoiceHandling: undefined }), legacyStored);
    assert.notEqual(mealSubmissionFingerprint("buyer", { ...legacy, invoiceHandling: "pickup_day" }), legacyStored);
    if (paymentMethod === "cash_on_pickup") assert.notEqual(mealSubmissionFingerprint("buyer", { ...legacy, invoiceHandling: "on_payment" }), legacyStored);
  }
});
