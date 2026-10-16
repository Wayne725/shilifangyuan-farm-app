import { Link } from "@tanstack/react-router";

const adminSections = [
  { to: "/admin" as const, label: "訂單營運", exact: true },
  { to: "/admin/catalog" as const, label: "商品供應" },
  { to: "/admin/groups" as const, label: "團購管理" },
  { to: "/admin/meals" as const, label: "便當管理" },
  { to: "/admin/kitchen" as const, label: "廚房出單" },
  { to: "/admin/users" as const, label: "會員管理" },
  { to: "/admin/finance" as const, label: "財務報表" },
  { to: "/admin/social" as const, label: "社務管理" },
  { to: "/admin/records" as const, label: "社員帳冊" },
  { to: "/admin/promotions" as const, label: "促銷管理" },
  { to: "/admin/settings" as const, label: "平台設定" },
];

export function AdminNav() {
  return (
    <nav className="admin-workspace-links" aria-label="管理工作台分類">
      {adminSections.map((item) => (
        <Link
          key={item.to}
          to={item.to}
          activeProps={{ className: "active" }}
          activeOptions={{ exact: item.exact, includeSearch: false }}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
