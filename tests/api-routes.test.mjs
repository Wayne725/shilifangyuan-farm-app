import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

/**
 * Guards the App/API seam. Every `/v1/...` path the client calls must exist in
 * the route manifest exported from FastAPI's OpenAPI schema
 * (`backend/scripts/export_openapi_paths.py`).
 *
 * This exists because the client and the API were written separately and eight
 * endpoints silently disagreed — the demo fallback hid every one of them.
 */

const apiSource = await readFile(
  new URL("../src/services/api.ts", import.meta.url),
  "utf8",
);
const backendRoutes = JSON.parse(
  await readFile(
    new URL("../src/services/api.routes.json", import.meta.url),
    "utf8",
  ),
);

const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

/**
 * Reduces a path to its shape so the three notations compare equal:
 * `${id}` (client), `{order_id}` (FastAPI) and `[id]` (Expo Router).
 */
function normalise(path) {
  return path
    .replace(/\$\{[^}]*\}/g, "{param}")
    .replace(/\{[^}]*\}/g, "{param}")
    .replace(/\[[^\]]*\]/g, "{param}")
    .replace(/\?.*$/, "");
}

const knownRoutes = backendRoutes.map((route) => {
  const [method, path] = route.split(" ");
  return { method, segments: normalise(path).split("/") };
});

/**
 * `{param}` matches any single segment on either side: the client interpolates
 * both ids (`${id}`) and literal action names (`${action}`) the same way.
 */
function matchesBackendRoute(method, path) {
  const segments = normalise(path).split("/");
  return knownRoutes.some(
    (route) =>
      route.method === method &&
      route.segments.length === segments.length &&
      route.segments.every(
        (segment, index) =>
          segment === segments[index] ||
          segment === "{param}" ||
          segments[index] === "{param}",
      ),
  );
}

/** Collects every request<...>("path", { method }) call site in the client. */
function collectCalls(source) {
  const calls = [];
  const pattern = /request<[^>]*>\(\s*(`[^`]*`|"[^"]*")([\s\S]{0,220}?)\)/g;
  let match;
  while ((match = pattern.exec(source)) !== null) {
    const path = match[1].slice(1, -1);
    if (!path.startsWith("/v1") && !path.startsWith("/payments")) continue;
    const methodMatch = /method:\s*"(GET|POST|PUT|PATCH|DELETE)"/.exec(
      match[2],
    );
    calls.push({
      method: methodMatch ? methodMatch[1] : "GET",
      path,
      index: match.index,
    });
  }
  return calls;
}

const calls = collectCalls(apiSource);

test("client 呼叫的每一個 API 路徑都存在於後端路由表", () => {
  assert.ok(calls.length > 30, `只解析到 ${calls.length} 個呼叫，解析器可能失效`);
  const missing = calls
    .filter((call) => !matchesBackendRoute(call.method, call.path))
    .map((call) => `${call.method} ${call.path}`);
  assert.deepEqual(
    [...new Set(missing)],
    [],
    "以下呼叫在後端不存在（請更新 api.ts 或重新匯出路由表）",
  );
});

test("路由表本身是最新的匯出格式", () => {
  assert.ok(Array.isArray(backendRoutes) && backendRoutes.length > 100);
  for (const route of backendRoutes) {
    const [method] = route.split(" ");
    assert.ok(
      METHODS.includes(method),
      `路由表格式錯誤：${route}`,
    );
  }
});

/**
 * The API redirects the browser back into the App after ECPay. Those paths are
 * built from string literals in Python, so nothing else would notice when an
 * App route is renamed — `/orders/{id}` pointed at a route that never existed.
 */
const backendRedirectTargets = [
  "/orders",
  "/order/{id}",
  "/members",
  "/verify-email",
  "/reset-password",
];

test("後端導回的 Web 路徑都對應到實際存在的 App 路由", async () => {
  const { readdir } = await import("node:fs/promises");
  const appDir = new URL("../app/", import.meta.url);

  async function collectRoutes(dir, prefix = "") {
    const routes = [];
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      if (entry.name.startsWith("+")) continue;
      if (entry.isDirectory()) {
        // Parenthesised groups do not appear in the URL.
        const segment = entry.name.startsWith("(") ? prefix : `${prefix}/${entry.name}`;
        routes.push(
          ...(await collectRoutes(new URL(`${entry.name}/`, dir), segment)),
        );
      } else if (entry.name.endsWith(".tsx")) {
        const base = entry.name.replace(/\.tsx$/, "");
        if (base === "_layout") continue;
        routes.push(base === "index" ? prefix || "/" : `${prefix}/${base}`);
      }
    }
    return routes;
  }

  const routes = new Set((await collectRoutes(appDir)).map(normalise));
  const missing = backendRedirectTargets.filter(
    (target) => !routes.has(normalise(target)),
  );
  assert.deepEqual(missing, [], "後端會導向不存在的 App 路由");
});
