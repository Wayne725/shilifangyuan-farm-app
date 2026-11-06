import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";

const ts = createRequire(new URL("../../web/package.json", import.meta.url))("typescript");
const session = (id, token = `${id}-token`) => ({
  access_token: token,
  user: { id, user_role: "customer", membership_type: "member", membership_status: "active" },
});

async function loadApi(fetcher) {
  const source = await readFile(new URL("../../web/src/lib/api.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source.replace("import.meta.env.VITE_API_BASE_URL", '""'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, Headers, FormData,
    require(name) {
      assert.equal(name, "./http");
      return { fetchWithTimeout: fetcher, RequestTimeoutError: class extends Error {} };
    },
  });
  return exports;
}

test("same account refresh retries the original order request exactly once", async () => {
  const attempts = [];
  const api = await loadApi(async (url, init) => {
    if (url === "/v1/auth/refresh") return Response.json(session("a", "a-renewed"));
    attempts.push({ authorization: init.headers.get("Authorization"), body: init.body });
    return attempts.length === 1 ? new Response(null, { status: 401 }) : Response.json({ id: "one-order" });
  });
  api.saveSession(session("a"));
  assert.equal((await api.apiFetch("/v1/orders", { method: "POST", body: '{"quantity":1}' })).id, "one-order");
  assert.deepEqual(attempts, [
    { authorization: "Bearer a-token", body: '{"quantity":1}' },
    { authorization: "Bearer a-renewed", body: '{"quantity":1}' },
  ]);
});

test("an order started as A is never replayed using refreshed account B", async () => {
  const orderTokens = [];
  const api = await loadApi(async (url, init) => {
    if (url === "/v1/auth/refresh") return Response.json(session("b"));
    orderTokens.push(init.headers.get("Authorization"));
    return orderTokens.length === 1 ? new Response(null, { status: 401 }) : Response.json({ id: "wrong-owner-order" });
  });
  api.saveSession(session("a"));
  await assert.rejects(api.apiFetch("/v1/orders", { method: "POST", body: "{}" }), error => error.status === 409);
  assert.deepEqual(orderTokens, ["Bearer a-token"]);
});

test("a successful private response from A is discarded after switching to B", async () => {
  let release;
  const api = await loadApi(() => new Promise(resolve => { release = resolve; }));
  api.saveSession(session("a"));
  const pending = api.apiFetch("/v1/orders");
  api.saveSession(session("b"));
  release(Response.json([{ id: "a-private-order" }]));
  await assert.rejects(pending, error => error.status === 409);
});

test("logout wins over a refresh already in flight", async () => {
  let release;
  const api = await loadApi(() => new Promise(resolve => { release = resolve; }));
  api.saveSession(session("a"));
  const restoring = api.restoreSession();
  api.clearSession();
  release(Response.json(session("a", "stale-restored-token")));
  assert.equal(await restoring, null);
  assert.equal(api.getAccessToken(), null);
});

test("an old refresh denial cannot sign out a newly logged in account", async () => {
  let release;
  const api = await loadApi(() => new Promise(resolve => { release = resolve; }));
  api.saveSession(session("a"));
  const restoring = api.restoreSession();
  api.saveSession(session("b"));
  release(new Response(null, { status: 401 }));
  await restoring;
  assert.equal(api.getAccessToken(), "b-token");
});

test("a late retry denial cannot sign out newer credentials for the same account", async () => {
  let attempts = 0;
  let release;
  let retryStarted;
  const retrying = new Promise(resolve => { retryStarted = resolve; });
  const api = await loadApi(async (url) => {
    if (url === "/v1/auth/refresh") return Response.json(session("a", "a-refreshed"));
    if (++attempts === 1) return new Response(null, { status: 401 });
    return new Promise(resolve => { release = resolve; retryStarted(); });
  });
  api.saveSession(session("a"));
  const pending = api.apiFetch("/v1/orders");
  await retrying;
  api.saveSession(session("a", "a-new-login"));
  release(new Response(null, { status: 401 }));
  await assert.rejects(pending, error => error.status === 401);
  assert.equal(api.getAccessToken(), "a-new-login");
  assert.equal(attempts, 2);
});
