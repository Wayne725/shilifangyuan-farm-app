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
const campaignDetailSource = await readFile(
  new URL("../app/campaign/[id].tsx", import.meta.url),
  "utf8",
);
const mealDetailSource = await readFile(
  new URL("../app/meal/[id].tsx", import.meta.url),
  "utf8",
);
const groupBuySource = await readFile(
  new URL("../app/(tabs)/group-buy.tsx", import.meta.url),
  "utf8",
);
const ordersSource = await readFile(
  new URL("../app/(tabs)/orders.tsx", import.meta.url),
  "utf8",
);
const orderDetailSource = await readFile(
  new URL("../app/order/[id].tsx", import.meta.url),
  "utf8",
);
const authSource = await readFile(
  new URL("../src/store/AuthContext.tsx", import.meta.url),
  "utf8",
);
const typesSource = await readFile(
  new URL("../src/types.ts", import.meta.url),
  "utf8",
);
const socialProfileSource = await readFile(
  new URL("../app/(tabs)/social-profile.tsx", import.meta.url),
  "utf8",
);
const socialHomeSource = await readFile(
  new URL("../app/(tabs)/social-home.tsx", import.meta.url),
  "utf8",
);
const adminCooperativeSource = await readFile(
  new URL("../app/admin-cooperative.tsx", import.meta.url),
  "utf8",
);
const workspaceSource = await readFile(
  new URL("../src/store/WorkspaceContext.tsx", import.meta.url),
  "utf8",
);
const legacyCooperativeSource = await readFile(
  new URL("../app/cooperative.tsx", import.meta.url),
  "utf8",
);
const verifyEmailSource = await readFile(
  new URL("../app/verify-email.tsx", import.meta.url),
  "utf8",
);
const cartContextSource = await readFile(
  new URL("../src/store/CartContext.tsx", import.meta.url),
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
    assert.match(workspaceSource, new RegExp(`"/${route}"`));
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
  assert.match(mealOrderSource, /pickup_qr_payload/);
  assert.doesNotMatch(mealOrderSource, /name="qr-code"/);
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

test("管理端可建立便當場次並依名單記錄活動出席", () => {
  assert.match(apiSource, /\/v1\/admin\/meals/);
  assert.match(apiSource, /adminCreateMealEvent/);
  assert.match(apiSource, /\/v1\/admin\/activities\/\$\{id\}\/registrations/);
  assert.match(apiSource, /adminMarkActivityAttendance/);
  assert.match(adminSource, /新增便當餐點/);
  assert.match(adminSource, /建立草稿場次/);
  assert.match(adminSource, /查看報名名單/);
  assert.match(adminSource, /確認社員簽到/);
  assert.match(adminSource, /標記未出席/);
});

test("社員可撤回申請、維護自願公開名錄，管理端操作依會籍狀態顯示", () => {
  assert.match(membersSource, /withdrawMembershipApplication/);
  assert.match(membersSource, /撤回入社申請/);
  assert.match(membersSource, /updateMemberDirectory/);
  assert.match(membersSource, /公開給其他有效社員/);
  assert.match(apiSource, /\/v1\/members\/me\/directory/);
  assert.match(apiSource, /\/v1\/admin\/members/);
  assert.match(adminSource, /membershipActionsFor/);
  assert.match(adminSource, /\["submitted", "needs_revision"\]\.includes/);
  assert.match(adminSource, /application\.status === "submitted"/);
  assert.match(adminSource, /share-capital-return/);
  assert.match(adminSource, /目前登入的管理員會籍不可/);
  assert.match(apiSource, /deleteMembershipDocument/);
  assert.match(apiSource, /adminMembershipDocumentDownloadUrl/);
  assert.match(adminSource, /審查私密資料/);
  assert.match(adminSource, /每次檢視證件都會寫入稽核紀錄/);
  assert.match(membersSource, /canPayMembershipCharges/);
  assert.match(membersSource, /wasMembershipActivated/);
});

test("入社付款返回後會輪詢會籍並同步社員價格", () => {
  assert.match(authSource, /refreshUser: \(\) => Promise<User \| null>/);
  assert.match(authSource, /const refreshUser = useCallback/);
  assert.match(authSource, /latest\.user\.id !== expectedUserId/);
  assert.match(authSource, /previousUserId !== nextUserId/);
  assert.match(authSource, /queryClient\.clear\(\)/);
  assert.match(membersSource, /refetchInterval/);
  assert.match(membersSource, /paymentSyncUntil > Date\.now\(\)/);
  assert.match(membersSource, /if \(!params\.payment\) return/);
  assert.match(membersSource, /params\.payment === "paid"/);
  assert.match(membersSource, /status === "pending_payment"/);
  assert.match(membersSource, /auth-membership-sync/);
  assert.match(membersSource, /membershipIdentityNeedsSync/);
  assert.doesNotMatch(
    membersSource,
    /membership\.data\?\.status === "trainee"[\s\S]{0,120}user\?\.membership_type === "nonmember"/,
  );
  assert.match(membersSource, /重新同步社員資格/);
});

test("驗證信重新寄送文案只承諾排隊與驗證碼效期", () => {
  assert.match(verifyEmailSource, /10 分鐘效期內/);
  assert.match(verifyEmailSource, /任一驗證成功後全部失效/);
  assert.doesNotMatch(verifyEmailSource, /新信送達/);
  assert.match(apiSource, /10 分鐘效期內/);
  assert.doesNotMatch(apiSource, /新信送達/);
});

test("前端會籍契約區分一般買家、實習社員與正式社員編號", () => {
  assert.match(typesSource, /"nonmember" \| "trainee" \| "member"/);
  assert.match(typesSource, /customer_number: string \| null/);
  assert.match(typesSource, /trainee_number\?: string \| null/);
  assert.match(typesSource, /\| "trainee"\n  \| "active"/);
  assert.match(membersSource, /實習社員編號/);
  assert.match(membersSource, /正式社員編號/);
  assert.match(membersSource, /一般買家編號/);
});

test("入社申請送出後即可付款，不以管理員審核作為前置", () => {
  assert.doesNotMatch(membersSource, /status === "approved"/);
  assert.match(membersSource, /送出申請後即可繳交入社費與股金/);
  assert.match(apiSource, /createPendingDemoMembership\(user\)/);
});

test("實習社員享社員價但正式社務仍只開放正式社員", () => {
  assert.match(membersSource, /membership_type === "trainee"/);
  assert.match(membersSource, /等待管理員轉為正式社員/);
  assert.match(socialHomeSource, /hasFormalMemberAccess/);
  assert.match(socialHomeSource, /實習社員目前享有社員價/);
});

test("管理員可把實習社員手動轉為正式社員", () => {
  assert.match(apiSource, /\/v1\/admin\/members\/\$\{id\}\/activate/);
  assert.match(adminSource, /轉為正式社員/);
  assert.match(adminSource, /trainee_number/);
});

test("社務第五個底部入口為更多並拆分正式社員服務", () => {
  assert.match(tabsSource, /name="social-profile"[\s\S]*title: "更多"/);
  for (const route of [
    "social-account",
    "social-points",
    "social-wishes",
    "social-meetings",
    "social-surplus",
  ]) {
    assert.match(tabsSource, new RegExp(`name="${route}"`));
  }
  for (const label of ["個人資料", "積點與徽章", "願望", "會議", "結餘分配"]) {
    assert.match(socialProfileSource, new RegExp(label));
  }
  assert.doesNotMatch(socialHomeSource, /合作教育/);
  assert.doesNotMatch(socialProfileSource, /合作教育/);
  assert.doesNotMatch(apiSource, /\/v1\/education/);
  assert.match(legacyCooperativeSource, /Redirect href="\/\(tabs\)\/members"/);
});

test("銷售介面分開顯示三類成交金額與占比", () => {
  assert.match(adminCooperativeSource, /一般買家銷售/);
  assert.match(adminCooperativeSource, /實習社員銷售/);
  assert.match(adminCooperativeSource, /正式社員銷售/);
  assert.match(adminCooperativeSource, /trainee_revenue/);
  assert.match(adminCooperativeSource, /member_revenue/);
});

test("綠界付款或物流返回遇冷啟動時會重試且保留手動查詢", () => {
  assert.match(ordersSource, /const resolveReturn = async/);
  assert.match(ordersSource, /attempt < 7/);
  assert.match(ordersSource, /setTimeout/);
  assert.match(ordersSource, /重新查詢訂單/);
  assert.ok(
    ordersSource.indexOf("await queryClient.fetchQuery") <
      ordersSource.indexOf("handledReturn.current = returnKey"),
    "必須成功取得訂單後才能把返回流程標成已處理",
  );
});

test("管理員可不經投票直接建立正式團購", () => {
  assert.match(apiSource, /createCampaign\(input:/);
  assert.match(apiSource, /request<unknown>\("\/v1\/group-campaigns"/);
  assert.match(adminSource, /直接建立正式團購/);
  assert.match(adminSource, /label="直接開團"/);
});

test("一般訂單與團購結帳皆可選擇綠界物流", async () => {
  assert.match(checkoutSource, /綠界物流配送/);
  assert.match(checkoutSource, /home_delivery/);
  assert.match(campaignDetailSource, /綠界物流/);
  assert.match(apiSource, /fulfillment_method/);
  assert.match(apiSource, /logistics_provider/);
});

test("訂單建立後付款或物流失敗不會重複建立一般與團購訂單", () => {
  for (const source of [checkoutSource, campaignDetailSource]) {
    assert.match(
      source,
      /submittedOrderId\s*\?\s*draftOrder\.data \?\? \(await api\.order/,
    );
    assert.match(source, /setSubmittedOrderId\(order\.id\)/);
    assert.match(source, /draftOrderId \?\? null/);
    assert.match(source, /router\.setParams\(\{ order_id: order\.id \}\)/);
    assert.doesNotMatch(source, /setupFailed: true/);
  }
  assert.match(checkoutSource, /pathname: "\/order\/\[id\]"/);
  assert.match(checkoutSource, /return <LoadingState label="正在開啟訂單"/);
  assert.match(checkoutSource, /enabled: items\.length > 0 && !submittedOrderId/);
  assert.match(checkoutSource, /validDraftOrder\?\.items \?\? quote\.data\?\.items/);
  assert.match(checkoutSource, /draftOrder\.data\?\.order_kind === "regular"/);
  assert.doesNotMatch(
    checkoutSource,
    /if \(items\.length \|\| submit\.isPending\) return;/,
  );
  assert.match(orderDetailSource, /重新填寫物流資料/);
  assert.match(orderDetailSource, /order_id: order\.id/);
  assert.match(mealDetailSource, /setupFailed: true/);
  assert.match(mealDetailSource, /setup: "retry"/);
  assert.match(
    cartContextSource,
    /localStorage\?\.setItem\(CART_KEY, "\[\]"\)/,
  );
  assert.match(campaignDetailSource, /quoteCampaign/);
  assert.match(campaignDetailSource, /後端報價/);
});

test("便當與社員活動展示素材已接入圖片映射", () => {
  assert.match(imageSource, /assets\/meals\/taiwanese-lunchbox\.png/);
  assert.match(imageSource, /assets\/community\/member-hike\.png/);
  assert.match(demoSource, /image_key: "meal-lunchbox"/);
  assert.match(demoSource, /image_key: "member-hike"/);
});
