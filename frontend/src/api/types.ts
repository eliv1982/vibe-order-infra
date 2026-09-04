/**
 * Wire types for the backend API. Field names/shapes mirror the Pydantic
 * schemas in backend/app/schemas/ exactly — do not rename fields here to
 * "improve" them; the backend is the source of truth.
 *
 * Decimal fields (budget, budget_min, budget_max) are serialized by
 * FastAPI as JSON strings in *Read responses (confirmed against
 * AdminSettingRead.model_dump_json()), but the Create/Update payloads
 * accept a plain number.
 */

// --- backend/app/schemas/admin_setting.py ---

export interface AdminSettingRead {
  id: number;
  service_name: string;
  budget_min: string;
  budget_max: string;
  description: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AdminSettingCreatePayload {
  service_name: string;
  budget_min: number;
  budget_max: number;
  description?: string | null;
  is_active?: boolean;
}

export interface AdminSettingUpdatePayload {
  service_name?: string;
  budget_min?: number;
  budget_max?: number;
  description?: string | null;
  is_active?: boolean;
}

// --- backend/app/schemas/application.py ---

export interface ApplicationCreatePayload {
  first_name: string;
  last_name: string;
  middle_name?: string | null;
  contact_data: string;
  business_niche: string;
  company_size: string;
  business_info: string;
  task_scope: string;
  requester_role: string;
  business_size: string;
  need_scope: string;
  deadline: string;
  task_type: string;
  // A stable server-side service identifier (AdminSettingRead.id), not a
  // free-text name - the backend looks it up, verifies it exists/is
  // active, and derives interested_product (below) from it server-side
  // (see backend/app/schemas/application.py). Replaces the old
  // interested_product client field as of Stage 1B.
  service_id: number;
  budget: number;
  preferred_contact_method: string;
  preferred_contact_time: string;
  comment?: string | null;
}

export interface ApplicationRead {
  id: number;
  first_name: string;
  last_name: string;
  middle_name: string | null;
  contact_data: string;
  business_niche: string;
  company_size: string;
  business_info: string;
  task_scope: string;
  requester_role: string;
  business_size: string;
  need_scope: string;
  deadline: string;
  task_type: string;
  // Nullable: a historical row from a database upgraded from the pre-
  // Stage-1B baseline has no service to point at (see
  // backend/app/core/schema_compat.py) - every row created through
  // Stage 1B's POST /applications always has a real value here.
  service_id: number | null;
  interested_product: string;
  budget: string;
  preferred_contact_method: string;
  preferred_contact_time: string;
  comment: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * POST /applications' response only - ApplicationRead plus the
 * behavior_metrics_capability token the client needs to submit behavior
 * metrics for this application (see
 * backend/app/schemas/application.py::ApplicationCreateRead and
 * backend/app/crud/application_behavior_capability.py). Never returned by
 * GET/PATCH - the raw token isn't stored server-side, so there would be
 * nothing to return even if they tried.
 *
 * Nullable (Stage 1B): a real, usable one-time token is issued exactly once
 * - on the winning, first-ever creation. Every idempotent retry (see the
 * Idempotency-Key header on api.createApplication) gets null here instead,
 * regardless of whether the original capability was already consumed - a
 * replay is never treated as authorization to mint or recover a capability.
 * See sendBehaviorMetrics in pages/home.ts for how a null value is handled
 * (skipped, not an error).
 */
export interface ApplicationCreateRead extends ApplicationRead {
  behavior_metrics_capability: string | null;
}

// --- backend/app/schemas/application_analysis.py ---

export interface ScoringReason {
  code: string;
  points: number;
  label: string;
}

export type PriorityLevel = 'hot' | 'medium' | 'low';

export interface ApplicationPriorityRead {
  application: ApplicationRead;
  priority_score: number;
  priority_level: PriorityLevel;
  priority_label: string;
  reasons: ScoringReason[];
  recommended_action: string;
  recommended_team: string;
  requires_personal_manager: boolean;
}

export interface PrioritizedApplicationList {
  items: ApplicationPriorityRead[];
  total: number;
  skip: number;
  limit: number;
}

// --- backend/app/schemas/behavior_metric.py ---

export interface BehaviorMetricCreatePayload {
  application_id: number;
  // One-time token returned as behavior_metrics_capability from
  // POST /applications (see ApplicationCreateRead above) - required,
  // single-use, and bound to this exact application_id. Without it (or with
  // the wrong one) the backend rejects the submission (see
  // backend/app/routes/behavior_metrics.py).
  capability: string;
  time_on_page?: number;
  clicked_buttons?: unknown[];
  cursor_hover_data?: Record<string, unknown>;
  return_count?: number;
}

export interface BehaviorMetricRead {
  id: number;
  application_id: number;
  time_on_page: number;
  clicked_buttons: unknown[];
  cursor_hover_data: Record<string, unknown>;
  return_count: number;
  created_at: string;
  updated_at: string;
}

// --- backend/app/schemas/analytics.py ---

export type AnalyticsPeriod = 'day' | 'week' | 'month';

export interface ButtonAnalyticsItem {
  name: string;
  count: number;
  share_percent: number;
}

export interface SectionAnalyticsItem {
  section: string;
  total_duration_seconds: number;
  average_duration_seconds: number;
  interactions_count: number;
  share_percent: number;
}

export interface AnalyticsOverview {
  period: AnalyticsPeriod;
  period_start: string;
  period_end: string;
  applications_count: number;
  metrics_count: number;
  applications_with_metrics: number;
  applications_without_metrics: number;
  average_time_on_page_seconds: number | null;
  median_time_on_page_seconds: number | null;
  // Mean/sum of return_count (see ApplicationBehaviorAnalytics.return_count
  // below for what that value actually is) across every metric in the
  // period - the admin UI labels these "Визиты с устройства", not
  // "Возвраты" (see pages/adminAnalytics.ts).
  average_return_count: number | null;
  total_return_count: number;
  total_button_clicks: number;
  unique_clicked_buttons: number;
  popular_buttons: ButtonAnalyticsItem[];
  section_activity: SectionAnalyticsItem[];
}

export interface ApplicationBehaviorAnalytics {
  application_id: number;
  has_metrics: boolean;
  time_on_page_seconds: number | null;
  // The value of a long-lived counter kept in the applicant's own browser
  // localStorage (see backend/app/models/behavior_metric.py and frontend/
  // src/metrics/behaviorMetrics.ts::readAndIncrementReturnCount), read at
  // the moment this application was submitted - "which visit number, on
  // that browser, this submission happened to be" (1 = no prior visit was
  // ever recorded on it). NOT a count of returns to this specific form,
  // a session count, or a distinct-visitor count - see backend/app/
  // services/behavior_analytics.py's module docstring for the full,
  // end-to-end-traced explanation. Displayed as "Счётчик визитов
  // (устройство)" (see pages/adminApplications.ts), never as "returns".
  return_count: number | null;
  clicked_buttons: ButtonAnalyticsItem[];
  section_activity: SectionAnalyticsItem[];
  total_button_clicks: number;
  recorded_at: string | null;
}

// --- backend/app/schemas/auth.py ---

export interface AdminLoginPayload {
  username: string;
  password: string;
}

export interface AdminRead {
  id: number;
  username: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthCheckResponse {
  admin_exists: boolean;
}
