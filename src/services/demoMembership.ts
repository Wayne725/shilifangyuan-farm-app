import type {
  Membership,
  MembershipApplication,
  MembershipCharge,
  User,
} from "../types";

export function findDemoMembershipApplication(
  applications: MembershipApplication[],
  userId: string,
) {
  return applications.find((application) => application.user_id === userId);
}

export function isDemoMembershipApplicationEditable(
  application: MembershipApplication,
) {
  return ["draft", "needs_revision"].includes(application.status);
}

export function canWithdrawDemoMembershipApplication(
  application: MembershipApplication | null,
  membership: Membership | null,
) {
  if (
    !application ||
    !["draft", "submitted", "needs_revision", "approved"].includes(
      application.status,
    )
  ) {
    return false;
  }
  if (!membership) return true;
  return !(
    membership.member_number ||
    membership.started_at ||
    ["active", "suspended", "resigned"].includes(membership.status)
  );
}

export function canPayDemoMembershipCharge(
  application: MembershipApplication | null,
  membership: Membership | null,
  charge: MembershipCharge | null,
) {
  return (
    application?.status === "approved" &&
    membership?.status === "pending_payment" &&
    charge?.payment_status === "pending"
  );
}

export function createPendingDemoMembership(user: User): {
  charges: MembershipCharge[];
  membership: Membership;
} {
  return {
    membership: {
      id: `membership-${user.id}`,
      user_id: user.id,
      member_number: null,
      status: "pending_payment",
      directory_visible: false,
      nickname: user.display_name,
      expertise: null,
      bio: null,
    },
    charges: [
      {
        id: `charge-joining-${user.id}`,
        charge_type: "joining_fee",
        amount: 500,
        payment_status: "pending",
        receipt_number: null,
        paid_at: null,
      },
      {
        id: `charge-share-${user.id}`,
        charge_type: "share_capital",
        amount: 1000,
        payment_status: "pending",
        receipt_number: null,
        paid_at: null,
      },
    ],
  };
}

export function activateDemoMembership(
  user: User,
  membership: Membership | null,
  charges: MembershipCharge[],
  memberNumber: string,
  activatedAt: string,
) {
  if (membership?.status !== "pending_payment") return false;
  const paidTypes = new Set(
    charges
      .filter((charge) => charge.payment_status === "paid")
      .map((charge) => charge.charge_type),
  );
  if (!paidTypes.has("joining_fee") || !paidTypes.has("share_capital")) {
    return false;
  }
  membership.status = "active";
  membership.member_number = memberNumber;
  membership.started_at = activatedAt;
  user.membership_type = "member";
  return true;
}
