import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";
import ts from "../web/node_modules/typescript/lib/typescript.js";

const webSourceDirectory = new URL("../web/src/", import.meta.url);
const backendRoutes = JSON.parse(await readFile(new URL("../web/src/lib/api.routes.json", import.meta.url), "utf8"));
const methods = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);

async function sourceFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const url = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directory);
    if (entry.isDirectory()) files.push(...await sourceFiles(url));
    if (entry.isFile() && /\.tsx?$/.test(entry.name)) files.push(url);
  }
  return files;
}

function normalise(path) {
  return path.replace(/\$\{[^}]*\}/g, "{param}").replace(/\{[^}]*\}/g, "{param}").replace(/\?.*$/, "");
}

const knownRoutes = backendRoutes.map((route) => {
  const [method, path] = route.split(" ");
  return { method, segments: normalise(path).split("/") };
});

function matchesBackendRoute(method, path) {
  const segments = normalise(path).split("/");
  return knownRoutes.some((route) => route.method === method && route.segments.length === segments.length && route.segments.every((segment, index) => segment === segments[index] || segment === "{param}" || segments[index] === "{param}"));
}

function requestMethod(call) {
  const options = call.arguments[1];
  if (!options || !ts.isObjectLiteralExpression(options)) return "GET";
  const property = options.properties.find((item) => ts.isPropertyAssignment(item) && item.name.getText() === "method");
  return property && ts.isPropertyAssignment(property) && ts.isStringLiteral(property.initializer) ? property.initializer.text : "GET";
}

function requestPath(argument, sourceFile) {
  if (ts.isStringLiteral(argument) || ts.isNoSubstitutionTemplateLiteral(argument)) return argument.text;
  if (ts.isTemplateExpression(argument)) return argument.getText(sourceFile).slice(1, -1);
  return null;
}

async function collectCalls() {
  const calls = [];
  for (const file of await sourceFiles(webSourceDirectory)) {
    const source = await readFile(file, "utf8");
    const sourceFile = ts.createSourceFile(file.pathname, source, ts.ScriptTarget.Latest, true, file.pathname.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const visit = (node) => {
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && ["apiFetch", "downloadApiFile"].includes(node.expression.text) && node.arguments[0]) {
        const path = requestPath(node.arguments[0], sourceFile);
        if (path?.startsWith("/v1")) calls.push({ file: file.pathname, method: node.expression.text === "downloadApiFile" ? "GET" : requestMethod(node), path });
      }
      ts.forEachChild(node, visit);
    };
    visit(sourceFile);
  }
  return calls;
}

const calls = await collectCalls();

test("新版 Web 呼叫的 API 都存在於 FastAPI 路由表", () => {
  assert.ok(calls.length >= 60, `只解析到 ${calls.length} 個 API 呼叫`);
  const missing = calls.filter((call) => !matchesBackendRoute(call.method, call.path)).map((call) => `${call.method} ${call.path}`);
  assert.deepEqual([...new Set(missing)], []);
});

test("OpenAPI 路由表保留標準方法與完整規模", () => {
  assert.ok(backendRoutes.length >= 150);
  for (const route of backendRoutes) assert.ok(methods.has(route.split(" ")[0]), route);
});

test("後端會導回的正式 Web 入口都有註冊", async () => {
  const router = await readFile(new URL("../web/src/router.tsx", import.meta.url), "utf8");
  for (const path of ["/orders", "/account", "/verify-email", "/reset-password"]) assert.match(router, new RegExp(`path: "${path.replace("/", "\\/")}"`));
});
