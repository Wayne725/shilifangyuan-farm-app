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
const uiSource = await readFile(
  new URL("../src/components/ui.tsx", import.meta.url),
  "utf8",
);
const tabsSource = await readFile(
  new URL("../app/(tabs)/_layout.tsx", import.meta.url),
  "utf8",
);
const membersSource = await readFile(
  new URL("../app/(tabs)/members.tsx", import.meta.url),
  "utf8",
);
const mealsSource = await readFile(
  new URL("../app/(tabs)/meals.tsx", import.meta.url),
  "utf8",
);
const mealOrderSource = await readFile(
  new URL("../app/meal-order/[id].tsx", import.meta.url),
  "utf8",
);
const checkoutSource = await readFile(
  new URL("../app/checkout.tsx", import.meta.url),
  "utf8",
);
const groupBuySource = await readFile(
  new URL("../app/(tabs)/group-buy.tsx", import.meta.url),
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

test("提供生活消費與社務系統雙工作區及各自五個底部入口", () => {
  assert.match(uiSource, /生活消費/);
  assert.match(uiSource, /社務系統/);
  for (const route of [
    "home",
    "group-buy",
    "meals",
    "orders",
    "profile",
    "social-home",
    "members",
    "activities",
    "member-proposals",
    "social-profile",
  ]) {
    assert.match(tabsSource, new RegExp(`name="${route}"`));
  }
  assert.match(tabsSource, /name="cart".*href: null/s);
});

test("商品、團購與便當共用 4:3 CatalogCard", () => {
  assert.match(uiSource, /export function CatalogCard/);
  assert.match(uiSource, /aspectRatio: 4 \/ 3/);
  assert.match(mealsSource, /<CatalogCard/);
  assert.match(groupBuySource, /<CatalogCard/);
});

test("便當預購包含場次、訂單與取餐 QR", () => {
  assert.match(apiSource, /\/v1\/meal-events/);
  assert.match(apiSource, /createMealOrder/);
  assert.match(mealsSource, /我的便當/);
  assert.match(mealOrderSource, /name="qr-code"/);
  assert.match(mealOrderSource, /六位取餐碼/);
});

test("社務前端包含入社警告、活動、社員提案與管理審核", () => {
  assert.match(membersSource, /Sandbox 禁止上傳真實證件/);
  assert.match(apiSource, /\/v1\/membership\/application/);
  assert.match(apiSource, /\/v1\/activities/);
  assert.match(apiSource, /\/v1\/member-proposals/);
  assert.match(adminSource, /訂單與物流/);
  assert.match(adminSource, /入社申請/);
  assert.match(adminSource, /社員治理提案/);
});

test("一般訂單與團購結帳皆可選擇綠界物流", async () => {
  const campaignSource = await readFile(
    new URL("../app/campaign/[id].tsx", import.meta.url),
    "utf8",
  );
  assert.match(checkoutSource, /綠界物流配送/);
  assert.match(checkoutSource, /home_delivery/);
  assert.match(campaignSource, /綠界物流/);
  assert.match(apiSource, /fulfillment_method/);
  assert.match(apiSource, /logistics_provider/);
});

test("便當與社員活動展示素材已接入圖片映射", () => {
  assert.match(imageSource, /assets\/meals\/taiwanese-lunchbox\.png/);
  assert.match(imageSource, /assets\/community\/member-hike\.png/);
  assert.match(demoSource, /image_key: "meal-lunchbox"/);
  assert.match(demoSource, /image_key: "member-hike"/);
});
