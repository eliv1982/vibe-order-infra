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
  interested_product: string;
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
  interested_product: string;
  budget: string;
  preferred_contact_method: string;
  preferred_contact_time: string;
  comment: string | null;
  created_at: string;
  updated_at: string;
}

// --- backend/app/schemas/behavior_metric.py ---

export interface BehaviorMetricCreatePayload {
  application_id: number;
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
