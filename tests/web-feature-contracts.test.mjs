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
const invoiceFields = await readFile(new URL("../web/src/components/InvoicePreferenceFields.tsx", import.meta.url), "utf8");
const authContext = await readFile(new URL("../web/src/context/AuthContext.tsx", import.meta.url), "utf8");
const ordersPage = await readFile(new URL("../web/src/pages/OrdersPage.tsx", import.meta.url), "utf8");
const accountPage = await readFile(new URL("../web/src/pages/AccountPage.tsx", import.meta.url), "utf8");

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

test("社務流程由正式後端支援，入社不再收新證件", () => {
  for (const marker of ["membership/application", "member-proposals", "activities", "members/directory", "meetings", "wishes", "notifications"]) assert.match(source, new RegExp(marker.replaceAll("/", "\\/")));
  assert.doesNotMatch(source, /membership\/documents\/upload-url/);
});

test("社員提案與會議有獨立路由", async () => {
  const proposals = await readFile(new URL("../web/src/pages/GovernancePage.tsx", import.meta.url), "utf8");
  const meetings = await readFile(new URL("../web/src/pages/MeetingsPage.tsx", import.meta.url), "utf8");
  const social = await readFile(new URL("../web/src/pages/SocialPage.tsx", import.meta.url), "utf8");
  assert.match(router, /path: "\/meetings"/);
  assert.match(proposals, /\/v1\/member-proposals/);
  assert.doesNotMatch(proposals, /\/v1\/meetings/);
  assert.match(meetings, /\/v1\/meetings/);
  assert.doesNotMatch(meetings, /\/v1\/member-proposals/);
  assert.doesNotMatch(social, /share_count|share_capital_amount|share_certificate_number/);
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

test("首頁入口與長訂單編號維持響應式邊界", () => {
  assert.match(styles, /\.workspace-door\s*\{[^}]*justify-content:\s*flex-start;/s);
  assert.match(styles, /\.workspace-door strong\s*\{[^}]*min-height:/s);
  assert.match(styles, /\.order-number\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
  assert.match(styles, /@media \(max-width:\s*1180px\)[\s\S]*?\.workspace-passage\s*\{[^}]*grid-template-columns:\s*1fr 1fr;/s);
  assert.match(source, /className="order-number/);
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

test("個人與公司發票共用 Email 會員載具並在三種結帳顯示確認資料", () => {
  assert.match(invoiceFields, /Email 會員載具/);
  assert.match(invoiceFields, /發票通知 Email/);
  assert.match(invoiceFields, /value\.carrierType === "cloud"\s*\? 64 : 80/);
  assert.doesNotMatch(invoiceFields, /<strong>不使用載具<\/strong>/);
  assert.match(commerce, /invoice_buyer_email:\s*preference\.buyerEmail/);
  assert.equal((source.match(/<InvoicePreferenceSummary/g) || []).length, 3);
});

test("付款返回頁會先恢復登入工作階段，再決定是否顯示登入入口", () => {
  assert.match(authContext, /isAuthReady: boolean/);
  assert.match(authContext, /setIsAuthReady\(true\)/);
  for (const paymentReturnPage of [ordersPage, accountPage]) {
    assert.ok(
      paymentReturnPage.indexOf("if (!isAuthReady)")
        < paymentReturnPage.indexOf("if (!user)"),
    );
  }
});
