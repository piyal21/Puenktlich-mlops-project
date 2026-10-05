import { Activity, Info, TrainFront, type LucideIcon } from "lucide-react";
import { Link, NavLink, useLocation } from "react-router";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}
const NAV: NavItem[] = [
  { to: "/", label: "Board", icon: TrainFront },
  { to: "/health", label: "Health", icon: Activity },
  { to: "/about", label: "About", icon: Info },
];

export function TopBar() {
  const { pathname } = useLocation();
  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex h-16 max-w-[960px] items-center justify-between px-4 md:px-6">
        <Link to="/" className="rounded-md text-xl font-[650] text-text">
          Pünktlich<span className="text-primary">?</span>
        </Link>
        <nav aria-label="Main">
          <ul className="flex gap-1">
            {NAV.map(({ to, label, icon: Icon }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end
                  className={({ isActive }) => {
                    const active = isActive || (to === "/" && pathname.startsWith("/station/"));
                    return `flex min-h-11 min-w-11 flex-col items-center justify-center gap-0.5 rounded-md px-2 text-xs transition-colors duration-150 sm:flex-row sm:gap-2 sm:px-3 sm:text-sm ${
                      active ? "bg-primary-soft text-primary" : "text-muted hover:text-text"
                    }`;
                  }}
                >
                  <Icon aria-hidden="true" className="size-5" />
                  <span>{label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  );
}
