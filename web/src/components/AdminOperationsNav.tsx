import { Link } from "@tanstack/react-router";

type Section = "orders" | "summary" | "issues" | "suppliers" | "locations";

const items: Array<{ section: Section; label: string }> = [
  { section: "orders", label: "訂單、付款與發票" },
  { section: "summary", label: "訂單彙整" },
  { section: "issues", label: "問題回報" },
  { section: "suppliers", label: "供應者與審核狀態" },
  { section: "locations", label: "取貨點維護" },
];

export function AdminOperationsNav({ section }: { section: Section }) {
  return <nav className="tab-list admin-operation-nav" aria-label="訂單營運子頁面">
    {items.map((item) => <Link key={item.section} to="/admin" search={{ section: item.section }} data-state={section === item.section ? "active" : "inactive"} aria-current={section === item.section ? "page" : undefined}>{item.label}</Link>)}
  </nav>;
}
