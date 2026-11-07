import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";

const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
const source = await readFile(new URL("../web/src/lib/commerce.ts", import.meta.url), "utf8");

test("送單 API 保留舊預設契約並送出真正的新開票時機", async () => {
  for (const [paymentMethod, invoiceHandling, expected] of [
    ["online", undefined, undefined], ["online", "on_payment", undefined], ["online", "pickup_day", "pickup_day"],
    ["cash_on_pickup", undefined, undefined], ["cash_on_pickup", "external", undefined],
    ["cash_on_pickup", "on_payment", "on_payment"], ["cash_on_pickup", "pickup_day", "pickup_day"],
  ]) {
    const calls = [], exports = {};
    const apiFetch = async (path, options) => {
      calls.push({ path, body: options?.body ? JSON.parse(options.body) : null });
      return path.endsWith("/payment-attempts") ? { payment_url: "/isolated-payment" } : { id: "synthetic-order", payment_method: paymentMethod };
    };
    vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { exports, require: () => ({ apiFetch }) });
    const result = await exports.beginMealCheckout({
      eventId: "event", pickupAt: "2026-11-18T03:00:00Z", items: [{ offering_id: "meal", quantity: 1, option_ids: [] }],
      contactEmail: "buyer@example.test", clientRequestId: "existing-retry-id", paymentMethod, invoiceHandling,
      invoicePreference: { buyerType: "personal", buyerEmail: "buyer@example.test", carrierType: "cloud" },
    });
    assert.equal(calls[0].body.invoice_handling, expected);
    assert.equal(Object.hasOwn(calls[0].body, "invoice_handling"), expected !== undefined);
    assert.equal(calls[0].body.client_request_id, "existing-retry-id");
    assert.equal(result.kind, paymentMethod === "online" ? "redirect" : "reserved");
    assert.equal(calls.length, paymentMethod === "online" ? 2 : 1);
    assert.equal(Object.hasOwn(calls[0].body, "invoice_carrier_type"), paymentMethod === "online" || expected !== undefined);
  }
});
