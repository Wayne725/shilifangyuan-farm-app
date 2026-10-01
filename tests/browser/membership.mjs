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
          doc.expires_at = new Date(Date.now() + 14 * 86400000).toISOString();
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

test("入社申請不收身分證字號或證件，不發出上傳請求", async () => {
  await membership_browser(async (page, state) => {
    assert.equal(await page.getByLabel("身分證字號", { exact: true }).count(), 0);
    assert.equal(await page.locator('input[type="file"]').count(), 0);
    assert.equal(await page.getByRole("heading", { name: "身分證件", exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "送出申請", exact: true }).isEnabled(), true);
    assert.deepEqual(state.tickets, []);
    assert.deepEqual(state.uploads, []);
  });
});

test("入社基本資料送出、補件重送與撤回不需要證件", async () => {
  await membership_browser(async (page, state) => {
    await page.getByLabel("現居地址", { exact: true }).fill("更新後的隔離測試地址");
    await page.getByRole("button", { name: "儲存基本資料", exact: true }).click();
    await page.getByText("基本資料已儲存。", { exact: true }).waitFor();
    assert.equal(state.application.profile.address, "更新後的隔離測試地址");
    assert.equal("identity_number" in state.application.profile, false);
    await page.getByRole("button", { name: "送出申請", exact: true }).click();
    await page.getByRole("heading", { name: "合作社正在審核", exact: true }).waitFor();
    assert.equal(await page.getByRole("link", { name: "查看社員款項", exact: true }).count(), 1);
    state.application.status = "needs_supplement";
    state.application.review_reason = "請更新聯絡地址";
    await page.reload();
    await page.getByText("請更新聯絡地址", { exact: true }).waitFor();
    assert.equal(await page.locator('input[type="file"]').count(), 0);
    await page.getByRole("button", { name: "重新送件", exact: true }).click();
    await page.getByRole("heading", { name: "合作社正在審核", exact: true }).waitFor();
    assert.deepEqual(state.submissions, ["/v1/membership/application/submit", "/v1/membership/application/supplement"]);
    assert.deepEqual(state.tickets, []);
    await page.getByRole("button", { name: "撤回申請", exact: true }).click();
    await page.getByRole("heading", { name: "申請已撤回", exact: true }).waitFor();
    assert.equal(await page.getByRole("link", { name: "查看社員款項", exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  });
});

test("歷史證件不顯示，也不阻擋基本資料送件", async () => {
  await membership_browser(async (page, state) => {
    state.application.documents = ["id_front", "id_back", "secondary"].map((type, index) => ({
      id: 'test-' + type, document_type: type, status: index === 2 ? "deleted" : "confirmed",
      expires_at: new Date(Date.now() - 1000).toISOString(), retention_expired: true,
      deletion_error: index === 1 ? "storage_delete_failed" : null,
      deleted_at: index === 2 ? new Date().toISOString() : null,
    }));
    await page.reload();
    await page.getByRole("heading", { name: "入社申請", exact: true }).waitFor();
    assert.equal(await page.locator('.document-section').count(), 0);
    assert.equal(await page.getByRole("button", { name: "送出申請", exact: true }).isEnabled(), true);
    assert.equal(state.application.documents.length, 3);
  });
});

test("未儲存基本資料前仍不允許送件", async () => {
  await membership_browser(async (page, state) => {
    state.application.profile = null;
    await page.reload();
    await page.getByRole("heading", { name: "入社申請", exact: true }).waitFor();
    assert.equal(await page.getByRole("button", { name: "送出申請", exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole("button", { name: "儲存基本資料", exact: true }).isDisabled(), true);
  });
});
