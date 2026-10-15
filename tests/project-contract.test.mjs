import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
const workflow = await readFile(new URL("../.github/workflows/ci.yml", import.meta.url), "utf8");
const deployment = await readFile(new URL("../docs/DEPLOYMENT.md", import.meta.url), "utf8");
const renderBlueprint = await readFile(new URL("../render.yaml", import.meta.url), "utf8");
const backendEnvExample = await readFile(new URL("../backend/.env.example", import.meta.url), "utf8");

test("根專案只以 Vite Web 作為正式前端", () => {
  assert.match(packageJson.scripts.dev, /--dir web dev/);
  assert.match(packageJson.scripts.build, /--dir web build/);
  assert.equal(packageJson.main, undefined);
  assert.equal(packageJson.dependencies, undefined);
});

test("CI 驗證 Vite、前端契約、後端與 migration", () => {
  assert.match(workflow, /pnpm build/);
  assert.match(workflow, /pnpm test/);
  assert.match(workflow, /pytest/);
  assert.match(workflow, /alembic downgrade base/);
  assert.doesNotMatch(workflow, /expo export/);
});

test("API 契約工作會先安裝前端解析器依賴", () => {
  const apiContractJob = workflow.match(/  api-contract:\n[\s\S]*$/)?.[0] ?? "";

  assert.match(apiContractJob, /pnpm\/action-setup@v4/);
  assert.match(apiContractJob, /pnpm install --frozen-lockfile/);
});

test("部署手冊保留私有 R2 與正式上線條件", () => {
  assert.match(deployment, /R2 Bucket CORS/);
  assert.match(deployment, /x-amz-meta-sha256/);
  assert.match(deployment, /正式營運上線條件/);
});

test("環境變數範例列出 Production 必須取代的綠界端點", () => {
  const providerEndpointVariables = [
    "ECPAY_PAYMENT_AIO_URL",
    "ECPAY_PAYMENT_QUERY_URL",
    "ECPAY_INVOICE_ISSUE_URL",
    "ECPAY_INVOICE_QUERY_URL",
    "ECPAY_INVOICE_BARCODE_URL",
    "ECPAY_LOGISTICS_SELECTION_URL",
    "ECPAY_LOGISTICS_UPDATE_TEMP_URL",
    "ECPAY_LOGISTICS_CREATE_URL",
    "ECPAY_LOGISTICS_QUERY_URL",
    "ECPAY_LOGISTICS_PRINT_URL",
  ];

  for (const variable of providerEndpointVariables) {
    assert.match(backendEnvExample, new RegExp(`^${variable}=`, "m"));
    assert.ok(deployment.includes(`\`${variable}\``));
  }
});

test("環境變數範例使用後端會拒絕的開發用密鑰", () => {
  assert.match(
    backendEnvExample,
    /^JWT_SECRET=change-this-development-jwt-secret$/m,
  );
  assert.match(
    backendEnvExample,
    /^RECONCILE_SECRET=change-this-development-reconcile-secret$/m,
  );
});

test("Render API 明確要求 Resend 寄信設定", () => {
  const apiService = renderBlueprint.match(/  - type: web[\s\S]*?\n  - type: web/)?.[0] ?? "";

  assert.match(apiService, /- key: RESEND_API_KEY\n\s+sync: false/);
  assert.match(apiService, /- key: EMAIL_FROM_EMAIL\n\s+sync: false/);
  assert.match(apiService, /- key: EMAIL_FROM_NAME\n\s+value: 十里方圓/);
});

test("Render 明確使用汎宇測試發票且不把憑證寫進 Git", () => {
  const apiService = renderBlueprint.match(/  - type: web[\s\S]*?\n  - type: web/)?.[0] ?? "";

  assert.match(apiService, /- key: INVOICE_PROVIDER\n\s+value: fanyu/);
  for (const key of [
    "FANYU_INVOICE_COMPANY_ID",
    "FANYU_INVOICE_USER_ID",
    "FANYU_INVOICE_AUTH_PASSWORD",
    "FANYU_INVOICE_API_KEY",
    "FANYU_INVOICE_SELLER_ID",
  ]) {
    assert.match(apiService, new RegExp(`- key: ${key}\\n\\s+sync: false`));
  }
  assert.match(apiService, /- key: FANYU_INVOICE_STAGE\n\s+value: "true"/);
  assert.match(
    apiService,
    /- key: FANYU_INVOICE_BASE_URL\n\s+value: https:\/\/webtest\.einvoice\.com\.tw\/einv/,
  );
  assert.match(apiService, /- key: FANYU_INVOICE_SIGNATURE_VERIFIED\n\s+value: "true"/);
});

test("Render 通過 CI 才部署，API 啟動具備可追蹤的資料庫準備程序", () => {
  const apiService = renderBlueprint.match(/  - type: web[\s\S]*?\n  - type: web/)?.[0] ?? "";

  assert.match(apiService, /autoDeployTrigger: checksPass/);
  assert.match(apiService, /startCommand: python -m app\.startup prepare/);
  assert.match(apiService, /healthCheckPath: \/ready/);
  assert.match(workflow, /branches: \[main, codex\/v2-social-commerce\]/);
});

test("Render 正式前端以同網域代理 API，避免跨站登入 Cookie 遺失", () => {
  const webService = renderBlueprint.match(/  - type: web\n    name: shilifangyuan-web[\s\S]*$/)?.[0] ?? "";
  const apiRewriteIndex = webService.indexOf("source: /v1/*");
  const spaRewriteIndex = webService.indexOf("source: /*");

  assert.match(webService, /- key: VITE_API_BASE_URL\n\s+value: ""/);
  assert.match(
    webService,
    /source: \/v1\/\*\n\s+destination: https:\/\/shilifangyuan-api\.onrender\.com\/v1\/\*/,
  );
  assert.ok(apiRewriteIndex >= 0);
  assert.ok(spaRewriteIndex > apiRewriteIndex);
});
