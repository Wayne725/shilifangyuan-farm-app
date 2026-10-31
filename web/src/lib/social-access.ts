import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./api";
import type { MembershipSummary, User } from "./types";

export function useMembership(user: User | null) {
  return useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: Boolean(user),
  });
}

export function canViewSocial(user: User | null, confirmed?: MembershipSummary): boolean {
  const kind = confirmed?.membership_type ?? user?.membership_type;
  const status = confirmed?.membership_type ? confirmed.membership?.status : user?.membership_status;
  return user?.user_role === "admin" || (Boolean(user) && kind === "member"
    && (status == null || status === "active"));
}

export function useSocialAccess(user: User | null): boolean {
  const confirmed = useQuery({
    queryKey: ["membership-me", user?.id],
    queryFn: () => apiFetch<MembershipSummary>("/v1/members/me"),
    enabled: false,
  });
  return canViewSocial(user, confirmed.data);
}
