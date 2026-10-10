import { KeyRound } from "lucide-react";
import { useEffect } from "react";

import { errorMessage } from "../../../api/client";
import { useResetUserPassword } from "../../../api/users";
import type { TeamUser } from "../../../types/users";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { Notice } from "../../ui/States";
import { OneTimePassword } from "./OneTimePassword";

/**
 * Asks first, then makes a new password for the person and shows it once. The old password
 * stops working at once; the person picks their own password at the next sign-in.
 */
export function ResetPasswordDialog({ user, isSelf, onClose }: { user: TeamUser | null; isSelf: boolean; onClose: () => void }) {
  const reset = useResetUserPassword();
  const { reset: forget } = reset;

  // Opening the dialog for another person starts from the question again.
  useEffect(() => {
    forget();
  }, [user?.username, forget]);

  if (!user) return null;

  const close = () => {
    reset.reset(); // forgets the one-time password
    onClose();
  };

  if (reset.data) {
    return (
      <Dialog
        open
        title={`New password for ${user.display_name}`}
        description="The old password no longer works."
        // The password is gone once this closes, so only the button closes it.
        onClose={() => undefined}
        className="max-w-lg!"
        footer={
          <Button variant="primary" onClick={close}>
            Done, I have copied it
          </Button>
        }
      >
        <OneTimePassword password={reset.data.password} name={user.display_name} />
      </Dialog>
    );
  }

  return (
    <Dialog
      open
      title={`Reset the password of ${user.display_name}?`}
      description={
        isSelf
          ? "This makes a new password for your own account. Your current password stops working and you will be asked to choose a new one."
          : "The app makes a new password and shows it to you once. Their current password stops working at once, and they choose their own password at the next sign-in."
      }
      onClose={close}
      footer={
        <>
          <Button variant="secondary" onClick={close} disabled={reset.isPending}>
            Cancel
          </Button>
          <Button variant="primary" icon={<KeyRound />} loading={reset.isPending} onClick={() => reset.mutate(user.username)}>
            Make a new password
          </Button>
        </>
      }
    >
      {reset.isError ? <Notice tone="fail">{errorMessage(reset.error)}</Notice> : null}
    </Dialog>
  );
}
