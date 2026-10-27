import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
const source = await readFile(new URL("../web/src/lib/invoice-carrier.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { saveInvoiceCarrierIfRequested } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);
const mobile = { buyerType: "personal", buyerEmail: "buyer@example.test", carrierType: "mobile_barcode", carrierValue: "/NEW1234" };

test("未勾選、取消勾選及改用 Email 均不變更已存帳號載具", async () => {
  const calls = [];
  for (const preference of [mobile, { ...mobile, saveCarrier: false }, { ...mobile, saveCarrier: true, carrierType: "cloud" }]) {
    await saveInvoiceCarrierIfRequested(preference, "/OLD1234", async value => { calls.push(value); });
  }
  assert.deepEqual(calls, []);
});

test("只保存明確勾選的有效條碼，與已存值相同不重複送出", async () => {
  const calls = [];
  const save = async value => { calls.push(value); };
  await saveInvoiceCarrierIfRequested({ ...mobile, carrierValue: " /new1234 ", saveCarrier: true }, "/OLD1234", save);
  await saveInvoiceCarrierIfRequested({ ...mobile, saveCarrier: true }, "/NEW1234", save);
  assert.deepEqual(calls, ["/NEW1234"]);
});

test("無效條碼不呼叫寫入，儲存失敗會傳回結帳阻擋而不吞錯", async () => {
  let calls = 0;
  const save = async () => { calls++; throw new Error("驗證服務失敗"); };
  await assert.rejects(saveInvoiceCarrierIfRequested({ ...mobile, carrierValue: "/SHORT", saveCarrier: true }, null, save), /請輸入正確/);
  assert.equal(calls, 0);
  await assert.rejects(saveInvoiceCarrierIfRequested({ ...mobile, saveCarrier: true }, null, save), /驗證服務失敗/);
  assert.equal(calls, 1);
});
