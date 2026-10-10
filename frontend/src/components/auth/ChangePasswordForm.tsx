import { zodResolver } from "@hookform/resolvers/zod";
import { Check, Circle } from "lucide-react";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { useChangePassword } from "../../api/auth";
import { ApiError, errorMessage, isUnauthorized } from "../../api/client";
import { cn } from "../../lib/cn";
import { PASSWORD_MIN_LENGTH } from "../../types/auth";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { PasswordInput } from "./PasswordInput";

const schema = z
  .object({
    current_password: z.string().min(1, "Type the password you use now."),
    new_password: z.string().min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters.`),
    confirm: z.string().min(1, "Type the new password again."),
  })
  .refine((values) => values.new_password === values.confirm, {
    path: ["confirm"],
    message: "The two new passwords are not the same.",
  })
  .refine((values) => values.new_password !== values.current_password, {
    path: ["new_password"],
    message: "Choose a password that is different from the one you use now.",
  });

type Values = z.infer<typeof schema>;

export interface ChangePasswordFormProps {
  onDone: () => void;
  /** omitted when the change is required (no way around it except signing out) */
  onCancel?: () => void;
  submitLabel?: string;
  /** label of the first box, e.g. "One-time password from your admin" */
  currentLabel?: string;
}

/** Current password, new password (with the rule), confirm. Shared by the dialog and the forced screen. */
export function ChangePasswordForm({ onDone, onCancel, submitLabel = "Change password", currentLabel = "Current password" }: ChangePasswordFormProps) {
  const change = useChangePassword();
  const [serverError, setServerError] = useState<string | null>(null);
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: "", new_password: "", confirm: "" },
    mode: "onTouched",
  });
  const { register, handleSubmit, formState, setError, control } = form;
  const newPassword = useWatch({ control, name: "new_password" }) ?? "";
  const longEnough = newPassword.length >= PASSWORD_MIN_LENGTH;

  const onValid = (values: Values) => {
    setServerError(null);
    change.mutate(
      { current_password: values.current_password, new_password: values.new_password },
      {
        onSuccess: () => onDone(),
        onError: (error) => {
          if (error instanceof ApiError && error.fieldErrors.length > 0) {
            for (const field of error.fieldErrors) {
              if (field.path === "current_password" || field.path === "new_password") {
                setError(field.path, { message: field.message });
              }
            }
          }
          if (isUnauthorized(error) || (error instanceof ApiError && (error.status === 400 || error.status === 403))) {
            setError("current_password", { message: errorMessage(error) || "That is not your current password." });
            return;
          }
          setServerError(errorMessage(error));
        },
      },
    );
  };

  return (
    <form noValidate onSubmit={handleSubmit(onValid)} className="flex flex-col gap-4">
      <Field label={currentLabel} htmlFor="cp-current" error={formState.errors.current_password?.message}>
        <PasswordInput
          id="cp-current"
          autoComplete="current-password"
          autoFocus
          invalid={Boolean(formState.errors.current_password)}
          {...register("current_password")}
        />
      </Field>
      <Field label="New password" htmlFor="cp-new" error={formState.errors.new_password?.message}>
        <PasswordInput id="cp-new" autoComplete="new-password" invalid={Boolean(formState.errors.new_password)} {...register("new_password")} />
      </Field>
      <ul className="-mt-1 flex flex-col gap-1 text-xs" aria-live="polite">
        <li className={cn("flex items-center gap-1.5", longEnough ? "text-ok" : "text-ink-faint")}>
          {longEnough ? <Check className="size-3.5" aria-hidden /> : <Circle className="size-3" aria-hidden />}
          At least {PASSWORD_MIN_LENGTH} characters
        </li>
        <li className="text-ink-faint">Tip: three or four unrelated words are easy to type and hard to guess.</li>
      </ul>
      <Field label="New password again" htmlFor="cp-confirm" error={formState.errors.confirm?.message}>
        <PasswordInput id="cp-confirm" autoComplete="new-password" invalid={Boolean(formState.errors.confirm)} {...register("confirm")} />
      </Field>

      {serverError ? (
        <p role="alert" className="rounded-md border border-fail/30 bg-fail/10 px-3 py-2 text-[13px] text-ink">
          {serverError}
        </p>
      ) : null}

      <div className="mt-1 flex justify-end gap-2">
        {onCancel ? (
          <Button variant="secondary" onClick={onCancel} disabled={change.isPending}>
            Cancel
          </Button>
        ) : null}
        <Button type="submit" variant="primary" loading={change.isPending} className={onCancel ? undefined : "w-full"}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
