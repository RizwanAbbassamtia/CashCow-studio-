import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, type ButtonProps } from "../ui/Button";
import { useToast } from "../ui/Toast";

/**
 * Puts text on the clipboard. Uses the clipboard API when the page may, else the older copy
 * command on a hidden text box (some embedded browsers refuse the API but allow that).
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* refused: try the copy command below */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.setAttribute("aria-hidden", "true");
  Object.assign(area.style, { position: "fixed", top: "0", left: "-9999px", opacity: "0", pointerEvents: "none" });
  const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  document.body.appendChild(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  area.remove();
  active?.focus();
  return copied;
}

export interface CopyButtonProps extends Omit<ButtonProps, "onClick" | "children"> {
  text: string;
  /** what was copied, for the toast: "Folder path", "Password" */
  what: string;
  label?: string;
  /** show a toast after copying (the button also turns into a tick for a moment) */
  announce?: boolean;
}

/** Copies a piece of text to the clipboard; falls back to asking the person to copy by hand. */
export function CopyButton({ text, what, label = "Copy", announce = true, variant = "secondary", size = "sm", ...props }: CopyButtonProps) {
  const { toast } = useToast();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1_500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    if (await copyText(text)) {
      setCopied(true);
      if (announce) toast({ tone: "success", title: `${what} copied` });
    } else {
      toast({ tone: "error", title: "Could not copy", description: "Select the text and copy it by hand (Ctrl+C)." });
    }
  };

  return (
    <Button variant={variant} size={size} icon={copied ? <Check /> : <Copy />} onClick={() => void copy()} {...props}>
      {copied ? "Copied" : label}
    </Button>
  );
}
