export interface KnowledgeDocument {
  id: number;
  content: string;
  category: string;
  keywords?: string[] | string;
  source?: string;
  source_id?: string;
  section?: string;
  score?: number;
}

export interface KnowledgeListResponse {
  documents: KnowledgeDocument[];
  categories: string[];
  total_count: number;
  status: string;
}

export interface KnowledgeLifecycleStatus {
  manifest: {
    status: string;
    matches: boolean;
    expected_chunk_count?: number;
    actual_chunk_count?: number;
  };
  database: {
    status: string;
    matches: boolean;
    active_chunk_count: number;
    managed_active_chunk_count: number;
    unmanaged_active_chunk_count: number;
    expected_chunk_count: number;
    missing_sources: string[];
    mismatched_sources: string[];
    extra_sources: string[];
  };
  source: { document_count: number; chunk_count: number };
}

export interface Technician {
  id: number;
  name: string;
  gender: string;
  strength: string;
}

export interface ScheduleItem {
  id: number;
  technician_id: number;
  start_time: string;
  end_time: string;
  status: string;
  appointment_id?: number | null;
}

export interface UserAnalysis {
  favorite_technician_id?: number | null;
  favorite_technician_name?: string | null;
  favorite_service?: string | null;
  favorite_duration?: number | null;
  total_appointments: number;
  days_since_last_appointment?: number | null;
  should_send_reminder: boolean;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
  agent?: string;
  thoughts?: Array<{ agent: string; content: string }>;
  sources?: CitationSource[];
}

export interface CitationSource {
  source_id?: string | null;
  source_name?: string | null;
  title?: string | null;
  category?: string | null;
  chunk_number?: number | null;
  chunk_count?: number | null;
  document_id?: number | null;
}

export interface HealthCheck {
  status: string;
  [key: string]: unknown;
}

export interface SystemHealthResponse {
  status: "ok" | "degraded";
  checked_at: string;
  checks: Record<string, HealthCheck>;
}

export interface MetricDistribution {
  mean: number;
  p50: number;
  p95: number;
  p99: number;
}

export interface OnlineMetricsResponse {
  report: string;
  generated_at: string;
  retrieval: {
    event_count: number;
    invalid_jsonl_lines: number;
    latency_ms: MetricDistribution;
    no_answer_rate: number;
    error_rate: number;
    rerank_trigger_rate: number;
    rerank_fallback_rate: number;
    rerank_provider_counts: Record<string, number>;
  };
  model_usage: {
    event_count: number;
    invalid_jsonl_lines: number;
    provider_token_count: MetricDistribution;
    estimated_embedding_token_count: MetricDistribution;
    logical_calls_per_consultation: MetricDistribution;
    failed_call_rate: number;
    cost_coverage_rate: number;
    cost_cny_per_consultation: MetricDistribution;
    cost_cny_total: number;
    cost_status_counts: Record<string, number>;
    stage_totals: Record<string, Record<string, number>>;
  };
  notes: string[];
}
