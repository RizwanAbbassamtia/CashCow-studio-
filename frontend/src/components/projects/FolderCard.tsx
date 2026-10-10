import { Copy, FolderOpen } from "lucide-react";

import { errorMessage } from "../../api/client";
import { useOpenProjectFolder } from "../../api/projects";
import type { Project } from "../../types/project";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { useToast } from "../ui/Toast";
import { STAGE_FOLDERS, STAGE_LABELS } from "./stageMeta";

/** The project folder on this computer. The files in it are the truth; the app only indexes them. */
export function FolderCard({ project }: { project: Project }) {
  const { toast } = useToast();
  const openFolder = useOpenProjectFolder();

  const copyPath = async () => {
    try {
      await navigator.clipboard.writeText(project.folder);
      toast({ tone: "success", title: "Folder path copied" });
    } catch {
      toast({ tone: "error", title: "Could not copy", description: "Select the path and copy it by hand." });
    }
  };

  return (
    <Card
      title="Project folder"
      description="Every stage writes its files here before it counts as done."
      actions={
        <>
          <Button variant="ghost" size="sm" icon={<Copy />} onClick={() => void copyPath()} aria-label="Copy folder path">
            Copy
          </Button>
          <Button
            variant="secondary"
            size="sm"
            icon={<FolderOpen />}
            loading={openFolder.isPending}
            onClick={() =>
              openFolder.mutate(project.id, {
                onError: (error) => toast({ tone: "error", title: "Could not open the folder", description: errorMessage(error) }),
              })
            }
          >
            Open folder
          </Button>
        </>
      }
    >
      <p className="select-all break-all font-mono text-xs text-ink">{project.folder}</p>
      <ul className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-ink-faint">
        {(Object.keys(STAGE_FOLDERS) as Array<keyof typeof STAGE_FOLDERS>).map((stage) => (
          <li key={stage} className="flex items-center justify-between gap-2">
            <span className="font-mono">{STAGE_FOLDERS[stage]}</span>
            <span>{STAGE_LABELS[stage]}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
