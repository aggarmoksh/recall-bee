const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type RecentAnswer = {
  answer_id: number;
  question_id: number | null;
  question_text: string | null;
  verdict: string;
  quality: number | null;
  matched_at: string;
};

export type CurrentQuestion = {
  question_number: number;
  question_id: number | null;
  question_text: string | null;
  alarm_at: string;
};

export type SessionState =
  | { state: "idle" }
  | {
      state: "in_session";
      trigger: string;
      questions_asked: number;
      daily_quota: number;
      current_question: CurrentQuestion | null;
    };

export type ReportCard = {
  questions_asked: number;
  questions_answered: number;
  questions_processing: number;
  ended_at: string;
};

export type TodayResponse = {
  daily_quota: number;
  answered_today: number;
  goal_met: boolean;
  streak_days: number;
  session: SessionState;
  recent_answers: RecentAnswer[];
  report_card: ReportCard | null;
};

export type RubricPoint = { point: string; hit: boolean };

export type AnswerDetail = {
  answer_id: number;
  question: { id: number; text: string } | null;
  rubric: RubricPoint[];
  answer_transcript: string;
  verdict: string;
  quality: number | null;
  feedback: string | null;
  hint_in_notification: boolean | null;
  source_excerpt: string | null;
  session_datetime: string | null;
};

export type MasteryQuestion = {
  question_id: number;
  text: string;
  latest_quality: number | null;
  status_label: string | null;
};

export type MasterySession = {
  session_id: number;
  label: string;
  question_count: number;
  average_quality: number | null;
  questions: MasteryQuestion[];
};

export type RetentionPoint = { review_number: number; average_quality: number };
export type RetentionDay = { day: string; points: RetentionPoint[] };

export type MasteryResponse = {
  sessions: MasterySession[];
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
  rules: string[];
};

// Per-entry shape is unconfirmed -- this account's list is empty, and the
// field names aren't documented anywhere (see FRICTION_LOG, Phase 5E Step
// 5). Rendered generically rather than assuming specific keys.
export type ExternalAccessEntry = Record<string, unknown>;
export type ExternalAccessResponse = {
  external_access: ExternalAccessEntry[];
  next_cursor: string | null;
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
  externalAccess: () => get<ExternalAccessResponse>("/api/privacy/external-access"),
  approveSession: (id: number) => post(`/api/sessions/${id}/approve`),
  rejectSession: (id: number) => post(`/api/sessions/${id}/reject`),
  rejectQuestion: (id: number) => post(`/api/questions/${id}/reject`),
};
