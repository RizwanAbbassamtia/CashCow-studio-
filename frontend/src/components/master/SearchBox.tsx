import { Search } from "lucide-react";

import { cn } from "../../lib/cn";
import { Input } from "../ui/Input";

export interface SearchBoxProps {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  /** read aloud by screen readers; defaults to the placeholder */
  label?: string;
  className?: string;
}

/** The search field above a list: an input with a magnifier, cleared with Escape. */
export function SearchBox({ value, onChange, placeholder, label, className }: SearchBoxProps) {
  return (
    <div className={cn("relative", className)}>
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-faint" aria-hidden />
      <Input
        type="search"
        placeholder={placeholder}
        className="pl-9"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && value) {
            event.stopPropagation();
            onChange("");
          }
        }}
        aria-label={label ?? placeholder}
      />
    </div>
  );
}
