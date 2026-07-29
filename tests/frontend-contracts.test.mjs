import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const demoSource = await readFile(
  new URL("../src/data/demoData.ts", import.meta.url),
  "utf8",
);
const apiSource = await readFile(
  new URL("../src/services/api.ts", import.meta.url),
  "utf8",
);
const imageSource = await readFile(
  new URL("../src/lib/images.ts", import.meta.url),
  "utf8",
);
const adminSource = await readFile(
  new URL("../app/admin.tsx", import.meta.url),
  "utf8",
);
const productSection = demoSource
  .split("export const demoProducts: Product[] = [")[1]
  .split("export const demoBundles")[0];
const createProductSection = apiSource
  .split("createProduct(input:")[1]
  .split("advanceOrder(")[0];

test("展示資料至少提供 12 項商品", () => {
  const productIds = productSection.match(/\n    id: "/g) ?? [];
  assert.ok(productIds.length >= 12);
});

test("雞蛋商品歸在蛋品而非加工品", () => {
  const eggBlocks = productSection.match(
    /\{\n    id: "[^"]+",[\s\S]*?\n  \},/g,
  )?.filter((block) => /name: ".*蛋"/.test(block));
  assert.ok(eggBlocks?.length);
  for (const block of eggBlocks) {
    assert.match(block, /category: "蛋品"/);
    assert.doesNotMatch(block, /category: "加工品"/);
  }
});

test("前端 API 合約包含付款、發票與通知端點", () => {
  assert.match(apiSource, /\/v1\/orders\/\$\{orderId\}\/payment-attempts/);
  assert.match(apiSource, /\/v1\/invoice-carriers\/mobile-barcode\/validate/);
  assert.match(apiSource, /\/v1\/notifications/);
  assert.match(apiSource, /method: "PATCH"/);
});

test("前端沒有使用舊的 tax_free 稅別", () => {
  assert.doesNotMatch(`${demoSource}\n${apiSource}`, /tax_free/);
  assert.match(demoSource, /tax_exempt/);
});

test("後端相對商品圖片會映射到 App 內建資源", () => {
  assert.match(imageSource, /startsWith\("\/assets\/products\/"\)/);
  assert.match(imageSource, /replace\(\/\\\.\[\^\.\]\+\$\/, ""\)/);
  assert.match(imageSource, /generic-product/);
  assert.match(imageSource, /images\["generic-product"\]/);
});

test("管理員可上架商品且一般展示清單隱藏下架品", () => {
  assert.match(apiSource, /createProduct\(input:/);
  assert.doesNotMatch(apiSource, /productSlug/);
  assert.doesNotMatch(createProductSection, /\bslug\b/);
  assert.match(
    apiSource,
    /demoState\.products\.filter\(\(product\) => product\.is_active\)/,
  );
  assert.match(adminSource, /上架新商品/);
  assert.match(adminSource, /productCategories/);
  assert.match(adminSource, /tax_exempt/);
  assert.match(adminSource, /memberPrice > nonmemberPrice/);
  assert.match(adminSource, /社員價不可高於非社員價/);
  assert.match(adminSource, /商品介紹/);
});

test("展示資料重設需要第二組確認碼", () => {
  assert.match(apiSource, /resetDemo\(confirmation: string\)/);
  assert.match(apiSource, /body: \{ confirmation \}/);
  assert.match(adminSource, /資料重設確認碼/);
  assert.match(adminSource, /resetConfirmation\.trim\(\)/);
});
