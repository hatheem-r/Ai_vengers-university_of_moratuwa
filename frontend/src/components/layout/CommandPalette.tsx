import { useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  BarChart3,
  ClipboardCheck,
  GitCompare,
  ListTree,
  Moon,
  PlayCircle,
  Search,
  Sun,
} from "lucide-react";
import { useAppStore } from "@/state/appStore";

export function CommandPalette() {
  const { commandOpen, setCommandOpen, activeRunId, theme, toggleTheme } = useAppStore();
  const [query, setQuery] = useState("");
  const navigate = useNavigate();

  const actions = useMemo(
    () => [
      {
        id: "start",
        label: "Go to Start · Select Scenario or Upload Bundle",
        shortcut: "1",
        icon: PlayCircle,
        run: () => navigate("/"),
      },
      {
        id: "live",
        label: `Go to Live Run (${activeRunId})`,
        shortcut: "2",
        icon: Activity,
        run: () => navigate(`/run/${activeRunId}`),
      },
      {
        id: "review",
        label: `Go to Plan Review & Gantt (${activeRunId})`,
        shortcut: "3",
        icon: ClipboardCheck,
        run: () => navigate(`/run/${activeRunId}/review`),
      },
      {
        id: "trace",
        label: `Go to Agent Hand-off Trace (${activeRunId})`,
        shortcut: "4",
        icon: ListTree,
        run: () => navigate(`/run/${activeRunId}/trace`),
      },
      {
        id: "compare",
        label: "Go to Compare Systems (Multi-Agent vs Baseline)",
        shortcut: "5",
        icon: GitCompare,
        run: () => navigate("/compare"),
      },
      {
        id: "evaluation",
        label: "Go to Benchmark Evaluation Dashboard",
        shortcut: "6",
        icon: BarChart3,
        run: () => navigate("/evaluation"),
      },
      {
        id: "theme",
        label: `Switch to ${theme === "light" ? "Dark" : "Light"} Industrial Theme`,
        shortcut: "T",
        icon: theme === "light" ? Moon : Sun,
        run: () => toggleTheme(),
      },
    ],
    [activeRunId, navigate, theme, toggleTheme]
  );

  const filtered = actions.filter((a) =>
    a.label.toLowerCase().includes(query.trim().toLowerCase())
  );

  return (
    <Dialog.Root open={commandOpen} onOpenChange={setCommandOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/35 backdrop-blur-[1px]" />
        <Dialog.Content className="fixed left-1/2 top-[18%] z-50 w-full max-w-lg -translate-x-1/2 rounded-lg border border-hairline bg-surface text-ink overflow-hidden focus:outline-none">
          <Dialog.Title className="sr-only">Quick Jump Command Palette</Dialog.Title>
          <div className="flex items-center gap-2.5 px-3.5 py-2.5 border-b border-hairline">
            <Search className="h-4 w-4 text-muted" strokeWidth={1.5} />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Jump to view, active run, or toggle theme..."
              className="w-full bg-transparent text-xs text-ink placeholder:text-faint focus:outline-none"
            />
            <kbd className="rounded border border-hairline bg-subtle px-1.5 py-0.5 font-mono text-[10px] text-muted">
              ESC
            </kbd>
          </div>
          <div className="max-h-72 overflow-y-auto p-1.5 space-y-0.5">
            {filtered.length === 0 ? (
              <div className="py-6 text-center text-xs text-muted">No matching command.</div>
            ) : (
              filtered.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    onClick={() => {
                      item.run();
                      setCommandOpen(false);
                      setQuery("");
                    }}
                    className="w-full flex items-center justify-between gap-3 rounded px-3 py-2 text-left text-xs text-ink hover:bg-subtle transition-colors"
                  >
                    <span className="flex items-center gap-2.5">
                      <Icon className="h-3.5 w-3.5 text-muted" strokeWidth={1.5} />
                      <span>{item.label}</span>
                    </span>
                    <kbd className="font-mono text-[10px] text-muted border border-hairline rounded px-1.5 py-0.5">
                      {item.shortcut}
                    </kbd>
                  </button>
                );
              })
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
