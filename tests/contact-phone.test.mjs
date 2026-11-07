import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
const source = await readFile(new URL("../web/src/lib/contact-phone.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText;
const { contactPhoneIsValid } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("聯絡電話相容空值、含空格手機與有區碼市話", () => {
  for (const value of ["", "  ", "0912345678", "0912 345 678", "02-23456789", "073456789"]) {
    assert.equal(contactPhoneIsValid(value), true, value);
  }
});

test("聯絡電話拒絕缺碼、超碼與非電話內容", () => {
  for (const value of ["091234567", "09123456789", "123456", "abc", "tel:0912345678", "09１２３４５６７８"]) {
    assert.equal(contactPhoneIsValid(value), false, value);
  }
});
