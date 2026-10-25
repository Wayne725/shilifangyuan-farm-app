import { Link, useLocation } from "@tanstack/react-router";

const groups = [
  { label: "訂餐營運", links: [{to: "/admin", label: "訂單營運"}, {to: "/admin/meals", label: "便當管理"}, {to: "/admin/kitchen", label: "廚房出單"}] },
  { label: "商品", links: [{to: "/admin/catalog", label: "商品供應"}, {to: "/admin/groups", label: "共同購買管理"}, {to: "/admin/promotions", label: "促銷管理"}] },
  { label: "會員社務", links: [{to: "/admin/users", label: "會員管理"}, {to: "/admin/social", label: "社務管理"}, {to: "/admin/records", label: "社員帳冊"}] },
  { label: "財務", links: [{to: "/admin/finance", label: "銷售統計"}, {to: "/admin/accounting", label: "會計匯出"}, {to: "/admin/statements", label: "明細對帳"}, {to: "/admin/invoices", label: "發票"}] },
  { label: "設定", links: [{to: "/admin/settings", label: "平台設定"}] },
] as const;

export function AdminNav() {
  const pathname = useLocation({select: location => location.pathname});
  const current = groups.find(group => group.links.some(link => link.to === pathname)) || groups[0];
  return <div className="admin-navigation">
    <nav className="admin-workspace-links" aria-label="管理工作台分類">
      {groups.map(group => <Link key={group.label} to={group.links[0].to} activeOptions={{exact: true}} className={group === current ? "active" : ""} aria-current={group === current ? "true" : undefined}>{group.label}</Link>)}
    </nav>
    <nav className="admin-section-links" aria-label={`${current.label}功能`}>
      {current.links.map(link => <Link key={link.to} to={link.to} activeOptions={{exact: true, includeSearch: false}} activeProps={{className: "active", "aria-current": "page"}}>{link.label}</Link>)}
    </nav>
  </div>;
}
