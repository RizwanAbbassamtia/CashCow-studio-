import { lazy } from "react";
import {
  createBrowserRouter,
  isRouteErrorResponse,
  Link,
  Navigate,
  RouterProvider,
  useLocation,
  useParams,
  useRouteError,
  useSearchParams,
  type RouteObject,
} from "react-router";

import { RequireRole } from "./components/auth/Gated";
import { AppShell } from "./layout/AppShell";
import type { RouteHandle } from "./layout/nav";
import { FirstRunPage } from "./pages/auth/FirstRunPage";
import { LoginPage } from "./pages/auth/LoginPage";
import { BatchPage } from "./pages/automation/BatchPage";
import { ChannelSetupPage } from "./pages/ChannelSetupPage";
import { ChannelsPage } from "./pages/ChannelsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ResearchPage } from "./pages/ResearchPage";
import { ReviewQueuePage } from "./pages/ReviewQueuePage";
import { SettingsPage } from "./pages/SettingsPage";

// Pages filled by the Editing / Master Sheet / Settings work, loaded when first opened.
const EditRoomPage = lazy(() => import("./pages/editing/EditRoomPage").then((m) => ({ default: m.EditRoomPage })));
const RendersPage = lazy(() => import("./pages/editing/RendersPage").then((m) => ({ default: m.RendersPage })));
const FrameworksLibraryPage = lazy(() =>
  import("./pages/master/FrameworksLibraryPage").then((m) => ({ default: m.FrameworksLibraryPage })),
);
const VoicesPage = lazy(() => import("./pages/master/VoicesPage").then((m) => ({ default: m.VoicesPage })));
const MusicLibraryPage = lazy(() => import("./pages/master/MusicLibraryPage").then((m) => ({ default: m.MusicLibraryPage })));
const ProductionLogPage = lazy(() => import("./pages/master/ProductionLogPage").then((m) => ({ default: m.ProductionLogPage })));
const UsersPage = lazy(() => import("./pages/settings/UsersPage").then((m) => ({ default: m.UsersPage })));
const ActivityPage = lazy(() => import("./pages/settings/ActivityPage").then((m) => ({ default: m.ActivityPage })));

/** Shown when a page throws while rendering, so the app never goes blank. */
function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : "Something went wrong on this page.";
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas p-8 text-ink">
      <div className="max-w-md rounded-card border border-line bg-surface p-8 text-center shadow-card">
        <h1 className="text-lg font-semibold">This page hit a problem</h1>
        <p className="mt-2 text-sm text-ink-muted">{message}</p>
        <Link
          to="/automation"
          className="mt-6 inline-flex h-9 items-center rounded-md bg-accent px-4 text-sm font-semibold text-accent-ink hover:bg-accent-hover"
        >
          Back to the Dashboard
        </Link>
      </div>
    </div>
  );
}

/**
 * An address from before the four areas (M0-M4) that still works: sends it to its new home,
 * keeping the ids in it, the ?query and the #anchor.
 */
function RedirectTo({ to }: { to: string }) {
  const params = useParams();
  const location = useLocation();
  const pathname = to.replace(/:([A-Za-z]+)/g, (_match, name: string) => encodeURIComponent(params[name] ?? ""));
  return <Navigate to={{ pathname, search: location.search, hash: location.hash }} replace />;
}

/** Projects, or only the projects at one stage with `?stage=storyboard` (the old Storyboard page). */
function ProjectsRoute() {
  const [params] = useSearchParams();
  return <ProjectsPage stageFilter={params.get("stage") ?? undefined} />;
}

function handle(area: string, title: string): RouteHandle {
  return { area, title };
}

const automation: RouteObject = {
  path: "automation",
  handle: { area: "Automation" } satisfies RouteHandle,
  children: [
    { index: true, element: <DashboardPage />, handle: handle("Automation", "Dashboard") },
    { path: "research", element: <ResearchPage />, handle: handle("Automation", "New video") },
    { path: "research/:slug", element: <ResearchPage />, handle: handle("Automation", "New video") },
    { path: "projects", element: <ProjectsRoute />, handle: handle("Automation", "Projects") },
    { path: "projects/:id", element: <ProjectPage />, handle: handle("Automation", "Project") },
    { path: "review", element: <ReviewQueuePage />, handle: handle("Automation", "Review queue") },
    { path: "batch", element: <BatchPage />, handle: handle("Automation", "Batch") },
  ],
};

const editing: RouteObject = {
  path: "editing",
  handle: { area: "Editing" } satisfies RouteHandle,
  children: [
    { index: true, element: <EditRoomPage />, handle: handle("Editing", "Edit room") },
    { path: "renders", element: <RendersPage />, handle: handle("Editing", "Renders") },
    { path: ":projectId", element: <EditRoomPage />, handle: handle("Editing", "Edit room") },
  ],
};

const master: RouteObject = {
  path: "master",
  handle: { area: "Master Sheet" } satisfies RouteHandle,
  children: [
    { index: true, element: <Navigate to="/master/channels" replace /> },
    { path: "channels", element: <ChannelsPage />, handle: handle("Master Sheet", "Channels") },
    { path: "channels/new", element: <ChannelSetupPage />, handle: handle("Master Sheet", "New channel") },
    { path: "channels/:slug", element: <ChannelSetupPage />, handle: handle("Master Sheet", "Channel setup") },
    { path: "frameworks", element: <FrameworksLibraryPage />, handle: handle("Master Sheet", "Frameworks") },
    { path: "voices", element: <VoicesPage />, handle: handle("Master Sheet", "Voices") },
    { path: "music", element: <MusicLibraryPage />, handle: handle("Master Sheet", "Music") },
    { path: "production-log", element: <ProductionLogPage />, handle: handle("Master Sheet", "Production log") },
  ],
};

const settings: RouteObject = {
  path: "settings",
  handle: { area: "Settings" } satisfies RouteHandle,
  children: [
    { index: true, element: <SettingsPage />, handle: handle("Settings", "General") },
    {
      path: "users",
      element: (
        <RequireRole action="manage_users">
          <UsersPage />
        </RequireRole>
      ),
      handle: handle("Settings", "Users & roles"),
    },
    { path: "activity", element: <ActivityPage />, handle: handle("Settings", "Activity") },
  ],
};

/** M0-M4 addresses, so bookmarks and links in older screens keep working. */
const oldAddresses: RouteObject[] = [
  { path: "channels", element: <RedirectTo to="/master/channels" /> },
  { path: "channels/new", element: <RedirectTo to="/master/channels/new" /> },
  { path: "channels/:slug", element: <RedirectTo to="/master/channels/:slug" /> },
  { path: "research", element: <RedirectTo to="/automation/research" /> },
  { path: "research/:slug", element: <RedirectTo to="/automation/research/:slug" /> },
  { path: "projects", element: <RedirectTo to="/automation/projects" /> },
  { path: "projects/:id", element: <RedirectTo to="/automation/projects/:id" /> },
  { path: "review", element: <RedirectTo to="/automation/review" /> },
  { path: "storyboard", element: <Navigate to={{ pathname: "/automation/projects", search: "?stage=storyboard" }} replace /> },
  { path: "editor", element: <RedirectTo to="/editing" /> },
];

const router = createBrowserRouter([
  // Before sign-in: the only pages reachable without a session.
  { path: "/login", element: <LoginPage />, errorElement: <RouteError /> },
  { path: "/first-run", element: <FirstRunPage />, errorElement: <RouteError /> },
  {
    // Everything else needs a session; AppShell is the guard.
    path: "/",
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to="/automation" replace /> },
      automation,
      editing,
      master,
      settings,
      ...oldAddresses,
      { path: "*", element: <NotFoundPage />, handle: { title: "Page not found" } satisfies RouteHandle },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}
