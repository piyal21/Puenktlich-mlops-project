import { Monitor, Moon, Sun } from "lucide-react";
import { useState } from "react";
import { applyTheme, nextTheme, readTheme, type Theme } from "../lib/theme";

const THEME_ICON = { system: Monitor, light: Sun, dark: Moon } as const;
const THEME_LABEL: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

export function Footer() {
  const [theme, setTheme] = useState<Theme>(() => readTheme());
  const Icon = THEME_ICON[theme];
  function cycle() {
    const next = nextTheme(theme);
    applyTheme(next);
    setTheme(next);
  }
  return (
    <footer className="mt-12 border-t border-border">
      <div className="mx-auto max-w-[960px] space-y-3 px-4 py-6 text-xs font-medium text-muted md:px-6">
        <p>
          Data: Deutsche Bahn Timetables API &amp; piebro/deutsche-bahn-data (CC BY 4.0). Pünktlich
          is an independent student project and is{" "}
          <strong>not affiliated with Deutsche Bahn</strong>. Predictions are estimates.
        </p>
        <button
          type="button"
          onClick={cycle}
          className="inline-flex min-h-11 items-center gap-2 rounded-md px-2 text-muted hover:text-text"
        >
          <Icon aria-hidden="true" className="size-4" />
          Theme: {THEME_LABEL[theme]}
        </button>
      </div>
    </footer>
  );
}
