import React, { createContext, useContext, useEffect, useMemo, useState } from "react";

interface AppStoreValue {
  activeRunId: string;
  setActiveRunId: (id: string) => void;
  theme: "light" | "dark";
  toggleTheme: () => void;
  sidebarCollapsed: boolean;
  setSidebarCollapsed: React.Dispatch<React.SetStateAction<boolean>>;
  commandOpen: boolean;
  setCommandOpen: React.Dispatch<React.SetStateAction<boolean>>;
}

const AppStoreContext = createContext<AppStoreValue | null>(null);

const DEFAULT_SEEDED_RUN_ID = "run_20261124_093000_mmix";

export function AppStoreProvider({ children }: { children: React.ReactNode }) {
  const [activeRunId, setActiveRunIdState] = useState<string>(() => {
    return localStorage.getItem("wop_active_run_id") || DEFAULT_SEEDED_RUN_ID;
  });

  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem("wop_theme");
    return saved === "dark" ? "dark" : "light";
  });

  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(false);
  const [commandOpen, setCommandOpen] = useState<boolean>(false);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "dark") {
      root.classList.add("dark");
    } else {
      root.classList.remove("dark");
    }
    localStorage.setItem("wop_theme", theme);
  }, [theme]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const setActiveRunId = (id: string) => {
    setActiveRunIdState(id);
    localStorage.setItem("wop_active_run_id", id);
  };

  const toggleTheme = () => setTheme((prev) => (prev === "light" ? "dark" : "light"));

  const value = useMemo(
    () => ({
      activeRunId,
      setActiveRunId,
      theme,
      toggleTheme,
      sidebarCollapsed,
      setSidebarCollapsed,
      commandOpen,
      setCommandOpen,
    }),
    [activeRunId, theme, sidebarCollapsed, commandOpen]
  );

  return <AppStoreContext.Provider value={value}>{children}</AppStoreContext.Provider>;
}

export function useAppStore(): AppStoreValue {
  const ctx = useContext(AppStoreContext);
  if (!ctx) throw new Error("useAppStore must be used inside AppStoreProvider");
  return ctx;
}
