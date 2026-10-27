import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
const source = await readFile(new URL("../web/src/lib/login-navigation.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { lineLoginReturnTo } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);
test("LINE 首頁登入返回會員中心，明確呈現登入帳號", () => {
  assert.equal(lineLoginReturnTo("/"), "/account");
  assert.equal(lineLoginReturnTo("/?source=header#top"), "/account");
});
test("LINE 官方選單、結帳及便當選餐保留原返回位置", () => {
  for (const path of ["/shop", "/checkout?from=cart#summary", "/meals/example?pickup_at=2026-10-28T13%3A00", "/account"]) assert.equal(lineLoginReturnTo(path), path);
  assert.equal(lineLoginReturnTo(undefined), "/shop");
});
test("LINE 登入拒絕外站並避開登入循環", () => {
  for (const path of ["https://external.example", "//external.example", "/\\external.example", "/shop\n"]) assert.equal(lineLoginReturnTo(path), "/shop");
  assert.equal(lineLoginReturnTo("/login"), "/account");
});
