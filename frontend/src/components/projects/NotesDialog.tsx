import { useEffect, useState, type ReactNode } from "react";

import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Field } from "../ui/Field";
import { Textarea } from "../ui/Input";

export interface NotesDialogProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  confirmLabel: string;
  /** redo and skip need a reason; approve does not */
  requireNotes?: boolean;
  placeholder?: string;
  tone?: "default" | "danger";
  loading?: boolean;
  onConfirm: (notes: string) => void;
  onCancel: () => void;
}

/** Asks for the reviewer's notes before approving, redoing or skipping a stage. */
export function NotesDialog({
  open,
  title,
  description,
  confirmLabel,
  requireNotes = false,
  placeholder = "What should change, in plain words.",
  tone = "default",
  loading = false,
  onConfirm,
  onCancel,
}: NotesDialogProps) {
  const [notes, setNotes] = useState("");

  useEffect(() => {
    if (open) setNotes("");
  }, [open]);

  const trimmed = notes.trim();
  const canConfirm = !requireNotes || trimmed.length > 0;

  return (
    <Dialog
      open={open}
      title={title}
      description={description}
      onClose={onCancel}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={loading}>
            Cancel
          </Button>
          <Button variant={tone === "danger" ? "danger" : "primary"} onClick={() => onConfirm(trimmed)} loading={loading} disabled={!canConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <Field label={requireNotes ? "Notes" : "Notes (optional)"} htmlFor="stage-notes" required={requireNotes} hint={requireNotes ? "These notes go into the next attempt." : "Kept with the project for later."}>
        <Textarea
          id="stage-notes"
          rows={4}
          autoFocus
          placeholder={placeholder}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          onKeyDown={(event) => {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && canConfirm && !loading) onConfirm(trimmed);
          }}
        />
      </Field>
    </Dialog>
  );
}
