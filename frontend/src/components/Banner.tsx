import { Clock, Info, OctagonAlert } from "lucide-react";
import type { ReactNode } from "react";

type Variant = "info" | "stale" | "error";
const STYLE: Record<Variant, string> = {
  info: "bg-primary-soft text-text",
  stale: "bg-accent-soft text-accent-ink",
  error: "bg-risk-high-soft text-text",
};
const ICON = { info: Info, stale: Clock, error: OctagonAlert } as const;

export function Banner({
  variant,
  children,
  action,
}: {
  variant: Variant;
  children: ReactNode;
  action?: ReactNode;
}) {
  const Icon = ICON[variant];
  return (
    <div
      role={variant === "error" ? "alert" : "status"}
      className={`flex flex-wrap items-center gap-3 rounded-md px-4 py-3 text-sm ${STYLE[variant]}`}
    >
      <Icon aria-hidden="true" className="size-4 shrink-0" />
      <div className="min-w-0 flex-1">{children}</div>
      {action}
    </div>
  );
}
