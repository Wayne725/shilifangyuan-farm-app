import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  activateDemoMembership,
  canPayDemoMembershipCharge,
  canWithdrawDemoMembershipApplication,
  createPendingDemoMembership,
  findDemoMembershipApplication,
} from "../src/services/demoMembership.ts";

const apiSource = await readFile(
  new URL("../src/services/api.ts", import.meta.url),
  "utf8",
);

test("內建展示送出申請後會建立兩筆應繳款", () => {
  const user = {
    id: "user-test",
    email: "test@example.com",
    display_name: "測試申請人",
    user_role: "customer",
    membership_type: "nonmember",
  };
  const pending = createPendingDemoMembership(user);

  assert.equal(pending.membership.status, "pending_payment");
  assert.deepEqual(
    pending.charges.map((charge) => [charge.charge_type, charge.amount]),
    [
      ["joining_fee", 500],
      ["share_capital", 1000],
    ],
  );
});

test("內建展示只有兩種款項皆付清才轉為實習社員並啟用社員價", () => {
  const user = {
    id: "user-test",
    email: "test@example.com",
    display_name: "測試申請人",
    user_role: "customer",
    membership_type: "nonmember",
  };
  const { membership, charges } = createPendingDemoMembership(user);
  charges[0].payment_status = "paid";
  assert.equal(
    activateDemoMembership(
      user,
      membership,
      charges,
      "SLF-T-2026-0020",
      "2026-08-01T00:00:00.000Z",
    ),
    false,
  );
  assert.equal(user.membership_type, "nonmember");

  charges[1].payment_status = "paid";
  assert.equal(
    activateDemoMembership(
      user,
      membership,
      charges,
      "SLF-T-2026-0020",
      "2026-08-01T00:00:00.000Z",
    ),
    true,
  );
  assert.equal(membership.status, "trainee");
  assert.equal(membership.trainee_number, "SLF-T-2026-0020");
  assert.equal(membership.member_number, null);
  assert.equal(user.membership_type, "trainee");
});

test("內建 API 依登入者隔離費用並接上審核與啟用狀態機", () => {
  assert.match(apiSource, /membershipCharges\[getDemoUser\(\)\.id\]/);
  assert.match(
    apiSource,
    /createPendingDemoMembership\(approvedAccount\.user\)/,
  );
  assert.match(apiSource, /activateDemoMembership\(/);
  assert.match(apiSource, /demoState\.users = structuredCloneSafe\(demoUsers\)/);
});

test("入社申請以不可變 user_id 綁定，不受姓名修改影響", () => {
  const applications = [
    {
      id: "application-a",
      user_id: "user-a",
      legal_name: "原姓名",
    },
    {
      id: "application-b",
      user_id: "user-b",
      legal_name: "相同姓名也不會誤綁",
    },
  ];

  applications[0].legal_name = "修改後姓名";
  assert.equal(
    findDemoMembershipApplication(applications, "user-a")?.id,
    "application-a",
  );
  assert.equal(findDemoMembershipApplication(applications, "user-c"), undefined);
});

test("內建通知依登入帳號隔離，訪客預設不會取得社員價", () => {
  assert.match(
    apiSource,
    /demoState\.notifications\[getDemoUser\(\)\.id\] \?\? \[\]/,
  );
  assert.match(apiSource, /membership_type: "nonmember"/);
  assert.match(apiSource, /canPayDemoMembershipCharge\(/);
  assert.match(apiSource, /const canReview =/);
  assert.match(apiSource, /application\.status === "submitted"/);
});

test("啟用後不可撤回，終止的待付款會籍也不可再繳款", () => {
  const user = {
    id: "user-test",
    email: "test@example.com",
    display_name: "測試申請人",
    user_role: "customer",
    membership_type: "nonmember",
  };
  const { membership, charges } = createPendingDemoMembership(user);
  const application = {
    id: "application-test",
    user_id: user.id,
    status: "submitted",
  };

  assert.equal(
    canWithdrawDemoMembershipApplication(application, membership),
    true,
  );
  assert.equal(
    canPayDemoMembershipCharge(application, membership, charges[0]),
    true,
  );

  membership.status = "active";
  membership.member_number = "SLF-2026-0099";
  membership.started_at = "2026-08-01T00:00:00.000Z";
  assert.equal(
    canWithdrawDemoMembershipApplication(application, membership),
    false,
  );

  membership.status = "terminated";
  assert.equal(
    canWithdrawDemoMembershipApplication(application, membership),
    false,
  );
  assert.equal(
    canPayDemoMembershipCharge(application, membership, charges[0]),
    false,
  );
});
