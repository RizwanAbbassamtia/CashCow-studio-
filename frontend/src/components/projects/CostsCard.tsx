import type { ProjectCosts } from "../../types/project";
import { totalCost } from "../../types/project";
import { Card } from "../ui/Card";
import { formatUsd } from "./stageMeta";

/** What this project has spent so far on AI calls, by kind. */
export function CostsCard({ costs }: { costs: ProjectCosts }) {
  const rows = [
    { label: "Writing (Claude)", value: costs.llm_usd },
    { label: "Voice", value: costs.voice_usd },
    { label: "Images", value: costs.images_usd },
  ];
  return (
    <Card title="Costs so far" description="Estimated from the provider price tables.">
      <dl className="flex flex-col gap-2 text-sm">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between gap-4">
            <dt className="text-ink-muted">{row.label}</dt>
            <dd className="tabular-nums text-ink">{formatUsd(row.value)}</dd>
          </div>
        ))}
        <div className="mt-1 flex items-center justify-between gap-4 border-t border-line pt-2 font-semibold">
          <dt className="text-ink">Total</dt>
          <dd className="tabular-nums text-ink">{formatUsd(totalCost(costs))}</dd>
        </div>
      </dl>
    </Card>
  );
}
