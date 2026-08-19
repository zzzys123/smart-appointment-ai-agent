import type {
  KnowledgeDocument,
  KnowledgeLifecycleStatus,
  KnowledgeListResponse,
  OnlineMetricsResponse,
  ScheduleItem,
  SystemHealthResponse,
  Technician,
  UserAnalysis
} from "./types";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error(`无法连接后端服务（${url}）。请确认 FastAPI 已在 8000 端口启动`);
  }
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const payload = await response.json();
      detail = payload.detail || payload.message || detail;
    } catch {
      // Keep the HTTP fallback when the body is not JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

const jsonHeaders = { "Content-Type": "application/json" };

export const api = {
  listKnowledge: () => request<KnowledgeListResponse>("/api/knowledge/"),
  searchKnowledge: (query: string, category?: string) =>
    request<{ results: KnowledgeDocument[] }>("/api/knowledge/search", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ query, category: category || null, top_k: 20 })
    }),
  createKnowledge: (data: Omit<KnowledgeDocument, "id">) =>
    request("/api/knowledge/", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(data)
    }),
  updateKnowledge: (id: number, data: Omit<KnowledgeDocument, "id">) =>
    request(`/api/knowledge/${id}`, {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify(data)
    }),
  deleteKnowledge: (id: number) =>
    request(`/api/knowledge/${id}`, { method: "DELETE" }),
  importKnowledge: (formData: FormData) =>
    request<{ message: string }>("/api/knowledge/import", {
      method: "POST",
      body: formData
    }),
  knowledgeLifecycle: () =>
    request<{ status: string; data: KnowledgeLifecycleStatus }>("/api/knowledge/lifecycle/status"),
  backupKnowledge: () =>
    request<{ status: string; data: { path: string; sha256: string } }>("/api/knowledge/lifecycle/backup", { method: "POST" }),
  syncManagedKnowledge: () =>
    request<{ status: string; data: { totals: Record<string, number> }; backup?: { path: string } }>("/api/knowledge/lifecycle/sync", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ confirm: true, create_backup_first: true })
    }),
  listTechnicians: () => request<Technician[]>("/api/technicians/"),
  technicianSchedule: (id: number) =>
    request<ScheduleItem[]>(`/api/technicians/${id}/schedule`),
  userAnalysis: () =>
    request<UserAnalysis>("/api/user-behavior/dashboard_data"),
  createReminder: () =>
    request<{ message: string; technician_available_times?: unknown[] }>(
      "/api/user-behavior/send-reminder",
      {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ user_id: "default_user" })
      }
    ),
  systemHealth: () => request<SystemHealthResponse>("/api/system/health"),
  onlineMetrics: (since?: string) =>
    request<OnlineMetricsResponse>(
      `/api/system/metrics${since ? `?since=${encodeURIComponent(since)}` : ""}`
    )
};

export function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误，请稍后重试";
}
