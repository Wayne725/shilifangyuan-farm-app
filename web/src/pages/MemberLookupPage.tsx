import { Navigate, useSearch } from "@tanstack/react-router";
import { lineLoginReturnTo } from "../lib/login-navigation";

export function MemberLookupPage() {
  const search = useSearch({ from: "/member-lookup" });
  return <Navigate to="/register" search={{
    intent: "existing_member",
    return_to: lineLoginReturnTo(search.return_to || "/shop"),
    remember_me: search.remember_me !== false,
  }} replace />;
}
