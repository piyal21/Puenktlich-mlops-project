import {
  CircleCheck,
  CircleHelp,
  CircleX,
  OctagonAlert,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import type { Prediction } from "../api/types";
import { percent } from "../lib/format";

interface Variant {
  label: string;
  icon: LucideIcon;
  className: string;
}
const VARIANTS: Record<"low" | "medium" | "high" | "cancelled" | "none", Variant> = {
  low: { label: "Low", icon: CircleCheck, className: "bg-risk-low-soft text-risk-low" },
  medium: {
    label: "Medium",
    icon: TriangleAlert,
    className: "bg-risk-medium-soft text-risk-medium",
  },
  high: { label: "High", icon: OctagonAlert, className: "bg-risk-high-soft text-risk-high" },
  cancelled: { label: "Cancelled", icon: CircleX, className: "bg-cancelled-soft text-cancelled" },
  none: { label: "No forecast", icon: CircleHelp, className: "bg-surface-2 text-muted" },
};

/** Risk as icon + word + number, never colour alone (design.md §6). */
export function RiskBadge({
  prediction,
  cancelled = false,
}: {
  prediction: Prediction | null;
  cancelled?: boolean;
}) {
  const key = cancelled ? "cancelled" : (prediction?.risk_level ?? "none");
  const { label, icon: Icon, className } = VARIANTS[key];
  const shown = !cancelled && prediction ? prediction : null;
  const name = cancelled
    ? "Cancelled"
    : shown
      ? `Delay risk ${label.toLowerCase()}, ${Math.round(shown.p_late * 100)} percent`
      : "No forecast available";
  return (
    <span
      role="img"
      aria-label={name}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-sm font-medium whitespace-nowrap ${className}`}
    >
      <Icon aria-hidden="true" className="size-4" />
      {shown ? `${label} · ${percent(shown.p_late)}` : label}
    </span>
  );
}
