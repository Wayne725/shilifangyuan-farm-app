import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const { chromium, webkit } = createRequire(import.meta.url)("playwright");
const base = process.env.BROWSER_TEST_URL || "http://127.0.0.1:4185";
if (!["localhost", "127.0.0.1"].includes(new URL(base).hostname)) {
  throw new Error("Membership acceptance must use an isolated local website");
}
const engine = process.env.BROWSER_ENGINE === "webkit" ? webkit : chromium;
const profile = {
  legal_name: "入社測試員", phone: "0900000000", birth_date: "1995-05-16",
  address: "測試地址，非真實住所", emergency_contact: "測試聯絡人",
  consent_version: "isolated-test-v1",
};

async function membership_browser(run) {
  const browser = await engine.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const state = {
      application: { id: "test-application", status: "draft", profile, documents: [] },
      tickets: [], uploads: [], submissions: [], reject_upload: false,
    };
    await context.route("**/*", (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin === "https://storage.example.test") {
        state.uploads.push(request.method());
        return route.fulfill({ status: state.reject_upload ? 503 : 200, body: "" });
      }
      if (url.pathname.startsWith("/v1/")) {
        let json = [];
        if (url.pathname === "/v1/auth/refresh") json = { access_token: "isolated-token", user: { id: "test-applicant", email: "applicant@example.com", display_name: "測試申請人", user_role: "customer" } };
        if (url.pathname === "/v1/members/me") json = { membership_type: "nonmember", membership: null };
        if (url.pathname === "/v1/membership/application") {
          if (request.method() === "PUT") state.application.profile = request.postDataJSON();
          json = state.application;
        }
        if (url.pathname === "/v1/membership/documents/upload-url") {
          const payload = request.postDataJSON();
          state.tickets.push(payload);
          const id = `test-${payload.document_type}`;
          state.application.documents = state.application.documents.filter(doc => doc.id !== id);
          state.application.documents.push({ id, ...payload, status: "pending_upload" });
          json = { document_id: id, upload_url: `https://storage.example.test/${id}`, required_headers: { "Content-Type": payload.content_type } };
        }
        if (url.pathname.endsWith("/confirm")) {
          const id = url.pathname.split("/").at(-2);
          const doc = state.application.documents.find(doc => doc.id === id);
          assert.equal(request.postDataJSON().checksum_sha256, doc.checksum_sha256);
          doc.status = "confirmed";
          json = doc;
        }
        if (url.pathname.startsWith("/v1/membership/documents/") && request.method() === "DELETE") {
          state.application.documents = state.application.documents.filter(doc => doc.id !== url.pathname.split("/").at(-1));
          return route.fulfill({ status: 204 });
        }
        if (url.pathname.endsWith("/submit") || url.pathname.endsWith("/supplement")) {
          state.submissions.push(url.pathname);
          state.application.status = "submitted";
          json = state.application;
        }
        if (url.pathname.endsWith("/withdraw")) {
          state.application.status = "withdrawn";
          json = state.application;
        }
        return route.fulfill({ json });
      }
      if (url.origin === base) return route.continue();
      return route.abort();
    });
    const page = await context.newPage();
    await page.goto(`${base}/membership`);
    await page.getByRole("heading", { name: "入社申請", exact: true }).waitFor();
    const test_only = page.getByLabel("我確認本次只會上傳測試檔案");
    if (await test_only.count()) await test_only.check();
    await run(page, state);
  } finally {
    await browser.close();
  }
}

test("入社證件超過 8MB 在瀏覽器拒絕，不讀取或上傳大檔", async () => {
  await membership_browser(async (page, state) => {
    await page.locator('input[type="file"]').first().setInputFiles({
      name: "oversized-test.pdf", mimeType: "application/pdf", buffer: Buffer.alloc(8 * 1024 * 1024 + 1),
    });
    await page.getByText("檔案大小須為 1 byte 至 8MB，請重新選擇", { exact: true }).waitFor({ timeout: 2500 });
    assert.equal(state.tickets.length, 0);
    assert.equal(state.uploads.length, 0);
  });
});

test("不支援的證件格式在瀏覽器拒絕，不送出上傳", async () => {
  await membership_browser(async (page, state) => {
    await page.locator('input[type="file"]').first().setInputFiles({
      name: "test.html", mimeType: "text/html", buffer: Buffer.from("<h1>非證件測試檔</h1>"),
    });
    await page.getByText("僅支援 JPEG、PNG 或 PDF 檔案", { exact: true }).waitFor({ timeout: 2500 });
    assert.equal(state.tickets.length, 0);
    assert.equal(state.uploads.length, 0);
  });
});

test("入社三份假證件上傳、補件重送與撤回保持正確畫面狀態", async () => {
  await membership_browser(async (page, state) => {
    await page.getByLabel("現居地址", { exact: true }).fill("更新後的隔離測試地址");
    await page.getByRole("button", { name: "儲存基本資料", exact: true }).click();
    await page.getByText("基本資料已儲存。", { exact: true }).waitFor();
    assert.equal(state.application.profile.address, "更新後的隔離測試地址");
    const submit = page.getByRole("button", { name: "送出申請", exact: true });
    assert.equal(await submit.isDisabled(), true);
    for (const [index, label] of ["身分證正面", "身分證反面", "第二證件"].entries()) {
      const slot = page.locator(".document-grid article").filter({ has: page.getByRole("heading", { name: label, exact: true }) });
      await slot.locator('input[type="file"]').setInputFiles({
        name: `test-${index}.pdf`, mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\nTEST ONLY - NOT AN ID DOCUMENT"),
      });
      await slot.getByText("已確認", { exact: true }).waitFor();
      if (index < 2) assert.equal(await submit.isDisabled(), true);
    }
    assert.deepEqual(state.tickets.map(ticket => ticket.document_type), ["id_front", "id_back", "secondary"]);
    assert.deepEqual(state.uploads, ["PUT", "PUT", "PUT"]);
    await submit.click();
    await page.getByRole("heading", { name: "合作社正在審核", exact: true }).waitFor();
    assert.equal(await page.locator('input[type="file"]').count(), 0);
    assert.equal(await page.getByRole("link", { name: "查看社員款項", exact: true }).count(), 1);

    // Simulate the response of a separately tested administrator API.
    state.application.status = "needs_supplement";
    state.application.review_reason = "請更新第二證件";
    await page.reload();
    await page.getByText("請更新第二證件", { exact: true }).waitFor();
    await page.getByRole("button", { name: "刪除第二證件", exact: true }).click();
    const resubmit = page.getByRole("button", { name: "重新送件", exact: true });
    await page.getByText("2/3", { exact: true }).waitFor();
    assert.equal(await resubmit.isDisabled(), true);
    const test_only = page.getByLabel("我確認本次只會上傳測試檔案");
    if (await test_only.count()) await test_only.check();
    const slot = page.locator(".document-grid article").filter({ has: page.getByRole("heading", { name: "第二證件", exact: true }) });
    await slot.locator('input[type="file"]').setInputFiles({ name: "replacement.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\nREPLACEMENT TEST ONLY") });
    await slot.getByText("已確認", { exact: true }).waitFor();
    await resubmit.click();
    await page.getByRole("heading", { name: "合作社正在審核", exact: true }).waitFor();
    assert.deepEqual(state.submissions, ["/v1/membership/application/submit", "/v1/membership/application/supplement"]);
    await page.getByRole("button", { name: "撤回申請", exact: true }).click();
    await page.getByRole("heading", { name: "申請已撤回", exact: true }).waitFor();
    assert.equal(await page.getByRole("link", { name: "查看社員款項", exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  });
});

test("證件儲存失敗不會確認成功或開放送件，並允許重選同一檔案", async () => {
  await membership_browser(async (page, state) => {
    const file = { name: "test.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\nTEST ONLY") };
    state.reject_upload = true;
    await page.locator('input[type="file"]').first().setInputFiles(file);
    await page.getByText("證件上傳失敗，請重新選擇檔案", { exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "送出申請", exact: true }).isDisabled(), true);
    assert.equal(state.application.documents[0].status, "pending_upload");
    state.reject_upload = false;
    await page.locator('input[type="file"]').first().setInputFiles(file);
    await page.getByText("1/3", { exact: true }).waitFor();
    assert.equal(state.application.documents[0].status, "confirmed");
  });
});
