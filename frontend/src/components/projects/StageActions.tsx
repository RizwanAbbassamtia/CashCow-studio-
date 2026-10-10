import { Check, Play, RotateCcw, SkipForward } from "lucide-react";
import { useState } from "react";

import { useCurrentUser } from "../../api/auth";
import { errorMessage } from "../../api/client";
import { useApproveStage, useRedoStage, useRunProject, useSkipStage } from "../../api/projects";
import { can, DENIED_REASONS } from "../../lib/roles";
import type { StageName } from "../../types/channel";
import type { Project } from "../../types/project";
import { Gated } from "../auth/Gated";
import { Button } from "../ui/Button";
import { useToast } from "../ui/Toast";
import { NotesDialog } from "./NotesDialog";
import { STAGE_LABELS } from "./stageMeta";

export interface StageActionsProps {
  project: Project;
  stage: StageName;
  /** hide Approve when the stage panel has its own approve button with edits */
  showApprove?: boolean;
  approveLabel?: string;
  /** hide Redo when the stage panel offers its own regenerate button */
  showRedo?: boolean;
  className?: string;
}

type Pending = "approve" | "redo" | "skip" | null;

/**
 * The generic stage buttons: approve (continue), redo with notes, skip, and resume. Stage
 * panels with their own edits (research pick, title choice, script, storyboard) hide the
 * ones they replace. Viewers see the buttons disabled, with the reason on hover.
 */
export function StageActions({ project, stage, showApprove = true, approveLabel, showRedo = true, className }: StageActionsProps) {
  const { toast } = useToast();
  const user = useCurrentUser();
  const mayApprove = can(user, "approve");
  const mayRun = can(user, "run");
  const approve = useApproveStage();
  const redo = useRedoStage();
  const skip = useSkipStage();
  const run = useRunProject();
  const [dialog, setDialog] = useState<Pending>(null);

  const state = project.stages[stage];
  const status = state?.status ?? "pending";
  const label = STAGE_LABELS[stage];

  const canApprove = status === "awaiting_review" || status === "awaiting_manual" || status === "failed";
  // Mirrors the engine: an approved stage is about to run on and a manual stage has nothing the AI could redo.
  const canRedo =
    (status === "awaiting_review" || status === "failed" || status === "done" || status === "awaiting_manual") &&
    project.stage_modes[stage] !== "manual";
  const canSkip = status !== "done" && status !== "approved" && status !== "skipped" && status !== "running";
  const canResume = (status === "awaiting_manual" || status === "failed" || status === "pending") && stage === project.current_stage;

  const busy = approve.isPending || redo.isPending || skip.isPending || run.isPending;

  const fail = (title: string, error: unknown) => toast({ tone: "error", title, description: errorMessage(error) });

  const onApprove = (notes: string) => {
    approve.mutate(
      { id: project.id, stage, body: { notes: notes || undefined } },
      {
        onSuccess: () => {
          setDialog(null);
          toast({ tone: "success", title: `${label} approved`, description: "The pipeline continues with the next stage." });
        },
        onError: (error) => fail(`Could not approve ${label.toLowerCase()}`, error),
      },
    );
  };

  const onRedo = (notes: string) => {
    redo.mutate(
      { id: project.id, stage, body: { notes } },
      {
        onSuccess: () => {
          setDialog(null);
          toast({ tone: "success", title: `${label} runs again`, description: "Your notes were added to the prompt." });
        },
        onError: (error) => fail(`Could not redo ${label.toLowerCase()}`, error),
      },
    );
  };

  const onSkip = (notes: string) => {
    skip.mutate(
      { id: project.id, stage, body: { notes } },
      {
        onSuccess: () => {
          setDialog(null);
          toast({ tone: "info", title: `${label} skipped` });
        },
        onError: (error) => fail(`Could not skip ${label.toLowerCase()}`, error),
      },
    );
  };

  const onResume = () => {
    run.mutate(project.id, {
      onSuccess: (response) =>
        toast({
          tone: "info",
          title: response.started ? "Pipeline resumed" : "Nothing to resume yet",
          description: response.message || (response.started ? `Picking up at ${label.toLowerCase()}.` : undefined),
        }),
      onError: (error) => fail("Could not resume", error),
    });
  };

  if (!canApprove && !canRedo && !canSkip && !canResume) return null;

  return (
    <div className={className}>
      <div className="flex flex-wrap items-center gap-2">
        {showApprove && canApprove ? (
          <Gated allowed={mayApprove} reason={DENIED_REASONS.approve}>
            <Button variant="primary" size="sm" icon={<Check />} onClick={() => setDialog("approve")} disabled={busy || !mayApprove}>
              {approveLabel ?? (status === "awaiting_manual" ? "My files are in place, continue" : "Approve and continue")}
            </Button>
          </Gated>
        ) : null}
        {canResume ? (
          <Gated allowed={mayRun} reason={DENIED_REASONS.run}>
            <Button
              variant="secondary"
              size="sm"
              icon={<Play />}
              onClick={onResume}
              loading={run.isPending}
              disabled={(busy && !run.isPending) || !mayRun}
            >
              Resume pipeline
            </Button>
          </Gated>
        ) : null}
        {showRedo && canRedo ? (
          <Gated allowed={mayApprove} reason={DENIED_REASONS.approve}>
            <Button variant="secondary" size="sm" icon={<RotateCcw />} onClick={() => setDialog("redo")} disabled={busy || !mayApprove}>
              Redo with notes
            </Button>
          </Gated>
        ) : null}
        {canSkip ? (
          <Gated allowed={mayApprove} reason={DENIED_REASONS.approve}>
            <Button variant="ghost" size="sm" icon={<SkipForward />} onClick={() => setDialog("skip")} disabled={busy || !mayApprove}>
              Skip this stage
            </Button>
          </Gated>
        ) : null}
      </div>

      <NotesDialog
        open={dialog === "approve"}
        title={`Approve ${label.toLowerCase()}?`}
        description="The project moves on to the next stage, and the approval is recorded under your name. Add a note if you want it kept with the project."
        confirmLabel="Approve"
        loading={approve.isPending}
        onConfirm={onApprove}
        onCancel={() => setDialog(null)}
      />
      <NotesDialog
        open={dialog === "redo"}
        title={`Redo ${label.toLowerCase()}`}
        description="The stage runs again and your notes are added to the prompt. Say what should be different."
        confirmLabel="Run again"
        requireNotes
        loading={redo.isPending}
        onConfirm={onRedo}
        onCancel={() => setDialog(null)}
      />
      <NotesDialog
        open={dialog === "skip"}
        title={`Skip ${label.toLowerCase()}?`}
        description="The pipeline continues without this stage's output. Later stages may need you to supply the files by hand."
        confirmLabel="Skip stage"
        requireNotes
        tone="danger"
        placeholder="Why this stage is skipped."
        loading={skip.isPending}
        onConfirm={onSkip}
        onCancel={() => setDialog(null)}
      />
    </div>
  );
}
