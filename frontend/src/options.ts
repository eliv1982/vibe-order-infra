/**
 * Option lists for the Application form's "enum-like" fields.
 *
 * The backend stores these as plain VARCHAR/TEXT with no enum constraint
 * (see backend/app/models/application.py), so there is no fixed set of
 * values to match — these are frontend-chosen, human-readable labels
 * stored verbatim as the field value.
 *
 * Legacy naming note: the exported constant names and the underlying
 * field/column names they back (business_niche, company_size,
 * business_size, requester_role, task_scope, task_type, deadline,
 * need_scope, ...) still mirror the original backend API/DB names for
 * backward compatibility with the existing payload contract. None of
 * these internal names are shown to the user — only the Russian labels
 * and option values below are user-facing, and they now describe the
 * vehicle/detailing intake for AUREL Detailing, not the original
 * IT-services/freelance wording the names were coined for.
 */

// Backs `business_niche` — how the vehicle is used.
export const BUSINESS_NICHE_OPTIONS = [
  'Личный автомобиль',
  'Семейный автомобиль',
  'Служебный автомобиль',
  'Такси или коммерческие перевозки',
  'Автопарк компании',
  'Автосалон или дилер',
  'Другое',
];

// Backs `company_size` — vehicle class.
export const COMPANY_SIZE_OPTIONS = [
  'Компактный автомобиль',
  'Седан или универсал',
  'Кроссовер или SUV',
  'Минивэн',
  'Пикап',
  'Коммерческий транспорт',
  'Другой класс',
];

// Backs `business_size` — number of vehicles.
export const BUSINESS_SIZE_OPTIONS = [
  'Один автомобиль',
  '2–5 автомобилей',
  '6–20 автомобилей',
  'Более 20 автомобилей',
];

// Backs `requester_role` — who is making the request.
export const REQUESTER_ROLE_OPTIONS = [
  'Владелец автомобиля',
  'Доверенное лицо',
  'Представитель компании',
  'Управляющий автопарком',
  'Представитель автосалона',
  'Другое',
];

// Backs `task_scope` — service format.
export const TASK_SCOPE_OPTIONS = [
  'Разовая услуга',
  'Комплексное обслуживание',
  'Регулярный уход',
  'Обслуживание автопарка',
  'Нужна рекомендация специалиста',
];

// Backs `task_type` — type of request.
export const TASK_TYPE_OPTIONS = [
  'Плановый уход',
  'Восстановление внешнего вида',
  'Защита кузова или салона',
  'Подготовка к продаже',
  'Подготовка к сезону',
  'Диагностика или ремонт',
  'Консультация',
  'Другое',
];

// Backs `deadline` — desired appointment timing.
export const DEADLINE_OPTIONS = [
  'Как можно скорее',
  'В течение 3 дней',
  'В течение недели',
  'В течение 2 недель',
  'В течение месяца',
  'Дата не принципиальна',
];

export const PREFERRED_CONTACT_METHOD_OPTIONS = ['Телефон', 'Email', 'Telegram', 'WhatsApp'];

export const PREFERRED_CONTACT_TIME_OPTIONS = [
  'Утро (9:00–12:00)',
  'День (12:00–17:00)',
  'Вечер (17:00–21:00)',
  'В любое время',
];
