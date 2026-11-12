import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
const source = await readFile(new URL("../web/src/lib/canonical-entry.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { canonicalEntryUrl } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);
const legacy = "https://shilifangyuan-web.onrender.com";

test("LINE舊入口統一正式網域並保留選餐時間、查詢與錨點", () => {
  for (const path of ["/shop?tab=products", "/shop?tab=groups", "/meals/abc?pickup_at=2026-11-07T11%3A00#meal-order-summary", "/admin?section=orders"]) {
    assert.equal(canonicalEntryUrl(legacy + path), "https://shilifangyuan.com" + path);
  }
});
test("正式域名、開發與其他來源不導向，不形成循環", () => {
  for (const origin of ["https://shilifangyuan.com", "http://localhost:5173", "https://preview.example", "https://shilifangyuan-web.onrender.com.evil.example"]) {
    assert.equal(canonicalEntryUrl(origin + "/shop"), null);
  }
});
test("既有付款與身分回傳在原網域完成，不搬移授權憑證", () => {
  for (const path of ["/auth/line/callback?code=x&state=y", "/payments/return", "/orders?attempt_id=a&payment=success", "/meals/orders?payment=cancelled", "/verify-email?token=x", "/reset-password", "/shop?access_token=x", "/#id_token=x", "/checkout", "/cart"]) {
    assert.equal(canonicalEntryUrl(legacy + path), null);
  }
});
test("金流頁的會籍及活動返回連結保留原網域", () => {
  for (const path of [
    "/account?membership_charge_id=charge&attempt_id=attempt",
    "/activities/registrations?activity_charge_id=charge&attempt_id=attempt",
  ]) {
    assert.equal(canonicalEntryUrl(legacy + path), null);
  }
  assert.equal(canonicalEntryUrl(legacy + "/account"), "https://shilifangyuan.com/account");
});
test("舊網域仍有購物或選餐草稿時保留原流程", () => {
  assert.equal(canonicalEntryUrl(legacy + "/shop", true), null);
  assert.equal(canonicalEntryUrl(legacy + "/shop", false), "https://shilifangyuan.com/shop");
});
test("現金預訂成功清除草稿後，既有訂單落地仍留在登入網域", () => {
  const orderId = "11111111-1111-4111-8111-111111111111";
  for (const path of ["/meal-orders", "/orders"]) {
    const landing = `${legacy}${path}?${new URLSearchParams({ order_id: orderId })}`;
    assert.equal(canonicalEntryUrl(landing, false), null);
  }
});
test("訂單落地豁免只限精確頁面及非空 order_id", () => {
  for (const path of ["/shop?order_id=abc", "/orders/other?order_id=abc", "/meal-orders", "/orders?order_id=", "/meal-orders?order_id=%20%20"]) {
    assert.equal(canonicalEntryUrl(legacy + path, false), "https://shilifangyuan.com" + path);
  }
});
test("雙斜線路徑也不能改變固定目的網域", () => {
  assert.equal(new URL(canonicalEntryUrl(legacy + "//evil.example/shop")).origin, "https://shilifangyuan.com");
});
