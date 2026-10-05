import { Search } from "lucide-react";
import { useId, useState, type KeyboardEvent } from "react";
import type { Station } from "../api/types";

interface Props {
  query: string;
  onQueryChange: (query: string) => void;
  options: Station[];
  onSelect: (station: Station) => void;
}

/** WAI-ARIA combobox with a listbox popup (Arrow keys, Enter, Escape). */
export function StationSearch({ query, onQueryChange, options, onSelect }: Props) {
  const inputId = useId();
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const expanded = open && query.trim() !== "" && options.length > 0;

  function choose(station: Station) {
    onSelect(station);
    setOpen(false);
    setActive(-1);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    const count = options.length;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setOpen(true);
        setActive((i) => (count === 0 ? -1 : (i + 1) % count));
        break;
      case "ArrowUp":
        event.preventDefault();
        setOpen(true);
        setActive((i) => (count === 0 ? -1 : i <= 0 ? count - 1 : i - 1));
        break;
      case "Enter": {
        const station = options[active] ?? (count === 1 ? options[0] : undefined);
        if (expanded && station) {
          event.preventDefault();
          choose(station);
        }
        break;
      }
      case "Escape":
        setOpen(false);
        setActive(-1);
        break;
    }
  }

  return (
    <div className="relative">
      <label htmlFor={inputId} className="sr-only">
        Station
      </label>
      <div className="flex min-h-11 items-center gap-2 rounded-md border border-border bg-surface px-3 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-primary">
        <Search aria-hidden="true" className="size-5 text-muted" />
        <input
          id={inputId}
          role="combobox"
          aria-expanded={expanded}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={expanded && active >= 0 ? `${listId}-${active}` : undefined}
          value={query}
          placeholder="Search a station, e.g. Erfurt Hbf"
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => {
            onQueryChange(event.target.value);
            setOpen(true);
            setActive(-1);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
          className="h-11 w-full bg-transparent text-base outline-none placeholder:text-muted"
        />
      </div>
      {expanded ? (
        <ul
          id={listId}
          role="listbox"
          aria-label="Stations"
          className="absolute z-30 mt-1 max-h-72 w-full overflow-auto rounded-md border border-border bg-surface py-1 shadow-card"
        >
          {options.map((station, index) => (
            <li
              key={station.eva}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(station)}
              className={`cursor-pointer px-3 py-2.5 ${
                index === active ? "bg-primary-soft" : "hover:bg-surface-2"
              }`}
            >
              <span lang="de">{station.name}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
