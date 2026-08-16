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
}
