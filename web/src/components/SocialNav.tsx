import {
  CalendarBlank,
  CalendarCheck,
  HandHeart,
  IdentificationCard,
  NotePencil,
  UsersThree,
} from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";

const items = [
  { to: "/social" as const, label: "社務首頁", icon: UsersThree },
  { to: "/activities" as const, label: "社員活動", icon: CalendarBlank },
  { to: "/governance" as const, label: "社員提案", icon: NotePencil },
  { to: "/meetings" as const, label: "社員會議", icon: CalendarCheck },
  { to: "/wishes" as const, label: "社員願望", icon: HandHeart },
  { to: "/directory" as const, label: "社員名錄", icon: IdentificationCard },
];

export function SocialNav() {
  return (
    <nav className="social-subnav" aria-label="社務功能">
      {items.map(({ to, label, icon: Icon }) => (
        <Link key={to} to={to} activeProps={{ className: "active" }}>
          <Icon size={17} weight="light" />
          {label}
        </Link>
      ))}
    </nav>
  );
}
