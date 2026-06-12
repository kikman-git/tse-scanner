"use client";

// Tab nav shared by the top-level pages (Scanner / Playbook / Jobs). Detail pages (/<code>)
// highlight Scanner — they're drill-downs of it.

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/", label: "📈 Scanner" },
  { href: "/playbook", label: "📕 Playbook" },
  { href: "/jobs", label: "⚙️ Jobs" },
];

export default function Nav() {
  const path = usePathname() ?? "/";
  const activeHref = path.startsWith("/playbook") ? "/playbook" : path.startsWith("/jobs") ? "/jobs" : "/";
  return (
    <nav className="row" style={{ gap: 0 }}>
      {TABS.map((t) => (
        <Link key={t.href} href={t.href} className={`chip${t.href === activeHref ? " on" : ""}`}
          style={{ textDecoration: "none" }}>
          {t.label}
        </Link>
      ))}
    </nav>
  );
}
