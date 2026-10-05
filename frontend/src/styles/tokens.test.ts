import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// Read from disk (path relative to frontend/, where npm runs vitest): Vitest blanks CSS
// imports (css: false), including `?raw`.
const css = readFileSync("src/styles/tokens.css", "utf8");

// WCAG 2.1 AA 1.4.3: badge and banner text is 14 px, so every pair needs >= 4.5:1.
const PAIRS: [string, string][] = [
  ["--risk-low", "--risk-low-soft"],
  ["--risk-medium", "--risk-medium-soft"],
  ["--risk-high", "--risk-high-soft"],
  ["--cancelled", "--cancelled-soft"],
  ["--accent-ink", "--accent-soft"],
  ["--text-muted", "--surface-2"],
  ["--risk-high", "--surface"],
  ["--risk-medium", "--surface"],
  ["--primary-fg", "--primary"],
];

function block(selector: string): Record<string, string> {
  const start = css.indexOf(selector);
  const body = css.slice(css.indexOf("{", start) + 1, css.indexOf("}", start));
  return Object.fromEntries(
    [...body.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]),
  );
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

describe("design tokens", () => {
  const light = block(":root {");
  const dark = { ...light, ...block(':root[data-theme="dark"] {') };
  it.each(PAIRS)("%s on %s meets 4.5:1 in both themes", (fg, bg) => {
    for (const theme of [light, dark]) {
      const [a, b] = [theme[fg], theme[bg]];
      expect(a && b, `${fg} / ${bg} defined`).toBeTruthy();
      expect(contrast(a as string, b as string)).toBeGreaterThanOrEqual(4.5);
    }
  });
});
