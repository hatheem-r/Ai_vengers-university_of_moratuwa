import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cpu,
  FileSpreadsheet,
  FileText,
  FolderArchive,
  Image as ImageIcon,
  Layers,
  Play,
  Search,
  UploadCloud,
  X,
} from "lucide-react";
import { useScenarios, useStartRun, useUploadBundle } from "@/api/hooks";
import type { BundleFile, MessLevel, ModelProfile, RunMode, ScenarioItem } from "@/api/types";
import { useAppStore } from "@/state/appStore";
import { cn, formatBytes } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { Skeleton } from "@/components/ui/Skeleton";

type SystemChoice = "M-mix" | "M-S" | "B";

const SYSTEM_OPTIONS: Array<{
  id: SystemChoice;
  title: string;
  tag: string;
  mode: RunMode;
  profile: ModelProfile;
  note: string;
}> = [
  {
    id: "M-mix",
    title: "Multi-Agent (M-mix)",
    tag: "Haiku 5.5 + Sonnet 5.5 · Cost-Optimised",
    mode: "multi_agent",
    profile: "mix",
    note: "Parallel Haiku 5.5 Intake → Sonnet 5.5 Planner (mrp_tool + CP-SAT) → Haiku 5.5 Checker (max 1x loop).",
  },
  {
    id: "M-S",
    title: "Multi-Agent (M-S)",
    tag: "All Sonnet 5.5 · Fair Architectural Test",
    mode: "multi_agent",
    profile: "sonnet",
    note: "Identical model (Sonnet 5.5) across Intake, Planner, and Checker to isolate multi-agent gains.",
  },
  {
    id: "B",
    title: "Single-Agent Baseline (B)",
    tag: "Sonnet 5.5 · Control System",
    mode: "baseline",
    profile: "sonnet",
    note: "One ReAct agent reading all documents, planning, and checking in a single context window.",
  },
];

function fileBadgeIcon(type: string) {
  const ext = type.toLowerCase();
  if (ext === "xlsx" || ext === "xls" || ext === "csv") {
    return <FileSpreadsheet className="h-3.5 w-3.5 text-status-nominal" strokeWidth={1.5} />;
  }
  if (ext === "png" || ext === "jpg" || ext === "jpeg") {
    return <ImageIcon className="h-3.5 w-3.5 text-status-warning" strokeWidth={1.5} />;
  }
  if (ext === "zip") {
    return <FolderArchive className="h-3.5 w-3.5 text-status-focus" strokeWidth={1.5} />;
  }
  return <FileText className="h-3.5 w-3.5 text-status-focus" strokeWidth={1.5} />;
}

function messTone(mess: MessLevel) {
  if (mess === "clean") return "nominal" as const;
  if (mess === "typical") return "warning" as const;
  return "critical" as const;
}

const PROFILE_HEADINGS: Record<string, string> = {
  A: "Profile A · Apparel Export (Cut-Make-Trim)",
  B: "Profile B · Light Assembly (Steel Furniture)",
  C: "Profile C · Food Packing (Tea Packer)",
};

export function StartPage() {
  const navigate = useNavigate();
  const { setActiveRunId } = useAppStore();

  const [inputMode, setInputMode] = useState<"scenario" | "upload">("scenario");
  const [setFilter, setSetFilter] = useState<"all" | "test" | "dev" | "scale">("test");
  const [messFilter, setMessFilter] = useState<"all" | MessLevel>("all");
  const [search, setSearch] = useState("");
  const [selectedScenarioId, setSelectedScenarioId] = useState<string>("A_S3_typical");
  const [expandedScenarioId, setExpandedScenarioId] = useState<string | null>("A_S3_typical");

  // Upload state
  const [stagedFiles, setStagedFiles] = useState<File[]>([]);
  const [bundleName, setBundleName] = useState("");
  const [uploadedBundleId, setUploadedBundleId] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // System config
  const [systemChoice, setSystemChoice] = useState<SystemChoice>("M-mix");

  const scenariosQuery = useScenarios("all");
  const uploadMutation = useUploadBundle();
  const startRunMutation = useStartRun();

  const filteredScenarios = useMemo(() => {
    const all = scenariosQuery.data?.scenarios ?? [];
    return all.filter((s) => {
      if (setFilter !== "all" && s.set !== setFilter) return false;
      if (messFilter !== "all" && s.mess_level !== messFilter) return false;
      if (search.trim()) {
        const q = search.toLowerCase();
        const hay = `${s.bundle_id} ${s.profile_name} ${s.scenario} ${s.scenario_name} ${s.mess_level}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [scenariosQuery.data, setFilter, messFilter, search]);

  const groupedByProfile = useMemo(() => {
    const groups: Record<string, ScenarioItem[]> = {};
    for (const sc of filteredScenarios) {
      const key = sc.profile || "Other";
      if (!groups[key]) groups[key] = [];
      groups[key].push(sc);
    }
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }, [filteredScenarios]);

  const activeChoice = SYSTEM_OPTIONS.find((o) => o.id === systemChoice)!;

  const handleStageFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const arr = Array.from(incoming);
    setStagedFiles((prev) => [...prev, ...arr]);
    setUploadedBundleId(null);
  };

  const handleRunCycle = async () => {
    try {
      let targetBundleId = selectedScenarioId;

      if (inputMode === "upload") {
        if (uploadedBundleId) {
          targetBundleId = uploadedBundleId;
        } else {
          if (stagedFiles.length === 0) return;
          const uploaded = await uploadMutation.mutateAsync({
            files: stagedFiles,
            name: bundleName || undefined,
          });
          setUploadedBundleId(uploaded.bundle_id);
          targetBundleId = uploaded.bundle_id;
        }
      }

      const started = await startRunMutation.mutateAsync({
        bundle_id: targetBundleId,
        mode: activeChoice.mode,
        model_profile: activeChoice.profile,
        auto_approve: false,
        seed: 0,
      });

      setActiveRunId(started.run_id);
      navigate(`/run/${started.run_id}`);
    } catch {
      // Handled via mutation error banner
    }
  };

  const canRun =
    !startRunMutation.isPending &&
    !uploadMutation.isPending &&
    (inputMode === "scenario" ? Boolean(selectedScenarioId) : stagedFiles.length > 0 || Boolean(uploadedBundleId));

  return (
    <div className="space-y-5">
      {/* Page Header */}
      <div className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline pb-4">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-wider text-muted">
            Step 01 · Input & Orchestration Setup
          </div>
          <h1 className="text-xl font-semibold tracking-tight mt-0.5">
            Initialize Production Planning Cycle
          </h1>
          <p className="text-xs text-muted mt-1 max-w-2xl">
            Select a synthetic factory bundle or upload raw factory documents (POs, BoMs, stock sheets, maintenance
            logs, scans), choose the agent architecture, and launch the planning pipeline.
          </p>
        </div>
        <div className="flex items-center gap-2 font-mono text-xs text-muted">
          <Badge tone="tool" mono>mrp_tool</Badge>
          <Badge tone="tool" mono>schedule_tool (CP-SAT)</Badge>
          <Badge tone="tool" mono>rule_check</Badge>
        </div>
      </div>

      {(scenariosQuery.error || uploadMutation.error || startRunMutation.error) && (
        <ErrorBanner
          title="Unable to initialize run"
          error={startRunMutation.error || uploadMutation.error || scenariosQuery.error}
          onRetry={() => scenariosQuery.refetch()}
        />
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* LEFT COLUMN (7 cols): Dual Input Mode */}
        <div className="lg:col-span-7 rounded-md border border-hairline bg-surface overflow-hidden">
          {/* Segmented Input Mode Header */}
          <div className="px-4 py-3 border-b border-hairline flex flex-wrap items-center justify-between gap-3 bg-subtle/50">
            <div className="inline-flex rounded border border-hairline bg-surface p-0.5">
              <button
                type="button"
                onClick={() => setInputMode("scenario")}
                className={cn(
                  "px-3 py-1.5 text-xs font-medium rounded-sm transition-colors flex items-center gap-1.5",
                  inputMode === "scenario"
                    ? "bg-status-focus text-white"
                    : "text-muted hover:text-ink"
                )}
              >
                <Layers className="h-3.5 w-3.5" strokeWidth={1.5} />
                Pick Scenario Bundle
              </button>
              <button
                type="button"
                onClick={() => setInputMode("upload")}
                className={cn(
                  "px-3 py-1.5 text-xs font-medium rounded-sm transition-colors flex items-center gap-1.5",
                  inputMode === "upload"
                    ? "bg-status-focus text-white"
                    : "text-muted hover:text-ink"
                )}
              >
                <UploadCloud className="h-3.5 w-3.5" strokeWidth={1.5} />
                Upload Documents
              </button>
            </div>

            {inputMode === "scenario" && (
              <div className="flex items-center gap-1 text-[11px] font-mono">
                {(["test", "dev", "scale", "all"] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setSetFilter(s)}
                    className={cn(
                      "px-2 py-1 rounded border transition-colors uppercase",
                      setFilter === s
                        ? "border-status-focus bg-status-focus/10 text-status-focus font-semibold"
                        : "border-hairline bg-surface text-muted hover:text-ink"
                    )}
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>

          {inputMode === "scenario" ? (
            <div className="p-4 space-y-4">
              {/* Search & Mess Level Filter Bar */}
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative flex-1 min-w-[200px]">
                  <Search
                    className="h-3.5 w-3.5 text-muted absolute left-2.5 top-1/2 -translate-y-1/2"
                    strokeWidth={1.5}
                  />
                  <input
                    type="text"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Filter by bundle ID, scenario (S1–S9), or factory..."
                    className="w-full h-8 pl-8 pr-3 rounded border border-hairline bg-subtle/60 text-xs text-ink placeholder:text-faint focus:outline-none focus:border-status-focus"
                  />
                </div>
                <div className="flex items-center gap-1">
                  {(["all", "clean", "typical", "messy"] as const).map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setMessFilter(m)}
                      className={cn(
                        "h-8 px-2.5 rounded border text-xs capitalize transition-colors",
                        messFilter === m
                          ? "border-ink bg-ink text-surface font-medium"
                          : "border-hairline bg-surface text-muted hover:text-ink"
                      )}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              </div>

              {/* Grouped Scenario List */}
              {scenariosQuery.isLoading ? (
                <div className="space-y-2">
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-16 w-full" />
                </div>
              ) : groupedByProfile.length === 0 ? (
                <div className="py-10 text-center text-xs text-muted border border-dashed border-hairline rounded">
                  No scenario bundles match your current filter.
                </div>
              ) : (
                <div className="space-y-4 max-h-[500px] overflow-y-auto pr-1">
                  {groupedByProfile.map(([profileKey, items]) => (
                    <div key={profileKey} className="space-y-1.5">
                      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted px-0.5 flex items-center justify-between">
                        <span>{PROFILE_HEADINGS[profileKey] ?? items[0]?.profile_name ?? profileKey}</span>
                        <span className="font-mono text-faint">{items.length} bundles</span>
                      </div>
                      <div className="space-y-1.5">
                        {items.map((sc) => {
                          const isSelected = sc.bundle_id === selectedScenarioId;
                          const isExpanded = sc.bundle_id === expandedScenarioId;
                          return (
                            <div
                              key={sc.bundle_id}
                              className={cn(
                                "rounded border transition-colors",
                                isSelected
                                  ? "border-status-focus bg-status-focus/[0.04]"
                                  : "border-hairline bg-surface hover:border-strong"
                              )}
                            >
                              <div
                                role="button"
                                tabIndex={0}
                                onClick={() => setSelectedScenarioId(sc.bundle_id)}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" || e.key === " ") setSelectedScenarioId(sc.bundle_id);
                                }}
                                className="p-3 flex items-center justify-between gap-3 cursor-pointer"
                              >
                                <div className="flex items-start gap-2.5 min-w-0">
                                  <div
                                    className={cn(
                                      "mt-0.5 h-4 w-4 rounded-full border flex items-center justify-center shrink-0",
                                      isSelected
                                        ? "border-status-focus bg-status-focus text-white"
                                        : "border-strong bg-surface"
                                    )}
                                  >
                                    {isSelected && <CheckCircle2 className="h-3 w-3" strokeWidth={2.2} />}
                                  </div>
                                  <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <span className="font-mono text-xs font-semibold text-ink">
                                        {sc.bundle_id}
                                      </span>
                                      <Badge tone="focus" mono>
                                        {sc.scenario} · {sc.scenario_name}
                                      </Badge>
                                      <Badge tone={messTone(sc.mess_level)} mono>
                                        {sc.mess_level}
                                      </Badge>
                                    </div>
                                    <div className="text-[11px] text-muted font-mono mt-1">
                                      {sc.n_documents} input files · {sc.n_orders}{" "}
                                      {sc.n_orders === 1 ? "customer order" : "customer orders"} · set:{sc.set}
                                    </div>
                                  </div>
                                </div>

                                {sc.files.length > 0 && (
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setExpandedScenarioId(isExpanded ? null : sc.bundle_id);
                                    }}
                                    className="p-1 rounded text-muted hover:text-ink hover:bg-subtle text-[11px] font-mono flex items-center gap-1 shrink-0"
                                  >
                                    <span>Files</span>
                                    {isExpanded ? (
                                      <ChevronDown className="h-3.5 w-3.5" />
                                    ) : (
                                      <ChevronRight className="h-3.5 w-3.5" />
                                    )}
                                  </button>
                                )}
                              </div>

                              {isExpanded && sc.files.length > 0 && (
                                <div className="px-3 pb-3 pt-1.5 border-t border-hairline/70 grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                                  {sc.files.map((f: BundleFile) => (
                                    <div
                                      key={f.name}
                                      className="flex items-center justify-between gap-2 rounded bg-subtle/70 border border-hairline px-2 py-1 text-[11px]"
                                    >
                                      <span className="flex items-center gap-1.5 truncate font-mono">
                                        {fileBadgeIcon(f.type)}
                                        <span className="truncate">{f.name}</span>
                                      </span>
                                      <span className="font-mono text-faint shrink-0">
                                        {f.pages ? `${f.pages}p · ` : ""}
                                        {formatBytes(f.size_bytes)}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            /* Upload Document Bundle Tab */
            <div className="p-4 space-y-4">
              <div>
                <label className="block text-xs font-medium text-ink mb-1">
                  Planning Cycle Name <span className="text-muted font-normal">(optional)</span>
                </label>
                <input
                  type="text"
                  value={bundleName}
                  onChange={(e) => setBundleName(e.target.value)}
                  placeholder="e.g., Polo Order Week 48 — Line 1 & 2"
                  className="w-full h-8 px-3 rounded border border-hairline bg-subtle/50 text-xs text-ink placeholder:text-faint focus:outline-none focus:border-status-focus"
                />
              </div>

              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragActive(true);
                }}
                onDragLeave={() => setDragActive(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragActive(false);
                  handleStageFiles(e.dataTransfer.files);
                }}
                onClick={() => fileInputRef.current?.click()}
                className={cn(
                  "rounded-md border-2 border-dashed p-8 text-center cursor-pointer transition-colors",
                  dragActive
                    ? "border-status-focus bg-status-focus/5"
                    : "border-hairline bg-subtle/30 hover:border-strong"
                )}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".pdf,.xlsx,.xls,.csv,.txt,.png,.jpg,.jpeg,.zip"
                  onChange={(e) => handleStageFiles(e.target.files)}
                  className="hidden"
                />
                <UploadCloud className="h-7 w-7 text-status-focus mx-auto mb-2" strokeWidth={1.5} />
                <div className="text-xs font-semibold text-ink">
                  Drop factory documents here, or click to browse
                </div>
                <p className="text-[11px] text-muted mt-1">
                  Supports PDF (including scans), XLSX, CSV, TXT supplier emails, PNG/JPG, or a single ZIP (max 25
                  files, 20 MB/file)
                </p>
              </div>

              {stagedFiles.length > 0 && (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-ink">
                      Staged Documents ({stagedFiles.length})
                    </span>
                    <button
                      type="button"
                      onClick={() => {
                        setStagedFiles([]);
                        setUploadedBundleId(null);
                      }}
                      className="text-[11px] text-muted hover:text-status-critical"
                    >
                      Clear all
                    </button>
                  </div>
                  <div className="max-h-52 overflow-y-auto space-y-1">
                    {stagedFiles.map((file, idx) => {
                      const ext = file.name.split(".").pop() || "file";
                      return (
                        <div
                          key={`${file.name}-${idx}`}
                          className="flex items-center justify-between gap-2 rounded border border-hairline bg-subtle/50 px-2.5 py-1.5 text-xs"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            {fileBadgeIcon(ext)}
                            <span className="font-mono truncate">{file.name}</span>
                            <Badge tone="neutral" mono className="uppercase">
                              {ext}
                            </Badge>
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            <span className="font-mono text-[11px] text-muted">
                              {formatBytes(file.size)}
                            </span>
                            <button
                              type="button"
                              onClick={() =>
                                setStagedFiles((prev) => prev.filter((_, i) => i !== idx))
                              }
                              className="text-muted hover:text-status-critical"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* RIGHT COLUMN (5 cols): System Architecture Selector & Primary CTA */}
        <div className="lg:col-span-5 space-y-4">
          <div className="rounded-md border border-hairline bg-surface p-4 space-y-4">
            <div className="flex items-center justify-between border-b border-hairline pb-3">
              <div className="flex items-center gap-2">
                <Cpu className="h-4 w-4 text-status-focus" strokeWidth={1.5} />
                <h2 className="text-sm font-semibold">System Architecture</h2>
              </div>
              <span className="font-mono text-[11px] text-muted">system_id: {systemChoice}</span>
            </div>

            <div className="space-y-2.5">
              {SYSTEM_OPTIONS.map((opt) => {
                const active = opt.id === systemChoice;
                return (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => setSystemChoice(opt.id)}
                    className={cn(
                      "w-full text-left rounded border p-3 transition-colors space-y-1",
                      active
                        ? "border-status-focus bg-status-focus/[0.05]"
                        : "border-hairline bg-surface hover:border-strong"
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-ink">{opt.title}</span>
                      <Badge tone={active ? "focus" : "neutral"} mono>
                        {opt.id}
                      </Badge>
                    </div>
                    <div className="text-[11px] font-mono text-status-focus">{opt.tag}</div>
                    <p className="text-xs text-muted leading-relaxed">{opt.note}</p>
                  </button>
                );
              })}
            </div>

            {/* Budget & Human Gate Summary */}
            <div className="rounded bg-subtle/70 border border-hairline p-3 space-y-1.5 text-[11px]">
              <div className="flex items-center justify-between">
                <span className="text-muted">Run Guardrails</span>
                <span className="font-mono text-ink">40 LLM calls · 1.5M tok · 600s</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted">Human Oversight</span>
                <span className="font-mono text-status-nominal">LangGraph interrupt() enabled</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted">Selected Input</span>
                <span className="font-mono text-ink font-semibold">
                  {inputMode === "scenario"
                    ? selectedScenarioId
                    : `${stagedFiles.length} uploaded file(s)`}
                </span>
              </div>
            </div>

            <Button
              variant="primary"
              size="lg"
              disabled={!canRun}
              onClick={handleRunCycle}
              className="w-full font-semibold"
            >
              <Play className="h-4 w-4 fill-current" strokeWidth={1.5} />
              {startRunMutation.isPending || uploadMutation.isPending
                ? "Initializing Pipeline..."
                : "Run Planning Cycle"}
              <ArrowRight className="h-4 w-4" strokeWidth={1.5} />
            </Button>
          </div>

          {/* Quick Demo Guide Card */}
          <div className="rounded-md border border-hairline bg-surface p-4 space-y-2 text-xs">
            <div className="font-semibold text-ink">5-Minute Judge Walkthrough</div>
            <ol className="space-y-1 text-muted list-decimal pl-4">
              <li>
                Run <span className="font-mono text-ink">A_S3_typical</span> on{" "}
                <span className="font-mono text-ink">M-mix</span> and watch parallel Intake, Planner tools, and Checker
                revision live.
              </li>
              <li>
                Inspect the Machine Gantt, shortage POs, and document citations, then Approve or Reject with notes.
              </li>
              <li>
                Open <strong>Trace</strong> to inspect JSONL agent hand-offs, <strong>Compare</strong> vs. Baseline{" "}
                <span className="font-mono text-ink">B</span>, and review <strong>Evaluation</strong> charts.
              </li>
            </ol>
          </div>
        </div>
      </div>
    </div>
  );
}
