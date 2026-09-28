const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type WindowStatus = "waiting" | "listening" | "grading";

export type OpenDispatch = {
  dispatch_id: number;
  question_id: number;
  question_text: string;
  alarm_at: string;
  window_status: WindowStatus;
};

export type RecentAnswer = {
  answer_id: number;
  question_id: number;
  question_text: string;
  verdict: string;
  quality: number | null;
  matched_at: string;
};

export type TodayResponse = {
  profile: "normal" | "dense";
  dispatches_today: number;
  open_dispatch: OpenDispatch | null;
  recent_answers: RecentAnswer[];
};

export type RubricPoint = { point: string; hit: boolean };

export type AnswerDetail = {
  answer_id: number;
  question: { id: number; text: string } | null;
  rubric: RubricPoint[];
  answer_transcript: string;
  verdict: string;
  quality: number | null;
  feedback_text: string | null;
  source_excerpt: string | null;
  session_datetime: string | null;
};

export type TopicMastery = {
  topic: string;
  question_count: number;
  average_latest_quality: number | null;
};

export type RetentionPoint = { review_number: number; average_quality: number };
export type RetentionDay = { day: string; points: RetentionPoint[] };

export type MasteryResponse = {
  topics: TopicMastery[];
  retention_series: RetentionDay[];
};

export type SessionRow = {
  session_id: number;
  date: string | null;
  duration_minutes: number | null;
  session_type: string | null;
  session_type_reason: string | null;
  status: string;
  question_count: number;
};

export type SessionsResponse = { sessions: SessionRow[] };

export type PrivacyResponse = {
  sessions_skipped_by_type: Record<string, number>;
  excluded_sessions_count: number;
  questions_rejected_count: number;
  rules: string[];
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status}`);
  }
  return res.json();
}

async function post(path: string): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST" });
  if (!res.ok) {
    throw new Error(`POST ${path} failed: ${res.status}`);
  }
}

export const api = {
  today: () => get<TodayResponse>("/api/today"),
  answer: (id: number) => get<AnswerDetail>(`/api/answers/${id}`),
  mastery: () => get<MasteryResponse>("/api/mastery"),
  sessions: () => get<SessionsResponse>("/api/sessions"),
  privacy: () => get<PrivacyResponse>("/api/privacy"),
  approveSession: (id: number) => post(`/api/sessions/${id}/approve`),
  rejectSession: (id: number) => post(`/api/sessions/${id}/reject`),
  rejectQuestion: (id: number) => post(`/api/questions/${id}/reject`),
};
