import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const packageJson = JSON.parse(
  await readFile(new URL("../package.json", import.meta.url), "utf8"),
);
const workflow = await readFile(
  new URL("../.github/workflows/ci.yml", import.meta.url),
  "utf8",
);
const deploymentGuide = await readFile(
  new URL("../docs/DEPLOYMENT.md", import.meta.url),
  "utf8",
);

test("Expo 提供 Web、iOS 與 Android 的獨立 export 指令", () => {
  for (const platform of ["web", "ios", "android"]) {
    const command = packageJson.scripts[`export:${platform}`];
    assert.equal(typeof command, "string");
    assert.match(command, new RegExp(`expo export .*--platform ${platform}`));
  }
});

test("CI 會驗證前端品質、三平台 bundle 與後端測試", () => {
  assert.match(workflow, /pnpm typecheck/);
  assert.match(workflow, /pnpm test/);
  assert.match(workflow, /platform: \[web, ios, android\]/);
  assert.match(workflow, /pnpm export:\$\{\{ matrix\.platform \}\}/);
  assert.match(workflow, /pytest/);
  assert.match(workflow, /alembic downgrade base/);
  assert.ok((workflow.match(/alembic upgrade head/g) ?? []).length >= 2);
});

test("部署手冊包含私有 R2 Web 上傳所需 CORS", () => {
  assert.match(deploymentGuide, /R2 Bucket CORS/);
  assert.match(deploymentGuide, /"AllowedMethods": \["PUT"\]/);
  assert.match(deploymentGuide, /x-amz-meta-sha256/);
  assert.match(deploymentGuide, /不可啟用公開網域或 `r2\.dev`/);
});
