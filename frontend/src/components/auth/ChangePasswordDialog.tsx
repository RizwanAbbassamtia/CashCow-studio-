import { Dialog } from "../ui/Dialog";
import { useToast } from "../ui/Toast";
import { ChangePasswordForm } from "./ChangePasswordForm";

/** "Change password" from the account menu in the top bar. */
export function ChangePasswordDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { toast } = useToast();
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Change your password"
      description="Choose a password only you know. You will use it the next time you sign in, on any laptop."
    >
      {/* Mounted only while open, so the boxes are empty every time the dialog opens. */}
      {open ? (
        <ChangePasswordForm
          onCancel={onClose}
          onDone={() => {
            onClose();
            toast({ tone: "success", title: "Password changed", description: "Use the new password the next time you sign in." });
          }}
        />
      ) : null}
    </Dialog>
  );
}
