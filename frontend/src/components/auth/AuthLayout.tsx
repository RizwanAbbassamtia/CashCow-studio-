import { useEffect, type ReactNode } from "react";

import { useSystemInfo } from "../../api/system";
import { ProductMark } from "./ProductMark";

export interface AuthLayoutProps {
  children: ReactNode;
  /** small print under the card, e.g. this laptop's name */
  below?: ReactNode;
  /** the window title, e.g. "Sign in" */
  pageTitle?: string;
}

/**
 * Full-screen frame for the screens before the app opens: sign in, create the first admin,
 * choose a new password. Always dark (the product's own colours), whatever theme the app uses.
 */
export function AuthLayout({ children, below, pageTitle }: AuthLayoutProps) {
  const system = useSystemInfo();

  useEffect(() => {
    document.title = pageTitle ? `${pageTitle} - CashCow Studio` : "CashCow Studio";
  }, [pageTitle]);

  return (
    <div className="dark min-h-screen bg-canvas text-ink">
      <div className="relative flex min-h-screen flex-col overflow-hidden">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(56rem_28rem_at_50%_-8%,rgb(255_192_0/0.11),transparent_70%)]"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-40 [background-image:linear-gradient(to_right,var(--cfs-line)_1px,transparent_1px),linear-gradient(to_bottom,var(--cfs-line)_1px,transparent_1px)] [background-size:48px_48px] [mask-image:radial-gradient(ellipse_at_50%_40%,black_20%,transparent_70%)]"
        />

        <main className="relative z-10 flex flex-1 items-center justify-center px-6 py-10">
          <div className="w-full max-w-[420px]">
            <div className="mb-7 flex flex-col items-center text-center">
              <ProductMark size={60} className="drop-shadow-[0_10px_28px_rgb(255_192_0/0.22)]" />
              <div className="mt-4 text-[22px] font-semibold tracking-tight text-ink">CashCow Studio</div>
              <div className="mt-1 text-sm text-ink-muted">Faceless videos, made by the team</div>
            </div>

            <div className="rounded-xl border border-line-strong/70 bg-surface/95 p-7 shadow-[0_24px_64px_-24px_rgb(0_0_0/0.75)] backdrop-blur">
              {children}
            </div>

            {below ? <div className="mt-5 text-center text-xs text-ink-faint">{below}</div> : null}
          </div>
        </main>

        <footer className="relative z-10 flex items-center justify-between gap-4 px-6 py-4 text-[11px] text-ink-faint">
          <span>Version {system.data?.version ?? "..."}</span>
          <span>Runs on this computer only (127.0.0.1)</span>
        </footer>
      </div>
    </div>
  );
}
