import type {
  Membership,
  MembershipStatus,
  MembershipType,
  User,
} from "../types";

export function hasMemberPricing(membershipType: MembershipType) {
  return membershipType === "trainee" || membershipType === "member";
}

export function hasFormalMemberAccess(membershipType: MembershipType) {
  return membershipType === "member";
}

export function membershipTypeForStatus(
  status: MembershipStatus,
): MembershipType {
  if (status === "active") return "member";
  if (status === "trainee") return "trainee";
  return "nonmember";
}

export function membershipIdentityNeedsSync(
  status: MembershipStatus,
  currentType: MembershipType,
) {
  return membershipTypeForStatus(status) !== currentType;
}

export function membershipIdentity(
  user: Pick<User, "customer_number" | "membership_type">,
  membership: Pick<Membership, "trainee_number" | "member_number"> | null,
) {
  if (user.membership_type === "member") {
    return {
      label: "正式社員編號",
      number: membership?.member_number ?? "尚未編號",
    };
  }
  if (user.membership_type === "trainee") {
    return {
      label: "實習社員編號",
      number: membership?.trainee_number ?? "尚未編號",
    };
  }
  return {
    label: "一般買家編號",
    number: user.customer_number ?? "尚未編號",
  };
}
