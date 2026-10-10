import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { authKeys } from "../../api/auth";
import { onUnauthorized } from "../../api/client";
import { projectEvents } from "../../api/ws";

/**
 * While the app is open, ask /api/auth/me again whenever any call answers 401 or the live
 * connection (WebSocket /api/ws, which needs a session too) drops or fails to reconnect.
 * When the answer is "nobody", the app shell returns to the sign-in page with a notice.
 * Asking instead of trusting the 401 directly means a late answer from before a fresh
 * sign-in can never throw the new session out.
 */
export function useSessionWatch(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const recheck = () => {
      void queryClient.invalidateQueries({ queryKey: authKeys.me, exact: true }, { cancelRefetch: false });
    };

    const stopUnauthorized = onUnauthorized(recheck);

    let previous = projectEvents.getSnapshot();
    const stopEvents = projectEvents.subscribeState(() => {
      const next = projectEvents.getSnapshot();
      const dropped = previous.state === "open" && next.state !== "open";
      const failedAgain = next.attempts > previous.attempts;
      previous = next;
      if (dropped || failedAgain) recheck();
    });

    return () => {
      stopUnauthorized();
      stopEvents();
    };
  }, [queryClient]);
}
