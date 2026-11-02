import { useId } from "react";

interface Props {
  options: string[];
  value: string | null;
  onChange: (destination: string | null) => void;
}

/** Narrows the board to one final stop. Matching stops along the way needs live data (Phase 7). */
export function DestinationFilter({ options, value, onChange }: Props) {
  const id = useId();
  // Keep the choice selectable when the current time window has no train to it.
  const names =
    value !== null && !options.includes(value)
      ? [...options, value].sort((a, b) => a.localeCompare(b, "de"))
      : options;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <label htmlFor={id} className="text-sm font-medium">
        Going to <span className="font-normal text-muted">(final stop of the train)</span>
      </label>
      <select
        id={id}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}
        className="min-h-11 w-full rounded-md border border-border bg-surface px-3 text-base text-text sm:w-auto sm:min-w-64"
      >
        <option value="">All destinations</option>
        {names.map((name) => (
          <option key={name} value={name} lang="de">
            {name}
          </option>
        ))}
      </select>
    </div>
  );
}
