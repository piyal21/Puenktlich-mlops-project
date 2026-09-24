# Design — Pünktlich

> Visual language for the web app: colors, typography, spacing, components, states.
> Goal: **calm, fast, trustworthy** — a tool you glance at on a platform in 3 seconds.
> Implemented with Tailwind CSS v4 + CSS variables. Components must use tokens, never raw hex values.

---

## 1. Principles

1. **Glanceable.** The departure time and the risk level are readable at arm's length on a phone.
2. **Honest.** Show probabilities, data age and model version. Never hide uncertainty.
3. **Accessible.** WCAG 2.1 AA. Risk is always **icon + word + number**, never color alone.
4. **Our own identity.** Must **not** resemble Deutsche Bahn: no DB logo, no DB red (`#EC0016`), no DB typefaces, no DB product colors for train types.
5. **Mobile-first.** Design at 360 px, then scale up. Max content width 960 px.

---

## 2. Color tokens

### 2.1 Light theme (default)

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#F6F7F9` | Page background |
| `--surface` | `#FFFFFF` | Cards, rows, sheets |
| `--surface-2` | `#EEF1F5` | Chips, inputs, hover backgrounds |
| `--border` | `#DDE2E8` | Dividers, card borders |
| `--text` | `#0E1726` | Primary text |
| `--text-muted` | `#5B6472` | Secondary text, captions (6.0:1 on white) |
| `--primary` | `#2F5BEA` | Links, primary buttons, focus ring, chart series (5.5:1 on white) |
| `--primary-hover` | `#2449C4` | Hover/pressed |
| `--primary-fg` | `#FFFFFF` | Text on primary |
| `--primary-soft` | `#E8EEFF` | Selected states, info banners |
| `--accent` | `#F5B700` | "Platform yellow" — tiny highlights only (live dot, logo mark). Never for text on white. |
| `--accent-soft` | `#FFF4CC` | Stale-data banner background (text: `--accent-ink`) |
| `--accent-ink` | `#5C4400` | Text on `--accent-soft` |
| `--risk-low` | `#12805C` | Low risk text/icon (4.9:1 on white) |
| `--risk-low-soft` | `#E3F4EC` | Low risk badge background |
| `--risk-medium` | `#9A5B00` | Medium risk text/icon (5.4:1 on white) |
| `--risk-medium-soft` | `#FDF0DC` | Medium risk badge background |
| `--risk-high` | `#C2362F` | High risk text/icon (5.4:1 on white) |
| `--risk-high-soft` | `#FBE5E3` | High risk badge background |
| `--cancelled` | `#5B6472` | Cancelled text/icon |
| `--cancelled-soft` | `#ECEEF1` | Cancelled badge background |

### 2.2 Dark theme

| Token | Hex |
|---|---|
| `--bg` | `#0B0F17` |
| `--surface` | `#131A24` |
| `--surface-2` | `#1A2330` |
| `--border` | `#263243` |
| `--text` | `#E6EAF0` |
| `--text-muted` | `#97A3B6` |
| `--primary` | `#7B98FF` |
| `--primary-hover` | `#9AB0FF` |
| `--primary-fg` | `#0B0F17` |
| `--primary-soft` | `#1B2644` |
| `--accent` | `#F5B700` |
| `--accent-soft` | `#2E2710` |
| `--accent-ink` | `#FFD968` |
| `--risk-low` / `-soft` | `#3FBF8F` / `#0F2A21` |
| `--risk-medium` / `-soft` | `#F0A43A` / `#2E2210` |
| `--risk-high` / `-soft` | `#FF7A70` / `#3A1716` |
| `--cancelled` / `-soft` | `#97A3B6` / `#1F2632` |

Theme follows `prefers-color-scheme`; a toggle in the footer sets `data-theme="light|dark"` on `<html>` (remembered in `localStorage`, wrapped in try/catch).

### 2.3 Chart colors
- Main series: `--primary`. Second series: `--text-muted`.
- Reference line (champion test score): `--text-muted`, dashed.
- Threshold line (drift/alert threshold): `--risk-high`, dashed.
- Gridlines: `--border`. No 3D, no gradients, no pie charts.

---

## 3. Typography

Fonts are **self-hosted** (npm `@fontsource-variable/inter`, `@fontsource/jetbrains-mono`) — no requests to Google servers (GDPR-friendly, and faster).

| Role | Font | Size / line-height | Weight | Notes |
|---|---|---|---|---|
| Display | Inter | 32 / 40 (mobile 28 / 36) | 650 | Page titles |
| H1 | Inter | 24 / 32 | 600 | Section titles |
| H2 | Inter | 20 / 28 | 600 | Card titles |
| H3 | Inter | 16 / 24 | 600 | Row titles |
| Body | Inter | 16 / 24 | 400 | Default text |
| Body small | Inter | 14 / 20 | 400 | Secondary info |
| Caption | Inter | 12 / 16 | 500 | Labels, metadata; `letter-spacing: 0.01em` |
| Time | JetBrains Mono | 20 / 28 | 500 | Departure times — `font-variant-numeric: tabular-nums` |
| Code / ids | JetBrains Mono | 13 / 20 | 400 | Model version, EVA ids, API examples |

Rules: sentence case everywhere (no ALL CAPS except train types like `ICE`). 24-hour times (`17:42`), local Europe/Berlin. Percentages as integers (`37 %` in German locale, `37%` in English).

---

## 4. Spacing, radius, elevation

- **Spacing scale (4 px base):** 4, 8, 12, 16, 24, 32, 48, 64. Page gutter 16 px (mobile) / 24 px (≥ 768 px).
- **Radius:** `sm` 6 px (chips), `md` 10 px (inputs, buttons, badges), `lg` 14 px (cards, sheets), `full` (pills, avatars).
- **Elevation:** light theme cards use `0 1px 2px rgb(14 23 38 / 0.06), 0 1px 1px rgb(14 23 38 / 0.04)`; dark theme uses borders only, no shadows.
- **Touch targets:** ≥ 44 × 44 px.
- **Breakpoints:** `sm` 640, `md` 768, `lg` 1024.

---

## 5. Iconography & motion

- Icons: **lucide-react**, 20 px (inline 16 px), stroke 2, `currentColor`.
- Risk icons: Low `CheckCircle2`, Medium `AlertTriangle`, High `AlertOctagon`, Cancelled `XCircle`, No forecast `HelpCircle`, Live `Radio`, Stale `Clock`.
- Motion: 150 ms `ease-out` for hover/press, 200 ms for sheets. No motion that conveys information. Respect `prefers-reduced-motion: reduce` (disable transitions).

---

## 6. Components

### RiskBadge
Pill: soft background + colored icon + label + probability.
```
[✓ Low · 12%]   [⚠ Medium · 34%]   [⛔ High · 61%]   [✕ Cancelled]   [? No forecast]
```
- Text uses the strong risk color on the soft background (all pairs ≥ 4.5:1).
- `aria-label="Delay risk medium, 34 percent"`.

### DepartureRow
```
┌──────────────────────────────────────────────────────────┐
│ 17:42        RE 4711 · RE1                [⚠ Medium · 34%] │
│ 17:45 +3     → Göttingen        Pl. 3                     │
└──────────────────────────────────────────────────────────┘
```
- Left: planned time (Time style). Below: live time + delay (`+3`) in `--risk-medium`/`--risk-high` if delayed, `--text-muted` if on time.
- Middle: train chip (`surface-2`, Caption, mono number) + line; destination (H3); platform (Body small, muted).
- Right: RiskBadge.
- Cancelled: planned time struck through, `Cancelled` badge, row at 70 % opacity.
- Whole row is a button → opens DepartureDetail.

### DepartureDetail (bottom sheet on mobile, side panel ≥ 1024 px)
- Header: time, train, destination, platform.
- **ProbabilityBar:** 0–100 % track (`surface-2`), filled to `p_late` in the risk color, tick marks at the risk thresholds (20 %, 45 %) with labels.
- "Why?" list: top 3 factors from the API (`↑` raises risk / `↓` lowers risk) in plain language.
- Footer (Caption, muted): `Model v12 · trained 21 Sep 2026 · data as of 15:30`.

### StationSearch
- Combobox (ARIA pattern), placeholder "Search a station, e.g. Erfurt Hbf". Keyboard: ↑ ↓ Enter Esc.
- Recent stations as chips below (stored locally, max 5).

### MetricCard (Model Health)
- Caption label, big value (H1, tabular nums), small delta vs. champion test score (↑/↓ + color + text).

### Banner
- Variants: `info` (primary-soft), `stale` (accent-soft + Clock icon: "Live data is 27 min old"), `error` (risk-high-soft).

### Buttons
- Primary: `--primary` bg, `--primary-fg` text, radius `md`, height 44 px.
- Secondary: `--surface` bg, `--border` border, `--text`.
- Ghost: text only, `--primary`.
- Focus: 2 px ring `--primary` with 2 px offset (always visible on keyboard focus).

---

## 7. Pages

| Page | Route | Content |
|---|---|---|
| Home / Board | `/` and `/station/:eva` | Search at top; board list for the next 3 h; "Updated 15:30" + Live dot; hours filter (1/3/6 h) |
| Model Health | `/health` | MetricCards (model version, trained at, yesterday's Brier, AUC, drift status, data freshness); Brier daily line chart with champion reference line; drift share bar chart with threshold; model history table (version, date, gate result); link to latest drift report |
| About | `/about` | What it is, how it works (small diagram), limitations, GitHub link, data attribution, disclaimer |

Top bar: wordmark left, nav right (Board · Health · About). On < 640 px, nav collapses into 3 icon buttons with labels underneath.

Footer (every page, Caption, muted):
> Data: Deutsche Bahn Timetables API & piebro/deutsche-bahn-data (CC BY 4.0). Pünktlich is an independent student project and is **not affiliated with Deutsche Bahn**. Predictions are estimates.

---

## 8. States (every data view must implement all)

| State | Pattern |
|---|---|
| Loading | Skeleton rows (3–5), no spinners on full pages |
| Empty | Icon + "No departures in the next 3 hours" + button to widen to 6 h |
| Error | Error banner + "Try again" button; keep showing last good data if available |
| Stale | Stale banner when `data_as_of` > 20 min |
| No model | Rows show `? No forecast`; info banner "Forecasts are temporarily unavailable" |

---

## 9. Voice & copy

- Short, plain, friendly English. Station names in German as-is (`München Hbf`).
- Explain risk without jargon: "34 % chance of leaving 6+ minutes late".
- Never overclaim: "estimate", "usually", "based on the last 9 months".
- Button labels are verbs: "Search", "Try again", "Show 6 hours".

---

## 10. Brand mark

- Wordmark: **Pünktlich?** in Inter 650; the `?` in `--primary`.
- Logo mark / favicon: rounded square in `--primary` with a simple white clock glyph and a small `--accent` dot. SVG, 32 × 32 and 180 × 180 (apple-touch-icon).

---

## 11. Implementation (Tailwind v4)

`frontend/src/styles/tokens.css`
```css
@import "tailwindcss";

:root {
  --bg:#F6F7F9; --surface:#FFFFFF; --surface-2:#EEF1F5; --border:#DDE2E8;
  --text:#0E1726; --text-muted:#5B6472;
  --primary:#2F5BEA; --primary-hover:#2449C4; --primary-fg:#FFFFFF; --primary-soft:#E8EEFF;
  --accent:#F5B700; --accent-soft:#FFF4CC; --accent-ink:#5C4400;
  --risk-low:#12805C; --risk-low-soft:#E3F4EC;
  --risk-medium:#9A5B00; --risk-medium-soft:#FDF0DC;
  --risk-high:#C2362F; --risk-high-soft:#FBE5E3;
  --cancelled:#5B6472; --cancelled-soft:#ECEEF1;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#0B0F17; --surface:#131A24; --surface-2:#1A2330; --border:#263243;
    --text:#E6EAF0; --text-muted:#97A3B6;
    --primary:#7B98FF; --primary-hover:#9AB0FF; --primary-fg:#0B0F17; --primary-soft:#1B2644;
    --accent-soft:#2E2710; --accent-ink:#FFD968;
    --risk-low:#3FBF8F; --risk-low-soft:#0F2A21;
    --risk-medium:#F0A43A; --risk-medium-soft:#2E2210;
    --risk-high:#FF7A70; --risk-high-soft:#3A1716;
    --cancelled:#97A3B6; --cancelled-soft:#1F2632;
  }
}
:root[data-theme="dark"] { /* same values as the dark block above */ }

@theme inline {
  --color-bg: var(--bg);
  --color-surface: var(--surface);
  --color-surface-2: var(--surface-2);
  --color-border: var(--border);
  --color-text: var(--text);
  --color-muted: var(--text-muted);
  --color-primary: var(--primary);
  --color-primary-hover: var(--primary-hover);
  --color-primary-fg: var(--primary-fg);
  --color-primary-soft: var(--primary-soft);
  --color-accent: var(--accent);
  --color-accent-soft: var(--accent-soft);
  --color-accent-ink: var(--accent-ink);
  --color-risk-low: var(--risk-low);
  --color-risk-low-soft: var(--risk-low-soft);
  --color-risk-medium: var(--risk-medium);
  --color-risk-medium-soft: var(--risk-medium-soft);
  --color-risk-high: var(--risk-high);
  --color-risk-high-soft: var(--risk-high-soft);
  --color-cancelled: var(--cancelled);
  --color-cancelled-soft: var(--cancelled-soft);
  --font-sans: "Inter Variable", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
  --radius-sm: 6px; --radius-md: 10px; --radius-lg: 14px;
}

body { background: var(--bg); color: var(--text); font-family: var(--font-sans); }
.tabular { font-variant-numeric: tabular-nums; }
```
Usage: `bg-surface text-text border-border`, `text-risk-high bg-risk-high-soft`, `font-mono tabular`.

---

## 12. Accessibility checklist
- [ ] Text contrast ≥ 4.5:1 (large text ≥ 3:1) in both themes.
- [ ] Risk conveyed by icon + text + number.
- [ ] All interactive elements reachable and operable by keyboard; visible focus.
- [ ] Combobox, sheet and nav follow WAI-ARIA patterns; sheet traps focus and closes on Esc.
- [ ] `lang="en"` on `<html>`; station names wrapped with `lang="de"`.
- [ ] Charts have a text summary and a data table alternative.
- [ ] Works at 200 % zoom and 360 px width without horizontal scroll.