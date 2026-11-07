import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";

const webRequire = createRequire(new URL("../web/package.json", import.meta.url));
const ts = webRequire("typescript");
const source = await readFile(new URL("../web/src/lib/invoice-tax.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
});
const taxModule = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);
const { invoiceTaxSummary, taxTypeLabel, resolveInvoiceTax } = taxModule;

test("正式社員免稅原因只讀訂單快照，商品本身免稅不被誤稱社員免稅", () => {
  const items = [{ tax_type: "tax_exempt" }];
  assert.deepEqual(invoiceTaxSummary({ items, memberTaxExempt: true }), {
    label: "免稅", reason: "正式社員個人交易免稅，仍開立電子發票",
  });
  assert.deepEqual(invoiceTaxSummary({ items, memberTaxExempt: false }), { label: "免稅", reason: null });
  assert.equal(invoiceTaxSummary({ items }).reason, null);
});

test("商品明細能區分全應稅、全免稅與混合", () => {
  assert.equal(invoiceTaxSummary({ items: [{ tax_type: "taxable" }] }).label, "應稅");
  assert.equal(invoiceTaxSummary({ items: [{ tax_type: "tax_exempt" }, { tax_type: "tax_exempt" }] }).label, "免稅");
  assert.equal(invoiceTaxSummary({ items: [{ tax_type: "tax_exempt" }, { tax_type: "taxable" }] }).label, "應稅／免稅混合");
});

test("免稅商品加付費運費屬混合稅別，正式社員個人交易的運費仍免稅", () => {
  const items = [{ tax_type: "tax_exempt" }];
  assert.equal(invoiceTaxSummary({ items, shippingFee: 60, memberTaxExempt: false }).label, "應稅／免稅混合");
  assert.equal(invoiceTaxSummary({ items, shippingFee: 60, memberTaxExempt: true }).label, "免稅");
  assert.equal(invoiceTaxSummary({ items, shippingFee: 0 }).label, "免稅");
});

test("舊單與未辨識稅別不從零稅額或空值推定免稅", () => {
  for (const tax_type of [undefined, null, "", "unknown", "zero_rated"]) {
    assert.equal(taxTypeLabel(tax_type), "稅別未提供");
    assert.equal(invoiceTaxSummary({ items: [{ tax_type }], memberTaxExempt: false }).label, "稅別未提供");
  }
  assert.equal(invoiceTaxSummary({ items: [] }).label, "稅別未提供");
  assert.equal(invoiceTaxSummary({ items: [{ tax_type: "taxable" }, {}], shippingFee: 60 }).label, "稅別未提供");
});

const estimated = { items: [{ tax_type: "taxable" }], taxAmount: 52 };
const original = { status: "issued", tax_type: "taxable", amount_semantics: "separately_stated", tax_amount: 53 };

test("未出票的報價、pending、failed 只顯示明標預估的訂單快照", () => {
  for (const invoiceStatus of [undefined, "not_eligible", "pending", "failed", "external"]) {
    const value = resolveInvoiceTax({ ...estimated, invoiceStatus });
    assert.equal(value.amount, 52);
    assert.equal(value.amountLabel, "預估稅額");
    assert.equal(value.taxTypeHeading, "預估稅別");
  }
  assert.equal(resolveInvoiceTax({ ...estimated, estimatedAmountLabel: "預估商品稅額" }).amountLabel, "預估商品稅額");
  assert.equal(resolveInvoiceTax({ ...estimated, invoiceTax: { ...original, status: "failed" } }).amount, 52);
});

test("已出票取原票實額與稅別，不受訂單值或社員快照覆寫", () => {
  const value = resolveInvoiceTax({ items: [{ tax_type: "tax_exempt" }], taxAmount: 0, memberTaxExempt: true,
    invoiceStatus: "issued", invoiceTax: original });
  assert.equal(value.amount, 53);
  assert.equal(value.amountLabel, "發票稅額");
  assert.equal(value.taxTypeLabel, "應稅");
  assert.equal(value.amountText, null);
  assert.equal(resolveInvoiceTax({ ...estimated, invoiceTax: original }).amount, 53);
  for (const [tax_type, label] of [["tax_exempt", "免稅"], ["mixed", "應稅／免稅混合"]]) {
    assert.equal(resolveInvoiceTax({ ...estimated, invoiceTax: { ...original, tax_type } }).taxTypeLabel, label);
  }
});

test("B2C 含稅未分列不顯示零元或推定免稅", () => {
  const value = resolveInvoiceTax({ ...estimated, invoiceStatus: "issued",
    invoiceTax: { ...original, amount_semantics: "included_not_separately_stated", tax_amount: 0 } });
  assert.equal(value.taxTypeLabel, "應稅");
  assert.equal(value.amount, null);
  assert.equal(value.amountText, "含稅，發票未分列稅額");
});

test("作廢與作廢處理中仍明示原票實額，不退回目前訂單估計", () => {
  for (const [status, label] of [["void_pending", "原發票稅額（作廢處理中）"], ["voided", "原發票稅額（已作廢）"]]) {
    for (const invoiceTax of [original, { ...original, status }]) {
      const value = resolveInvoiceTax({ ...estimated, invoiceStatus: status, invoiceTax });
      assert.equal(value.amount, 53);
      assert.equal(value.amountLabel, label);
      assert.equal(value.taxTypeHeading, "原發票稅別");
    }
  }
});

test("有出票狀態但原票缺失或語義未知，禁止冒用訂單預估", () => {
  for (const invoiceStatus of ["issued", "void_pending", "voided", "unknown"]) {
    for (const invoiceTax of [undefined, null, { ...original, status: "pending" },
      { ...original, tax_type: "unknown", amount_semantics: "unknown", tax_amount: 0 }]) {
      const value = resolveInvoiceTax({ ...estimated, invoiceStatus, invoiceTax });
      assert.equal(value.amount, null);
      assert.equal(value.amountText, "發票稅額未提供");
      assert.equal(value.taxTypeLabel, "稅別未提供");
      assert.doesNotMatch(value.amountLabel, /預估/);
    }
  }
});

test("數值零不決定稅別，原票分列零與缺值區分", () => {
  const value = resolveInvoiceTax({ ...estimated, invoiceTax: { ...original, tax_type: "unknown", tax_amount: 0 } });
  assert.equal(value.amount, 0);
  assert.equal(value.taxTypeLabel, "稅別未提供");
  for (const tax_amount of [null, undefined, NaN, Infinity, -1]) {
    assert.equal(resolveInvoiceTax({ ...estimated, invoiceTax: { ...original, tax_amount } }).amount, null);
  }
});

test("實際元件輸出沿用原票與預估標籤，不渲染 B2C 假零元", async () => {
  const raw = await readFile(new URL("../web/src/components/InvoiceTaxSummary.tsx", import.meta.url), "utf8");
  const output = ts.transpileModule(raw, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", output)((name) => name === "../lib/api" ? { formatMoney: (value) => `$${value}` }
    : name === "../lib/invoice-tax" ? taxModule : name === "@phosphor-icons/react" ? { Receipt: () => null } : webRequire(name), module, module.exports);
  const { createElement } = webRequire("react");
  const { renderToStaticMarkup } = webRequire("react-dom/server");
  for (const variant of ["rows", "card"]) {
    const render = (props) => renderToStaticMarkup(createElement(module.exports.InvoiceTaxSummary, { ...estimated, variant, ...props }));
    const included = render({ invoiceStatus: "issued", invoiceTax: { ...original, amount_semantics: "included_not_separately_stated", tax_amount: 0 } });
    assert.match(included, /含稅，發票未分列稅額/);
    assert.doesNotMatch(included, /\$0|免稅|預估/);
    assert.match(render({ invoiceStatus: "pending" }), /預估稅額.*\$52/);
    assert.match(render({ invoiceStatus: "voided", invoiceTax: original }), /原發票稅額（已作廢）.*\$53/);
    assert.doesNotMatch(render({ invoiceStatus: "issued" }), /\$52|預估/);
  }
});

test("五個已成立訂單呼叫同時傳入原票摘要與出票狀態，三個報價保持預估", async () => {
  const callers = [
    ["pages/AdminMealsPage.tsx", "order"], ["pages/OrdersPage.tsx", "selected"],
    ["components/MealOrderHistory.tsx", "selected"], ["components/AdminMealOrderSearch.tsx", "order"],
    ["pages/AdminPage.tsx", "order"], ["pages/CheckoutPage.tsx", null],
    ["pages/MealEventPage.tsx", null], ["pages/GroupCampaignPage.tsx", null],
  ];
  for (const [file, binding] of callers) {
    const raw = await readFile(new URL(`../web/src/${file}`, import.meta.url), "utf8");
    const tree = ts.createSourceFile(file, raw, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const nodes = [];
    function visit(node) {
      if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(tree) === "InvoiceTaxSummary") nodes.push(node);
      ts.forEachChild(node, visit);
    }
    visit(tree);
    assert.equal(nodes.length, 1, file);
    const attributes = new Map(nodes[0].attributes.properties.filter(ts.isJsxAttribute)
      .map((attribute) => [attribute.name.getText(tree), attribute.initializer?.expression?.getText(tree)]));
    assert.equal(attributes.get("invoiceTax"), binding ? `${binding}.invoice_tax` : undefined, file);
    assert.equal(attributes.get("invoiceStatus"), binding ? `${binding}.invoice_status` : undefined, file);
  }
});

test("發票列表使用同一 resolver，估計只讀 estimated_tax_amount", async () => {
  const raw = await readFile(new URL("../web/src/pages/AdminInvoicesPage.tsx", import.meta.url), "utf8");
  const output = ts.transpileModule(raw + "\nexport { InvoiceTaxCell };", {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", output)((name) => name === "../lib/api" ? { formatMoney: (value) => `$${value}` }
    : name === "../lib/invoice-tax" ? taxModule : name.startsWith("react") ? webRequire(name) : {}, module, module.exports);
  const { createElement } = webRequire("react");
  const { renderToStaticMarkup } = webRequire("react-dom/server");
  const render = (row) => renderToStaticMarkup(createElement(module.exports.InvoiceTaxCell,
    { row: { tax_amount: 99, estimated_tax_amount: 52, ...row } }));
  assert.match(render({ status: "pending" }), /預估稅額.*\$52/);
  assert.match(render({ status: "issued", invoice_tax: original }), /發票稅額.*\$53/);
  assert.match(render({ status: "voided", invoice_tax: original }), /原發票稅額（已作廢）.*\$53/);
  assert.doesNotMatch(render({ status: "issued" }), /\$99|\$52|預估/);
  assert.match(render({ status: "issued", invoice_tax: { ...original, amount_semantics: "included_not_separately_stated", tax_amount: 0 } }), /含稅，發票未分列稅額/);
});

test("改量與財務快照沿用數值且明標預估，既有 CSV 路徑不變", async () => {
  for (const file of ["components/OrderQuantityAmendment.tsx", "pages/AdminFinancePage.tsx"]) {
    const raw = await readFile(new URL(`../web/src/${file}`, import.meta.url), "utf8");
    assert.match(raw, /<dt>預估稅額<\/dt><dd>\{formatMoney\((?:preview|order)\.tax_amount\)\}/);
    assert.doesNotMatch(raw, /免稅稅額|金額內含稅額|<dt>內含稅額/);
  }
  const register = await readFile(new URL("../web/src/pages/AdminInvoicesPage.tsx", import.meta.url), "utf8");
  assert.match(register, /downloadApiFile\(`\/v1\/admin\/invoice-register\.csv\?\$\{params\}`/);
});
