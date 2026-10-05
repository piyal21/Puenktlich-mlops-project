export type Theme = "system" | "light" | "dark";
const KEY = "puenktlich.theme";

export function readTheme(): Theme {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  try {
    if (theme === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, theme);
  } catch {
    // Storage blocked (private mode): the choice lasts for this page view only.
  }
}

export const nextTheme = (theme: Theme): Theme =>
  theme === "system" ? "light" : theme === "light" ? "dark" : "system";
