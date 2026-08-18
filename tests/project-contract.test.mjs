import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
const workflow = await readFile(new URL("../.github/workflows/ci.yml", import.meta.url), "utf8");
const deployment = await readFile(new URL("../docs/DEPLOYMENT.md", import.meta.url), "utf8");

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

test("部署手冊保留私有 R2 與正式上線條件", () => {
  assert.match(deployment, /R2 Bucket CORS/);
  assert.match(deployment, /x-amz-meta-sha256/);
  assert.match(deployment, /正式營運上線條件/);
});
