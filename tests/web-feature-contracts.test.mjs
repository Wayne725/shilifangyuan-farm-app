import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";

async function readTree(directory) {
  const parts = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const url = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directory);
    if (entry.isDirectory()) parts.push(await readTree(url));
    if (entry.isFile() && /\.(ts|tsx|css)$/.test(entry.name)) parts.push(await readFile(url, "utf8"));
  }
  return parts.join("\n");
}

const source = await readTree(new URL("../web/src/", import.meta.url));
const router = await readFile(new URL("../web/src/router.tsx", import.meta.url), "utf8");
const commerce = await readFile(new URL("../web/src/lib/commerce.ts", import.meta.url), "utf8");
const styles = await readFile(new URL("../web/src/styles.css", import.meta.url), "utf8");
const shared = await readFile(new URL("../web/src/components/Shared.tsx", import.meta.url), "utf8");
const authFlows = await readFile(new URL("../web/src/pages/AuthFlowPages.tsx", import.meta.url), "utf8");

test("生活消費保留商品、團購、便當、付款與物流", () => {
  for (const marker of ["/v1/products", "/v1/group-campaigns", "/v1/meal-events", "beginCheckout", "beginGroupCheckout", "beginMealCheckout", "shippingEligibility", "selection-link"]) assert.match(source + commerce, new RegExp(marker.replaceAll("/", "\\/")));
});

test("便當端到端包含取餐 QR 與管理端核銷", () => {
  assert.match(source, /QRCodeSVG/);
  assert.match(source, /pickup-credential/);
  assert.match(source, /meal-events\/\$\{event\.id\}\/redeem/);
  assert.match(router, /path: "\/meal-orders"/);
  assert.match(router, /path: "\/admin\/meals"/);
});

test("管理工作台保留六個營運模組", () => {
  for (const path of ["/admin", "/admin/catalog", "/admin/groups", "/admin/meals", "/admin/finance", "/admin/social"]) assert.match(router, new RegExp(`path: "${path.replaceAll("/", "\\/")}"`));
  for (const endpoint of ["shipping-rates", "admin/refund", "points/adjustments", "surplus/dry-run", "tax-ledger.csv", "suppliers", "pickup-locations"]) assert.match(source, new RegExp(endpoint.replaceAll("/", "\\/")));
});

test("社務流程與入社文件仍由正式後端支援", () => {
  for (const marker of ["membership/application", "membership/documents", "member-proposals", "activities", "members/directory", "meetings", "wishes", "notifications"]) assert.match(source, new RegExp(marker.replaceAll("/", "\\/")));
});

test("正式 Web 採路由分包且不含 React Native 或行內樣式", () => {
  assert.match(router, /import\.meta\.glob/);
  assert.doesNotMatch(source, /from ["']react-native/);
  assert.doesNotMatch(source, /expo-router/);
  assert.doesNotMatch(source, /style=\{\{/);
  assert.doesNotMatch(source, /from ["']\.\/[^"']+Page["']/);
});

test("商店商品圖使用精簡比例且壞圖有一致備援", () => {
  assert.match(styles, /\.product-image-frame\s*\{[^}]*display:\s*block;[^}]*aspect-ratio:\s*4\s*\/\s*3;/s);
  assert.match(styles, /\.catalog-grid \.product-image-frame\s*\{[^}]*aspect-ratio:\s*16\s*\/\s*9;/s);
  assert.doesNotMatch(styles, /@media \(max-width:\s*1180px\)[\s\S]*?\.product-image-frame\s*\{[^}]*height:\s*250px;/s);
  assert.match(shared, /onError=\{replaceBrokenAsset\}/);
});

test("註冊入口區分既有社員認領與非社員帳號", () => {
  assert.match(authFlows, /既有社員註冊/);
  assert.match(authFlows, /非社員註冊/);
  assert.match(authFlows, /\/v1\/auth\/register-existing-member/);
  assert.match(source, /\/v1\/membership\/claim-existing/);
  assert.match(source, /\/v1\/admin\/member-roster/);
  assert.match(router, /search\.intent === "existing_member"/);
  assert.match(router, /search\.intent === "nonmember"/);
});
