import type {
  ApiErrorEnvelope,
  ApprovalRequest,
  ApprovalResponse,
  BundleResponse,
  EvaluationListItem,
  EvaluationSummary,
  HealthResponse,
  PlanResponse,
  RunScore,
  RunsListResponse,
  RunStatusResponse,
  ScenariosResponse,
  StartRunRequest,
  StartRunResponse,
  TraceResponse,
} from "./types";

export const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/+$/, "") ||
  "http://localhost:8000/api/v1";

export class ApiClientError extends Error {
  public readonly status: number;
  public readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
  }
}

/* Reactive tracker for the `X-Mock: true` HTTP header (docs/API.md §2) */
let mockHeaderDetected = false;
const mockSubscribers = new Set<(isMock: boolean) => void>();

export function getMockHeaderDetected(): boolean {
  return mockHeaderDetected;
}

export function subscribeMockHeader(fn: (isMock: boolean) => void): () => void {
  mockSubscribers.add(fn);
  return () => {
    mockSubscribers.delete(fn);
  };
}

function updateMockHeader(headers: Headers): void {
  const headerVal = headers.get("x-mock");
  if (headerVal !== null) {
    const next = headerVal.toLowerCase() === "true";
    if (next !== mockHeaderDetected) {
      mockHeaderDetected = next;
      mockSubscribers.forEach((cb) => cb(mockHeaderDetected));
    }
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch (err) {
    throw new ApiClientError(
      0,
      "network_error",
      `Cannot reach FastAPI server at ${API_BASE_URL}. Start it with: MOCK=1 uvicorn project.api.app:app --reload --port 8000 (${
        err instanceof Error ? err.message : "connection refused"
      })`
    );
  }

  updateMockHeader(response.headers);

  if (!response.ok) {
    let code = "internal";
    let message = `HTTP ${response.status} ${response.statusText}`;
    try {
      const data = (await response.json()) as Partial<ApiErrorEnvelope>;
      if (data?.error) {
        code = data.error.code || code;
        message = data.error.message || message;
      }
    } catch {
      // keep fallback HTTP message
    }
    throw new ApiClientError(response.status, code, message);
  }

  return (await response.json()) as T;
}

export const apiClient = {
  /** §4.11 Health */
  getHealth(): Promise<HealthResponse> {
    return requestJson<HealthResponse>("/health");
  },

  /** §4.1 Upload a document bundle */
  uploadBundle(files: File[], name?: string): Promise<BundleResponse> {
    const form = new FormData();
    for (const file of files) {
      form.append("files", file);
    }
    if (name && name.trim()) {
      form.append("name", name.trim());
    }
    return requestJson<BundleResponse>("/bundles", {
      method: "POST",
      body: form,
    });
  },

  /** §4.1 Get bundle details */
  getBundle(bundleId: string): Promise<BundleResponse> {
    return requestJson<BundleResponse>(`/bundles/${encodeURIComponent(bundleId)}`);
  },

  /** §4.2 List synthetic scenarios */
  listScenarios(set: "test" | "dev" | "scale" | "all" = "test"): Promise<ScenariosResponse> {
    return requestJson<ScenariosResponse>(`/scenarios?set=${encodeURIComponent(set)}`);
  },

  /** §4.3 Start a planning run */
  startRun(payload: StartRunRequest): Promise<StartRunResponse> {
    return requestJson<StartRunResponse>("/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  /** §4.4 Poll run status and per-agent progress */
  getRunStatus(runId: string): Promise<RunStatusResponse> {
    return requestJson<RunStatusResponse>(`/runs/${encodeURIComponent(runId)}`);
  },

  /** §4.5 List runs (optional bundle filter) */
  listRuns(params?: { bundle_id?: string; limit?: number }): Promise<RunsListResponse> {
    const qs = new URLSearchParams();
    if (params?.bundle_id) qs.set("bundle_id", params.bundle_id);
    if (params?.limit) qs.set("limit", String(params.limit));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return requestJson<RunsListResponse>(`/runs${suffix}`);
  },

  /** §4.6 Fetch WorkOrderPlan */
  getRunPlan(runId: string, version?: number): Promise<PlanResponse> {
    const qs = version !== undefined ? `?version=${version}` : "";
    return requestJson<PlanResponse>(`/runs/${encodeURIComponent(runId)}/plan${qs}`);
  },

  /** §4.7 Approve or reject plan with notes */
  submitApproval(runId: string, payload: ApprovalRequest): Promise<ApprovalResponse> {
    return requestJson<ApprovalResponse>(`/runs/${encodeURIComponent(runId)}/approval`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  /** §4.8 Fetch trace events (JSON) */
  getRunTrace(runId: string, afterSeq = 0): Promise<TraceResponse> {
    return requestJson<TraceResponse>(
      `/runs/${encodeURIComponent(runId)}/trace?format=json&after_seq=${afterSeq}`
    );
  },

  /** §4.8 Download raw JSONL trace file */
  async downloadTraceJsonl(runId: string): Promise<void> {
    const url = `${API_BASE_URL}/runs/${encodeURIComponent(runId)}/trace?format=jsonl`;
    const res = await fetch(url);
    updateMockHeader(res.headers);
    if (!res.ok) {
      throw new ApiClientError(res.status, "trace_download_failed", "Failed to download trace.jsonl");
    }
    const text = await res.text();
    const blob = new Blob([text], { type: "application/x-ndjson" });
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = `${runId}_trace.jsonl`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(blobUrl);
  },

  /** §4.10 Score one run against its answer key */
  getRunScore(runId: string): Promise<RunScore> {
    return requestJson<RunScore>(`/runs/${encodeURIComponent(runId)}/score`);
  },

  /** §4.9 List evaluation benchmark sets */
  listEvaluations(): Promise<{ evaluations: EvaluationListItem[] }> {
    return requestJson<{ evaluations: EvaluationListItem[] }>("/evaluations");
  },

  /** §4.9 Get evaluation benchmark summary */
  getEvaluation(evalId = "latest"): Promise<EvaluationSummary> {
    return requestJson<EvaluationSummary>(`/evaluations/${encodeURIComponent(evalId)}`);
  },
};
