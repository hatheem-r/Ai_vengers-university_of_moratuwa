import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppStoreProvider, useAppStore } from "@/state/appStore";
import { GlobalErrorBoundary } from "@/components/ui/ErrorBoundary";
import { AppShell } from "@/components/layout/AppShell";
import { StartPage } from "@/pages/StartPage";
import { LiveRunPage } from "@/pages/LiveRunPage";
import { PlanReviewPage } from "@/pages/PlanReviewPage";
import { TracePage } from "@/pages/TracePage";
import { ComparePage } from "@/pages/ComparePage";
import { EvaluationPage } from "@/pages/EvaluationPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 2_000,
    },
  },
});

function ActiveRunRedirect({ suffix = "" }: { suffix?: string }) {
  const { activeRunId } = useAppStore();
  return <Navigate to={`/run/${activeRunId}${suffix}`} replace />;
}

export function App() {
  return (
    <GlobalErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AppStoreProvider>
          <BrowserRouter>
            <AppShell>
              <Routes>
                <Route path="/" element={<StartPage />} />
                <Route path="/run" element={<ActiveRunRedirect />} />
                <Route path="/run/:runId" element={<LiveRunPage />} />
                <Route path="/review" element={<ActiveRunRedirect suffix="/review" />} />
                <Route path="/run/:runId/review" element={<PlanReviewPage />} />
                <Route path="/trace" element={<ActiveRunRedirect suffix="/trace" />} />
                <Route path="/run/:runId/trace" element={<TracePage />} />
                <Route path="/compare" element={<ComparePage />} />
                <Route path="/evaluation" element={<EvaluationPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </AppShell>
          </BrowserRouter>
        </AppStoreProvider>
      </QueryClientProvider>
    </GlobalErrorBoundary>
  );
}
