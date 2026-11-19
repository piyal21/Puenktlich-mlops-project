import { useState } from "react";
import { formatDate } from "../../lib/format";

export interface Series {
  label: string;
  /** CSS colour, e.g. "var(--series-1)" (dataviz-validated tokens). */
  color: string;
  values: (number | null)[];
}

export interface Reference {
  label: string;
  value: number;
}

interface Point {
  day: string;
  x: number;
  values: (number | null)[];
}

const W = 400;
const H = 170;
const PAD = { top: 12, right: 78, bottom: 26, left: 44 };
const MUTED = "fill-[var(--text-muted)] text-[12px]";

function domain(series: Series[], reference?: Reference): [number, number] {
  const values = series.flatMap((s) => s.values).filter((v): v is number => v !== null);
  if (reference) values.push(reference.value);
  if (values.length === 0) return [0, 1];
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (hi - lo < 1e-9) [lo, hi] = [lo - 0.05, hi + 0.05];
  const pad = (hi - lo) * 0.1;
  return [Math.max(0, lo - pad), hi + pad];
}

function lastValue(values: (number | null)[]): number | null {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    const v = values[i];
    if (v !== null && v !== undefined) return v;
  }
  return null;
}

/** A small daily line chart: thin lines, a dashed reference, hover readout, table alternative. */
export function TrendChart({
  title,
  days,
  series,
  reference,
  format,
}: {
  title: string;
  days: string[];
  series: Series[];
  reference?: Reference;
  format: (v: number) => string;
}) {
  const [hover, setHover] = useState<Point | null>(null);
  const [lo, hi] = domain(series, reference);
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const y = (v: number) => PAD.top + plotH - ((v - lo) / (hi - lo)) * plotH;
  const points: Point[] = days.map((day, i) => ({
    day,
    x: PAD.left + (days.length <= 1 ? plotW / 2 : (i / (days.length - 1)) * plotW),
    values: series.map((s) => s.values[i] ?? null),
  }));
  const first = points[0];
  const last = points.at(-1);
  const ticks = [lo, (lo + hi) / 2, hi];

  function path(index: number): string {
    let d = "";
    let pen = false;
    for (const p of points) {
      const v = p.values[index] ?? null;
      if (v === null) {
        pen = false;
        continue;
      }
      d += `${pen ? "L" : "M"}${p.x.toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    }
    return d;
  }

  // Direct end labels (<= 4 series), nudged apart when they would collide.
  const ends = series
    .map((s) => ({ label: s.label, value: lastValue(s.values) }))
    .filter((e): e is { label: string; value: number } => e.value !== null)
    .map((e) => ({ label: e.label, y: y(e.value) }))
    .sort((a, b) => a.y - b.y)
    .reduce<{ label: string; y: number }[]>((acc, e) => {
      const prev = acc.at(-1);
      acc.push(prev && e.y - prev.y < 14 ? { ...e, y: prev.y + 14 } : e);
      return acc;
    }, []);

  const latest = series
    .map((s) => {
      const v = lastValue(s.values);
      return `${s.label} ${v === null ? "no value" : format(v)}`;
    })
    .join(", ");
  const summary = `${title}: ${days.length} days, latest ${latest}${
    reference ? `; ${reference.label} ${format(reference.value)}` : ""
  }.`;

  function onMove(event: React.MouseEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width === 0) return;
    const px = ((event.clientX - box.left) / box.width) * W;
    let best: Point | null = null;
    for (const p of points) {
      if (best === null || Math.abs(p.x - px) < Math.abs(best.x - px)) best = p;
    }
    setHover(best);
  }

  return (
    <figure className="rounded-lg border border-border bg-surface p-4 shadow-card">
      <figcaption className="text-sm font-semibold">{title}</figcaption>
      {series.length > 1 ? (
        <ul className="mt-2 flex flex-wrap gap-4 text-xs text-muted" aria-hidden="true">
          {series.map((s) => (
            <li key={s.label} className="flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-4 rounded" style={{ background: s.color }} />
              {s.label}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="relative mt-2">
        <svg
          role="img"
          aria-label={summary}
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line
                x1={PAD.left}
                x2={W - PAD.right}
                y1={y(t)}
                y2={y(t)}
                stroke="var(--border)"
                strokeWidth={1}
              />
              <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" className={`${MUTED} tabular`}>
                {format(t)}
              </text>
            </g>
          ))}
          {first ? (
            <text x={first.x} y={H - 6} className={MUTED}>
              {formatDate(first.day)}
            </text>
          ) : null}
          {last && last !== first ? (
            <text x={last.x} y={H - 6} textAnchor="end" className={MUTED}>
              {formatDate(last.day)}
            </text>
          ) : null}
          {reference ? (
            <g>
              <line
                x1={PAD.left}
                x2={W - PAD.right}
                y1={y(reference.value)}
                y2={y(reference.value)}
                stroke="var(--text-muted)"
                strokeWidth={1}
                strokeDasharray="4 4"
              />
              <text x={W - PAD.right + 6} y={y(reference.value) + 4} className={MUTED}>
                {reference.label}
              </text>
            </g>
          ) : null}
          {series.map((s, index) => (
            <path
              key={s.label}
              d={path(index)}
              fill="none"
              stroke={s.color}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ))}
          {series.length > 1
            ? ends.map((e) => (
                <text
                  key={e.label}
                  x={W - PAD.right + 6}
                  y={e.y + 4}
                  className="fill-[var(--text)] text-[12px]"
                >
                  {e.label}
                </text>
              ))
            : null}
          {hover ? (
            <g>
              <line
                x1={hover.x}
                x2={hover.x}
                y1={PAD.top}
                y2={PAD.top + plotH}
                stroke="var(--text-muted)"
                strokeWidth={1}
              />
              {series.map((s, index) => {
                const v = hover.values[index] ?? null;
                return v === null ? null : (
                  <circle
                    key={s.label}
                    cx={hover.x}
                    cy={y(v)}
                    r={4}
                    fill={s.color}
                    stroke="var(--surface)"
                    strokeWidth={2}
                  />
                );
              })}
            </g>
          ) : null}
        </svg>
        {hover ? (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute top-0 rounded-md border border-border bg-surface px-2 py-1 text-xs shadow-card"
            style={{
              left: `${(hover.x / W) * 100}%`,
              transform: hover.x > W / 2 ? "translateX(-105%)" : "translateX(5%)",
            }}
          >
            <p className="font-medium">{formatDate(hover.day)}</p>
            {series.map((s, index) => {
              const v = hover.values[index] ?? null;
              return (
                <p key={s.label} className="tabular text-muted">
                  {s.label}: {v === null ? "—" : format(v)}
                </p>
              );
            })}
          </div>
        ) : null}
      </div>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted">Show data table</summary>
        <table aria-label={`${title} (table)`} className="mt-2 w-full text-left tabular">
          <thead>
            <tr>
              <th scope="col" className="py-1 font-medium">
                Day
              </th>
              {series.map((s) => (
                <th key={s.label} scope="col" className="py-1 font-medium">
                  {s.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.day}>
                <th scope="row" className="py-0.5 font-normal">
                  {formatDate(p.day)}
                </th>
                {series.map((s, index) => {
                  const v = p.values[index] ?? null;
                  return (
                    <td key={s.label} className="py-0.5">
                      {v === null ? "—" : format(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
