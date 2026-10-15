import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
const source = await readFile(new URL("../web/src/lib/invoice-tax.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
});
const { invoiceTaxSummary, taxTypeLabel } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);

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
