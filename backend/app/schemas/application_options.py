"""Server-side authoritative option sets for the Application form's
"enum-like" fields.

Mirrors frontend/src/options.ts exactly (values, order is irrelevant but
kept identical for easy diffing) - the frontend previously was the only
place these choices were enforced (a direct HTTP client could send any
string for business_niche/company_size/etc). Kept in a separate module
(not inline in schemas/application.py) so it is easy to compare against
frontend/src/options.ts by hand when either side changes, and so
tests/test_application_options_sync.py can parse both files and assert
they still agree.

If frontend/src/options.ts ever adds/removes/renames an option, this file
must be updated in the same change, or legitimate frontend submissions
using the new option will be rejected by the backend as an invalid
categorical value.
"""

from typing import Literal

# Backs `business_niche` - how the vehicle is used.
BusinessNiche = Literal[
    "Личный автомобиль",
    "Семейный автомобиль",
    "Служебный автомобиль",
    "Такси или коммерческие перевозки",
    "Автопарк компании",
    "Автосалон или дилер",
    "Другое",
]

# Backs `company_size` - vehicle class.
CompanySize = Literal[
    "Компактный автомобиль",
    "Седан или универсал",
    "Кроссовер или SUV",
    "Минивэн",
    "Пикап",
    "Коммерческий транспорт",
    "Другой класс",
]

# Backs `business_size` - number of vehicles.
BusinessSize = Literal[
    "Один автомобиль",
    "2–5 автомобилей",
    "6–20 автомобилей",
    "Более 20 автомобилей",
]

# Backs `requester_role` - who is making the request.
RequesterRole = Literal[
    "Владелец автомобиля",
    "Доверенное лицо",
    "Представитель компании",
    "Управляющий автопарком",
    "Представитель автосалона",
    "Другое",
]

# Backs `task_scope` - service format.
TaskScope = Literal[
    "Разовая услуга",
    "Комплексное обслуживание",
    "Регулярный уход",
    "Обслуживание автопарка",
    "Нужна рекомендация специалиста",
]

# Backs `task_type` - type of request.
TaskType = Literal[
    "Плановый уход",
    "Восстановление внешнего вида",
    "Защита кузова или салона",
    "Подготовка к продаже",
    "Подготовка к сезону",
    "Диагностика или ремонт",
    "Консультация",
    "Другое",
]

# Backs `deadline` - desired appointment timing.
Deadline = Literal[
    "Как можно скорее",
    "В течение 3 дней",
    "В течение недели",
    "В течение 2 недель",
    "В течение месяца",
    "Дата не принципиальна",
]

PreferredContactMethod = Literal["Телефон", "Email", "Telegram", "WhatsApp"]

PreferredContactTime = Literal[
    "Утро (9:00–12:00)",
    "День (12:00–17:00)",
    "Вечер (17:00–21:00)",
    "В любое время",
]
