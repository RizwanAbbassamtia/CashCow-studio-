import { Suspense, useEffect, useRef } from "react";
import { Navigate, Outlet, useLocation } from "react-router";

import { useMe, useSignOutReason } from "../api/auth";
import { useProjectEvents } from "../api/ws";
import type { LoginLocationState } from "../components/auth/loginRedirect";
import { ForcedPasswordChange, SessionSplash, SessionUnavailable } from "../components/auth/SessionScreens";
import { useSessionWatch } from "../components/auth/useSessionWatch";
import { LoadingBlock } from "../components/ui/Spinner";
import type { PublicUser } from "../types/auth";
import { Sidebar } from "./Sidebar";
import { TopBar, usePageTitle } from "./TopBar";

/** Sidebar + top bar frame around every page. Designed for 1280px and wider. */
function Frame({ user }: { user: PublicUser }) {
  const location = useLocation();
  const main = useRef<HTMLElement>(null);
  const title = usePageTitle();
  // One live connection for as long as someone is signed in: the sidebar's review count stays
  // current on every page, and a refused reconnect (session ended) is noticed anywhere.
  useProjectEvents();

  // The page body scrolls inside <main>, so start each new page at the top.
  useEffect(() => {
    if (!location.hash) main.current?.scrollTo({ top: 0 });
  }, [location.pathname, location.hash]);

  useEffect(() => {
    document.title = title === "CashCow Studio" ? title : `${title} - CashCow Studio`;
  }, [title]);

  return (
    <div className="flex h-screen min-w-[1024px] overflow-hidden bg-canvas text-ink">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar user={user} />
        <main ref={main} className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1440px] px-8 py-6">
            <Suspense fallback={<LoadingBlock label="Opening the page..." />}>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>
    </div>
  );
}

/**
 * The route guard and frame of every page except /login and /first-run.
 *
 * `/api/auth/me` decides: a person -> the app (or the forced password change first); nobody
 * -> the sign-in page, remembering the page that was asked for and, when a session ended
 * while the app was open, a "signed out" notice. Any 401 elsewhere, and a dropped live
 * connection, make it ask again (useSessionWatch).
 */
export function AppShell() {
  const location = useLocation();
  const me = useMe({ poll: true });
  const reason = useSignOutReason();
  const hadUser = useRef(false);
  useSessionWatch();

  const user = me.data ?? null;
  useEffect(() => {
    if (user) hadUser.current = true;
  }, [user]);

  if (user) {
    if (user.must_change_password) return <ForcedPasswordChange user={user} />;
    return <Frame user={user} />;
  }

  if (me.data === null) {
    const notice = reason ?? (hadUser.current ? "expired" : null);
    const state: LoginLocationState = {
      // After Sign out the next person starts on the dashboard, not on the last person's page.
      from: notice === "signed_out" ? undefined : `${location.pathname}${location.search}${location.hash}`,
      notice,
    };
    return <Navigate to="/login" replace state={state} />;
  }

  if (me.isError) return <SessionUnavailable error={me.error} onRetry={() => void me.refetch()} retrying={me.isFetching} />;
  return <SessionSplash />;
}
