import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiClient,
  ApiClientError,
  getMockHeaderDetected,
  subscribeMockHeader,
} from "./client";
import type { ApprovalRequest, StartRunRequest } from "./types";

export function useMockDetector(): { isMock: boolean; backendOnline: boolean; apiVersion?: string } {
  const [headerMock, setHeaderMock] = useState<boolean>(getMockHeaderDetected);

  useEffect(() => subscribeMockHeader(setHeaderMock), []);

  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => apiClient.getHealth(),
    refetchInterval: 15_000,
    retry: 1,
  });

  return {
    isMock: Boolean(health.data?.mock) || headerMock,
    backendOnline: health.isSuccess,
    apiVersion: health.data?.api_version,
  };
}

export function useScenarios(set: "test" | "dev" | "scale" | "all" = "all") {
  return useQuery({
    queryKey: ["scenarios", set],
    queryFn: () => apiClient.listScenarios(set),
  });
}

export function useUploadBundle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ files, name }: { files: File[]; name?: string }) =>
      apiClient.uploadBundle(files, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scenarios"] });
    },
  });
}

export function useStartRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: StartRunRequest) => apiClient.startRun(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

/**
 * Polls `GET /runs/{run_id}` every 2 seconds until `awaiting_approval`, `completed`, or `failed` (docs/API.md §4.4).
 */
export function useRunStatus(runId: string | undefined) {
  return useQuery({
    queryKey: ["run", runId],
    queryFn: () => apiClient.getRunStatus(runId!),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (!status || status === "queued" || status === "running") {
        return 2000;
      }
      return false;
    },
  });
}

export function useRunsList(bundleId?: string, limit = 30) {
  return useQuery({
    queryKey: ["runs", bundleId ?? "all", limit],
    queryFn: () => apiClient.listRuns({ bundle_id: bundleId, limit }),
  });
}

export function useRunPlan(runId: string | undefined, version?: number, enabled = true) {
  return useQuery({
    queryKey: ["plan", runId, version ?? "latest"],
    queryFn: () => apiClient.getRunPlan(runId!, version),
    enabled: Boolean(runId) && enabled,
    retry: (failureCount, err) => {
      if (err instanceof ApiClientError && (err.status === 409 || err.status === 404)) {
        return false;
      }
      return failureCount < 2;
    },
  });
}

export function useSubmitApproval(runId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ApprovalRequest) => apiClient.submitApproval(runId!, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["run", runId] });
      qc.invalidateQueries({ queryKey: ["plan", runId] });
      qc.invalidateQueries({ queryKey: ["trace", runId] });
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

export function useRunTrace(runId: string | undefined, isLive = false) {
  return useQuery({
    queryKey: ["trace", runId],
    queryFn: () => apiClient.getRunTrace(runId!, 0),
    enabled: Boolean(runId),
    refetchInterval: isLive ? 2000 : false,
  });
}

export function useRunScore(runId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["score", runId],
    queryFn: () => apiClient.getRunScore(runId!),
    enabled: Boolean(runId) && enabled,
    retry: false,
  });
}

export function useEvaluationsList() {
  return useQuery({
    queryKey: ["evaluations"],
    queryFn: () => apiClient.listEvaluations(),
  });
}

export function useEvaluationSummary(evalId = "latest") {
  return useQuery({
    queryKey: ["evaluation", evalId],
    queryFn: () => apiClient.getEvaluation(evalId),
  });
}
