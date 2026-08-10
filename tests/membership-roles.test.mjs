import assert from "node:assert/strict";
import test from "node:test";

import {
  hasFormalMemberAccess,
  hasMemberPricing,
  membershipIdentity,
  membershipIdentityNeedsSync,
  membershipTypeForStatus,
} from "../src/lib/membership.ts";

test("實習社員與正式社員都享社員價，但只有正式社員有社務權限", () => {
  assert.equal(hasMemberPricing("nonmember"), false);
  assert.equal(hasMemberPricing("trainee"), true);
  assert.equal(hasMemberPricing("member"), true);
  assert.equal(hasFormalMemberAccess("nonmember"), false);
  assert.equal(hasFormalMemberAccess("trainee"), false);
  assert.equal(hasFormalMemberAccess("member"), true);
});

test("三階身分各自顯示不同前綴的永久編號", () => {
  const user = {
    customer_number: "SLF-C-2026-0012",
    membership_type: "nonmember",
  };
  const membership = {
    trainee_number: "SLF-T-2026-0007",
    member_number: "SLF-2026-0003",
  };

  assert.deepEqual(membershipIdentity(user, membership), {
    label: "一般買家編號",
    number: "SLF-C-2026-0012",
  });
  assert.deepEqual(
    membershipIdentity({ ...user, membership_type: "trainee" }, membership),
    { label: "實習社員編號", number: "SLF-T-2026-0007" },
  );
  assert.deepEqual(
    membershipIdentity({ ...user, membership_type: "member" }, membership),
    { label: "正式社員編號", number: "SLF-2026-0003" },
  );
});

test("會籍狀態與登入身分快照不一致時需要同步，包括轉正與降權", () => {
  assert.equal(membershipTypeForStatus("pending_payment"), "nonmember");
  assert.equal(membershipTypeForStatus("trainee"), "trainee");
  assert.equal(membershipTypeForStatus("active"), "member");
  assert.equal(membershipTypeForStatus("suspended"), "nonmember");
  assert.equal(membershipTypeForStatus("resigned"), "nonmember");
  assert.equal(membershipTypeForStatus("terminated"), "nonmember");

  assert.equal(membershipIdentityNeedsSync("trainee", "nonmember"), true);
  assert.equal(membershipIdentityNeedsSync("active", "trainee"), true);
  assert.equal(membershipIdentityNeedsSync("suspended", "member"), true);
  assert.equal(membershipIdentityNeedsSync("resigned", "member"), true);
  assert.equal(membershipIdentityNeedsSync("terminated", "member"), true);
  assert.equal(membershipIdentityNeedsSync("trainee", "trainee"), false);
  assert.equal(membershipIdentityNeedsSync("active", "member"), false);
});
