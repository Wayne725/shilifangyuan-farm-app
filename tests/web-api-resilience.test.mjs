import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const {
  RequestTimeoutError,
  fetchWithTimeout,
} = await import("../web/src/lib/http.ts");

const shared = await readFile(
  new URL("../web/src/components/Shared.tsx", import.meta.url),
  "utf8",
);
const shop = await readFile(
  new URL("../web/src/pages/ShopPage.tsx", import.meta.url),
  "utf8",
);

test("無回應的 API 會在期限內結束，不讓頁面永久載入", async () => {
  const neverResponds = () => new Promise(() => {});

  await assert.rejects(
    fetchWithTimeout("https://api.example.test/health", {}, {
      fetchImpl: neverResponds,
      timeoutMs: 10,
    }),
    RequestTimeoutError,
  );
});

test("呼叫端取消請求時保留原本的 AbortError", async () => {
  const controller = new AbortController();
  const waitsForAbort = (_input, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(init.signal.reason), {
      once: true,
    });
  });
  const request = fetchWithTimeout("https://api.example.test/products", {
    signal: controller.signal,
  }, {
    fetchImpl: waitsForAbort,
    timeoutMs: 100,
  });

  controller.abort(new DOMException("cancelled", "AbortError"));

  await assert.rejects(request, { name: "AbortError" });
});

test("商店錯誤狀態提供重試，商品與便當圖片都有壞圖備援", () => {
  assert.match(shared, /onAction\?: \(\) => void/);
  assert.match(shop, /onAction=\{\(\) => products\.refetch\(\)\}/);
  assert.match(shop, /onAction=\{\(\) => campaigns\.refetch\(\)\}/);
  assert.match(shop, /onAction=\{\(\) => meals\.refetch\(\)\}/);
  assert.match(shop, /onError=\{replaceBrokenAsset\}/);
});
