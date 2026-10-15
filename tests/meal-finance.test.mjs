import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(new URL("../web/package.json", import.meta.url))("typescript");
const source = await readFile(new URL("../web/src/lib/meal-finance.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } });
const { taipeiDate, financePeriodPreset, financeDateTime } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString("base64")}`);

test("財務日期固定使用台灣日期，不受電腦時區影響", () => {
  assert.equal(taipeiDate(new Date("2030-01-01T16:00:00Z")), "2030-01-02");
  assert.equal(taipeiDate(new Date("2030-01-01T15:59:59Z")), "2030-01-01");
  assert.match(financeDateTime("2030-01-01T16:00:00Z"), /2030\/01\/02/);
});

test("每週預設從週一開始，正確跨年與處理週日", () => {
  assert.deepEqual(financePeriodPreset("week", "2030-01-02"), { startsOn: "2029-12-31", endsOn: "2030-01-02" });
  assert.deepEqual(financePeriodPreset("week", "2030-01-06"), { startsOn: "2029-12-31", endsOn: "2030-01-06" });
  assert.deepEqual(financePeriodPreset("month", "2030-01-02"), { startsOn: "2030-01-01", endsOn: "2030-01-02" });
});
