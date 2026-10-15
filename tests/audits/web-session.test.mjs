import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
const ts = createRequire(new URL("../../web/package.json", import.meta.url))("typescript");

async function loadApi(fetcher) {
  const source = await readFile(new URL("../../web/src/lib/api.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(
    source.replace("import.meta.env.VITE_API_BASE_URL", '""'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
  ).outputText;
  const exports = {};
  vm.runInNewContext(compiled, {
    exports,
    Headers,
    require(name) {
      assert.equal(name, "./http");
      return {
        fetchWithTimeout: fetcher,
        RequestTimeoutError: class extends Error {},
      };
    },
  });
  return exports;
}

test("暫時性服務失敗不得與沒有登入工作階段混為一談", async () => {
  const exports = await loadApi(async () => new Response("Service unavailable", { status: 503 }));
  exports.saveSession({ access_token: "isolated-test-token" });
  await assert.rejects(exports.restoreSession(), (error) => error.status === 503);
  assert.equal(exports.getAccessToken(), "isolated-test-token");
});

test("憑證失效的 401 仍必須清除登入", async () => {
  const api = await loadApi(async () => new Response(null, { status: 401 }));
  api.saveSession({ access_token: "old-token" });
  assert.equal(await api.restoreSession(), null);
  assert.equal(api.getAccessToken(), null);
});

test("網路暫時中斷後可再還原，保留 HttpOnly cookie 傳送方式", async () => {
  let unavailable = true;
  const api = await loadApi(async (_url, init) => {
    assert.equal(init.credentials, "include");
    if (unavailable) throw new TypeError("network failed");
    return Response.json({ access_token: "restored-token", user: { id: "buyer" } });
  });
  await assert.rejects(api.restoreSession(), (error) => error.status === 503);
  unavailable = false;
  assert.equal((await api.restoreSession()).access_token, "restored-token");
});

test("同一頁同時還原登入只發出一個 refresh 請求", async () => {
  let calls = 0;
  let release;
  const response = new Promise((resolve) => { release = resolve; });
  const api = await loadApi(async () => { calls += 1; return response; });
  const first = api.restoreSession();
  const second = api.restoreSession();
  release(Response.json({ access_token: "new-token" }));
  assert.equal((await first).access_token, "new-token");
  assert.equal((await second).access_token, "new-token");
  assert.equal(calls, 1);
});

test("付款 API 的 401 若遇到 refresh 503，不清除現有登入", async () => {
  const api = await loadApi(async (url) => new Response(null, {
    status: url === "/v1/auth/refresh" ? 503 : 401,
  }));
  api.saveSession({ access_token: "existing-token" });
  await assert.rejects(api.apiFetch("/v1/orders/order/payment-attempts", { method: "POST" }), (error) => error.status === 503);
  assert.equal(api.getAccessToken(), "existing-token");
});
