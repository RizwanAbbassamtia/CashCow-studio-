import { useEffect } from "react";
import { useLocation } from "react-router";

import { useCan } from "../api/auth";
import { TeamLinksCard } from "../components/master/team/TeamLinksCard";
import { ApiKeysCard } from "../components/settings/ApiKeysCard";
import { DoctorPanel } from "../components/settings/DoctorPanel";
import { ModelsCard } from "../components/settings/ModelsCard";
import { PathsCard } from "../components/settings/PathsCard";
import { RenderCard } from "../components/settings/RenderCard";
import { Notice } from "../components/ui/States";
import { DENIED_REASONS } from "../lib/roles";

export function SettingsPage() {
  const location = useLocation();
  const mayChange = useCan("settings");

  // /settings#doctor (from the health pill) scrolls to that panel once it is on screen.
  useEffect(() => {
    if (!location.hash) return;
    const id = location.hash.slice(1);
    const timer = window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
    return () => window.clearTimeout(timer);
  }, [location.hash]);

  return (
    <div className="flex flex-col gap-6">
      <TeamLinksCard />
      {mayChange ? null : (
        <Notice tone="info" title="Read only">
          {DENIED_REASONS.settings} You can still read them here and run the health checks.
        </Notice>
      )}
      {/* A disabled fieldset switches off every field and button inside it in one place. */}
      <fieldset disabled={!mayChange} className="m-0 flex min-w-0 flex-col gap-6 border-0 p-0">
        <PathsCard />
        <ApiKeysCard />
        <ModelsCard />
        <RenderCard />
      </fieldset>
      <DoctorPanel />
    </div>
  );
}
