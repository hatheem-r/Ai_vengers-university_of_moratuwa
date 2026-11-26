import React from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  ClipboardCheck,
  Command,
  Factory,
  GitCompare,
  ListTree,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  PlayCircle,
  Sun,
} from "lucide-react";
import { useMockDetector, useRunsList } from "@/api/hooks";
import { useAppStore } from "@/state/appStore";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";
import { CommandPalette } from "./CommandPalette";

export function AppShell({ children }: { children: React.ReactNode }) {
  const {
    activeRunId,
    setActiveRunId,
    theme,
    toggleTheme,
    sidebarCollapsed,
    setSidebarCollapsed,
    setCommandOpen,
  } = useAppStore();
  const { isMock, backendOnline } = useMockDetector();
  const runsQuery = useRunsList(undefined, 15);
  const location = useLocation();
  const navigate = useNavigate();

  const navGroups = [
    {
      heading: "Planning Cycle",
      items: [
        { to: "/", label: "Start", icon: PlayCircle, matchPrefix: null },
        { to: `/run/${activeRunId}`, label: "Live Run", icon: Activity, matchPrefix: "live" },
        {
          to: `/run/${activeRunId}/review`,
          label: "Plan Review",
          icon: ClipboardCheck,
          matchPrefix: "review",
        },
        { to: `/run/${activeRunId}/trace`, label: "Trace", icon: ListTree, matchPrefix: "trace" },
      ],
    },
    {
      heading: "Benchmark & Proof",
      items: [
        { to: "/compare", label: "Compare", icon: GitCompare, matchPrefix: "/compare" },
        { to: "/evaluation", label: "Evaluation", icon: BarChart3, matchPrefix: "/evaluation" },
      ],
    },
  ];

  const isNavItemActive = (to: string, matchPrefix: string | null) => {
    const p = location.pathname;
    if (to === "/") return p === "/";
    if (matchPrefix === "live") return p.startsWith("/run/") && !p.endsWith("/review") && !p.endsWith("/trace");
    if (matchPrefix === "review") return p.endsWith("/review");
    if (matchPrefix === "trace") return p.endsWith("/trace");
    return matchPrefix ? p.startsWith(matchPrefix) : p === to;
  };

  return (
    <div className="min-h-screen bg-canvas text-ink flex">
      <CommandPalette />

      {/* Collapsible Industrial Sidebar */}
      <aside
        className={cn(
          "shrink-0 border-r border-hairline bg-surface flex flex-col justify-between transition-[width] duration-200",
          sidebarCollapsed ? "w-14" : "w-56"
        )}
      >
        <div>
          <div className="h-12 px-3.5 border-b border-hairline flex items-center justify-between gap-2">
            <button
              onClick={() => navigate("/")}
              className="flex items-center gap-2 text-left min-w-0"
            >
              <div className="h-7 w-7 rounded bg-status-focus text-white flex items-center justify-center shrink-0">
                <Factory className="h-4 w-4" strokeWidth={1.75} />
              </div>
              {!sidebarCollapsed && (
                <div className="truncate">
                  <div className="text-xs font-semibold tracking-tight leading-none">
                    Work Order Planner
                  </div>
                  <div className="text-[10px] text-muted font-mono mt-0.5">Ai vengers · MES</div>
                </div>
              )}
            </button>
          </div>

          <nav className="p-2 space-y-4 mt-1">
            {navGroups.map((group) => (
              <div key={group.heading} className="space-y-1">
                {!sidebarCollapsed && (
                  <div className="px-2.5 text-[10px] font-semibold uppercase tracking-wider text-faint">
                    {group.heading}
                  </div>
                )}
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const active = isNavItemActive(item.to, item.matchPrefix);
                  return (
                    <NavLink
                      key={item.label}
                      to={item.to}
                      title={sidebarCollapsed ? item.label : undefined}
                      className={cn(
                        "flex items-center gap-2.5 rounded px-2.5 py-1.5 text-xs font-medium transition-colors",
                        active
                          ? "bg-status-focus/10 text-status-focus border border-status-focus/25"
                          : "text-muted hover:text-ink hover:bg-subtle border border-transparent"
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" strokeWidth={1.5} />
                      {!sidebarCollapsed && <span className="truncate">{item.label}</span>}
                    </NavLink>
                  );
                })}
              </div>
            ))}
          </nav>
        </div>

        <div className="p-2 border-t border-hairline flex items-center justify-between">
          {!sidebarCollapsed && (
            <span className="px-2 text-[10px] font-mono text-faint">UoM · Catalyst 2026</span>
          )}
          <button
            onClick={() => setSidebarCollapsed((c) => !c)}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="p-1.5 rounded text-muted hover:text-ink hover:bg-subtle transition-colors"
          >
            {sidebarCollapsed ? (
              <PanelLeftOpen className="h-4 w-4" strokeWidth={1.5} />
            ) : (
              <PanelLeftClose className="h-4 w-4" strokeWidth={1.5} />
            )}
          </button>
        </div>
      </aside>

      {/* Main Column */}
      <div className="flex-1 min-w-0 flex flex-col">
        {/* Persistent Top Control Header */}
        <header className="h-12 border-b border-hairline bg-surface px-5 flex items-center justify-between gap-4 sticky top-0 z-30">
          <div className="flex items-center gap-3 min-w-0">
            {/* Persistent High-Visibility MOCK DATA Pill Badge (Competition Rule 05) */}
            {isMock ? (
              <Badge tone="mock" className="px-2.5 py-1 text-xs tracking-wide uppercase">
                <AlertTriangle className="h-3.5 w-3.5" strokeWidth={2} />
                MOCK DATA
              </Badge>
            ) : backendOnline ? (
              <Badge tone="nominal" mono>
                <span className="h-1.5 w-1.5 rounded-full bg-status-nominal" />
                LIVE API
              </Badge>
            ) : (
              <Badge tone="critical" mono>
                API OFFLINE
              </Badge>
            )}

            {/* Active Run Context Switcher */}
            {runsQuery.data?.runs && runsQuery.data.runs.length > 0 && (
              <div className="hidden sm:flex items-center gap-1.5 text-xs">
                <span className="text-muted">Active Run:</span>
                <select
                  aria-label="Select active run"
                  value={activeRunId}
                  onChange={(e) => {
                    const nextId = e.target.value;
                    setActiveRunId(nextId);
                    if (location.pathname.endsWith("/review")) {
                      navigate(`/run/${nextId}/review`);
                    } else if (location.pathname.endsWith("/trace")) {
                      navigate(`/run/${nextId}/trace`);
                    } else if (location.pathname.startsWith("/run/")) {
                      navigate(`/run/${nextId}`);
                    }
                  }}
                  className="h-7 rounded border border-hairline bg-subtle px-2 font-mono text-[11px] text-ink focus:outline-none focus:border-status-focus"
                >
                  {runsQuery.data.runs.map((r) => (
                    <option key={r.run_id} value={r.run_id}>
                      {r.bundle_id} · {r.system_id} · {r.status} ({r.run_id.slice(-4)})
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setCommandOpen(true)}
              className="hidden md:inline-flex items-center gap-2 h-7 px-2.5 rounded border border-hairline bg-subtle text-xs text-muted hover:text-ink transition-colors"
            >
              <Command className="h-3 w-3" strokeWidth={1.5} />
              <span>Quick Jump</span>
              <kbd className="font-mono text-[10px] border border-hairline bg-surface px-1 rounded">
                Ctrl K
              </kbd>
            </button>

            <button
              onClick={toggleTheme}
              aria-label="Toggle light or dark theme"
              title={`Switch to ${theme === "light" ? "Dark" : "Light"} mode`}
              className="h-7 w-7 rounded border border-hairline bg-subtle flex items-center justify-center text-muted hover:text-ink transition-colors"
            >
              {theme === "light" ? (
                <Moon className="h-3.5 w-3.5" strokeWidth={1.5} />
              ) : (
                <Sun className="h-3.5 w-3.5" strokeWidth={1.5} />
              )}
            </button>
          </div>
        </header>

        {/* Animated Route Content */}
        <main className="flex-1 p-5 lg:p-6 max-w-[1480px] w-full mx-auto">
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.16, ease: "easeOut" }}
            >
              {children}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
