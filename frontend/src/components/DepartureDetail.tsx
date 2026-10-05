import { ArrowDown, ArrowUp, X } from "lucide-react";
import { useEffect, useId, useRef, type KeyboardEvent } from "react";
import type { Departure } from "../api/types";
import { formatDate, formatTime } from "../lib/format";
import { ProbabilityBar } from "./ProbabilityBar";

interface Props {
  departure: Departure;
  thresholds: { medium: number; high: number } | null;
  modelVersion: string | null;
  trainedAt: string | null;
  dataAsOf: string;
  onClose: () => void;
}

const FOCUSABLE = 'button, [href], input, [tabindex]:not([tabindex="-1"])';

/** Bottom sheet on phones, side panel from 1024 px; modal with focus trap (design.md §6). */
export function DepartureDetail({
  departure,
  thresholds,
  modelVersion,
  trainedAt,
  dataAsOf,
  onClose,
}: Props) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden"; // the page behind a modal must not scroll
    closeButton.current?.focus();
    return () => {
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !panel.current) return;
    const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    const first = items[0];
    const last = items[items.length - 1];
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const { train, prediction } = departure;
  const footer = [
    modelVersion ? `Model v${modelVersion}` : "No model",
    trainedAt ? `trained ${formatDate(trainedAt)}` : null,
    `data as of ${formatTime(dataAsOf)}`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      className="fixed inset-0 z-40 flex items-end bg-overlay lg:items-stretch lg:justify-end"
      onClick={onClose}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        onClick={(event) => event.stopPropagation()}
        className="max-h-[85vh] w-full overflow-y-auto rounded-t-lg bg-surface p-4 shadow-card lg:h-full lg:max-h-none lg:w-[420px] lg:rounded-none lg:p-6"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id={titleId} className="text-xl font-semibold">
              <span className="font-mono tabular">{formatTime(departure.planned_departure)}</span>{" "}
              {train.type} {train.number ?? ""} <span aria-hidden="true">→</span>{" "}
              <span lang="de">{train.destination ?? "Destination unknown"}</span>
            </h2>
            <p className="text-sm text-muted">
              {departure.platform ? `Platform ${departure.platform}` : "Platform not known yet"}
              {train.line ? ` · Line ${train.line}` : ""}
            </p>
          </div>
          <button
            ref={closeButton}
            type="button"
            onClick={onClose}
            aria-label="Close details"
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-md hover:bg-surface-2"
          >
            <X aria-hidden="true" className="size-5" />
          </button>
        </div>

        <div className="mt-6 space-y-6">
          {departure.cancelled ? (
            <p>This departure is cancelled.</p>
          ) : prediction ? (
            <>
              <ProbabilityBar prediction={prediction} thresholds={thresholds} />
              <section aria-labelledby={`${titleId}-why`}>
                <h3 id={`${titleId}-why`} className="text-base font-semibold">
                  Why?
                </h3>
                <ul className="mt-2 space-y-2">
                  {prediction.top_factors.map((factor) => (
                    <li key={factor.feature} className="flex items-start gap-2">
                      {factor.direction === "up" ? (
                        <ArrowUp
                          aria-hidden="true"
                          className="mt-0.5 size-4 shrink-0 text-risk-high"
                        />
                      ) : (
                        <ArrowDown
                          aria-hidden="true"
                          className="mt-0.5 size-4 shrink-0 text-risk-low"
                        />
                      )}
                      <span>
                        {factor.text}
                        <span className="sr-only">
                          {factor.direction === "up" ? " (raises the risk)" : " (lowers the risk)"}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            </>
          ) : (
            <p>No forecast for this departure right now.</p>
          )}
        </div>

        <p className="mt-8 text-xs font-medium text-muted">{footer}</p>
      </div>
    </div>
  );
}
