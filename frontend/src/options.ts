/**
 * Option lists for the Application form's "enum-like" business fields.
 *
 * The backend stores these as plain VARCHAR/TEXT with no enum constraint
 * (see backend/app/models/application.py), so there is no fixed set of
 * values to match — these are frontend-chosen, human-readable labels
 * stored verbatim as the field value.
 */

export const COMPANY_SIZE_OPTIONS = [
  '1–10 сотрудников',
  '11–50 сотрудников',
  '51–200 сотрудников',
  'Более 200 сотрудников',
];

export const BUSINESS_SIZE_OPTIONS = [
  'Индивидуальный предприниматель',
  'Малый бизнес',
  'Средний бизнес',
  'Крупный бизнес',
];

export const REQUESTER_ROLE_OPTIONS = ['Сотрудник', 'Руководитель'];

export const TASK_SCOPE_OPTIONS = [
  'Разовая задача',
  'Постоянное сотрудничество',
  'Пробный проект',
];

export const TASK_TYPE_OPTIONS = [
  'Разработка с нуля',
  'Доработка существующего',
  'Консультация',
  'Другое',
];

export const DEADLINE_OPTIONS = [
  'Срочно (1–3 дня)',
  'В течение недели',
  'В течение месяца',
  'Гибкий срок',
];

export const PREFERRED_CONTACT_METHOD_OPTIONS = ['Телефон', 'Email', 'Telegram', 'WhatsApp'];

export const PREFERRED_CONTACT_TIME_OPTIONS = [
  'Утро (9:00–12:00)',
  'День (12:00–17:00)',
  'Вечер (17:00–21:00)',
  'В любое время',
];
