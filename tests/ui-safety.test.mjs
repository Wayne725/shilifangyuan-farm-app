import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const appDirectory = new URL("../app/", import.meta.url);

async function collectTypeScriptFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const url = new URL(entry.isDirectory() ? `${entry.name}/` : entry.name, directory);
    if (entry.isDirectory()) {
      files.push(...(await collectTypeScriptFiles(url)));
    } else if (entry.name.endsWith(".tsx")) {
      files.push(url);
    }
  }
  return files;
}

function parse(source, fileName) {
  return ts.createSourceFile(
    fileName,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX,
  );
}

function defaultComponent(sourceFile) {
  const declaration = sourceFile.statements.find(
    (statement) =>
      ts.isFunctionDeclaration(statement) &&
      statement.modifiers?.some(
        (modifier) => modifier.kind === ts.SyntaxKind.DefaultKeyword,
      ),
  );
  if (declaration) return declaration;

  const assignment = sourceFile.statements.find(ts.isExportAssignment);
  if (!assignment) return null;
  if (ts.isArrowFunction(assignment.expression)) return assignment.expression;
  if (!ts.isIdentifier(assignment.expression)) return null;

  const name = assignment.expression.text;
  for (const statement of sourceFile.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name?.text === name) {
      return statement;
    }
    if (!ts.isVariableStatement(statement)) continue;
    for (const item of statement.declarationList.declarations) {
      if (
        ts.isIdentifier(item.name) &&
        item.name.text === name &&
        item.initializer &&
        (ts.isArrowFunction(item.initializer) ||
          ts.isFunctionExpression(item.initializer))
      ) {
        return item.initializer;
      }
    }
  }
  return null;
}

function nearestFunction(node) {
  let current = node.parent;
  while (current) {
    if (ts.isFunctionLike(current)) return current;
    current = current.parent;
  }
  return null;
}

function directRenderRedirects(sourceFile, component) {
  const calls = [];
  function visit(node) {
    if (
      ts.isCallExpression(node) &&
      ts.isPropertyAccessExpression(node.expression) &&
      ts.isIdentifier(node.expression.expression) &&
      node.expression.expression.text === "router" &&
      node.expression.name.text === "replace" &&
      nearestFunction(node) === component
    ) {
      const location = sourceFile.getLineAndCharacterOfPosition(node.getStart());
      calls.push(location.line + 1);
    }
    ts.forEachChild(node, visit);
  }
  visit(component);
  return calls;
}

test("App 畫面不會在 React render 階段直接呼叫 router.replace", async () => {
  const violations = [];
  let checkoutInspected = false;
  for (const url of await collectTypeScriptFiles(appDirectory)) {
    const source = await readFile(url, "utf8");
    const sourceFile = parse(source, url.pathname);
    const component = defaultComponent(sourceFile);
    if (!component) continue;
    checkoutInspected ||= url.pathname.endsWith("/checkout.tsx");
    const lines = directRenderRedirects(sourceFile, component);
    if (lines.length) {
      violations.push(`${decodeURIComponent(url.pathname)}:${lines.join(",")}`);
    }
  }
  assert.ok(checkoutInspected, "測試必須能解析 CheckoutScreen，避免靜默漏檢");
  assert.deepEqual(
    violations,
    [],
    "導航副作用必須放在 useEffect、事件處理器或 mutation callback，不能在 render 執行",
  );
});

test("便當取餐憑證使用真正的 QR encoder 並編碼後端憑證", async () => {
  const url = new URL("../app/meal-order/[id].tsx", import.meta.url);
  const source = await readFile(url, "utf8");
  const sourceFile = parse(source, url.pathname);
  const qrImport = sourceFile.statements.find(
    (statement) =>
      ts.isImportDeclaration(statement) &&
      ts.isStringLiteral(statement.moduleSpecifier) &&
      statement.moduleSpecifier.text === "react-native-qrcode-svg",
  );

  assert.ok(qrImport, "取餐憑證必須使用 QR encoder，不可用圖示假裝 QR code");
  const qrComponent = qrImport.importClause?.name?.text;
  assert.ok(qrComponent, "QR encoder 必須以 default import 載入");

  const encodedValues = [];
  const fakeIcons = [];
  function visit(node) {
    if (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) {
      const tagName = node.tagName.getText(sourceFile);
      const nameAttribute = node.attributes.properties.find(
        (property) =>
          ts.isJsxAttribute(property) && property.name.getText(sourceFile) === "name",
      );
      if (
        tagName === "Ionicons" &&
        nameAttribute &&
        ts.isJsxAttribute(nameAttribute) &&
        nameAttribute.initializer &&
        ts.isStringLiteral(nameAttribute.initializer) &&
        nameAttribute.initializer.text === "qr-code"
      ) {
        fakeIcons.push(node.getStart(sourceFile));
      }
      if (tagName === qrComponent) {
        const valueAttribute = node.attributes.properties.find(
          (property) =>
            ts.isJsxAttribute(property) &&
            property.name.getText(sourceFile) === "value",
        );
        if (
          valueAttribute &&
          ts.isJsxAttribute(valueAttribute) &&
          valueAttribute.initializer &&
          ts.isJsxExpression(valueAttribute.initializer) &&
          valueAttribute.initializer.expression
        ) {
          encodedValues.push(valueAttribute.initializer.expression.getText(sourceFile));
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);

  assert.deepEqual(fakeIcons, [], "不可使用固定 QR 圖示冒充可掃描憑證");
  assert.ok(
    encodedValues.some((value) => value.includes("order.pickup_qr_payload")),
    "QR encoder 的 value 必須來自後端 pickup_qr_payload",
  );
});
