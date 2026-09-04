"""Deterministic, explainable priority scoring for client applications.

This module is intentionally pure:

- no HTTP / FastAPI imports, no route-handler dependencies;
- no database access, no commits, no mutation of the input object;
- no reads of the current wall-clock time;
- same input always produces the same output.

Scoring is a commercial-priority heuristic (how quickly and by whom the
lead should be handled), not a judgement of a person's worth: a private
owner with a single vehicle never receives negative points, they simply
don't match the higher-volume B2B tiers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

PriorityLevel = Literal["hot", "medium", "low"]

_HOT_THRESHOLD = 70
_MEDIUM_THRESHOLD = 40
_MIN_SCORE = 0
_MAX_SCORE = 100


class ScorableApplication(Protocol):
    """Structural shape the scorer needs.

    Satisfied by the Application ORM model, ApplicationRead, or any other
    compatible object/namespace with these attributes.
    """

    first_name: str
    last_name: str
    contact_data: str
    business_niche: str
    company_size: str
    business_info: str
    task_scope: str
    requester_role: str
    business_size: str
    need_scope: str
    deadline: str
    task_type: str
    interested_product: str
    budget: Decimal | None
    preferred_contact_method: str
    preferred_contact_time: str
    comment: str | None


@dataclass(frozen=True)
class ScoringReason:
    code: str
    points: int
    label: str


@dataclass(frozen=True)
class ApplicationScore:
    raw_score: int
    score: int
    level: PriorityLevel
    label: str
    reasons: tuple[ScoringReason, ...]
    recommended_action: str
    recommended_team: str
    requires_personal_manager: bool


def _normalize(value: str | None) -> str:
    """Strip + casefold for safe, case/whitespace-insensitive comparison.

    Never raises: None becomes "", and any string - including unknown,
    legacy or empty values - simply fails to match a rule below (0 points)
    instead of throwing.
    """
    if value is None:
        return ""
    return value.strip().casefold()


# --- Rule tables --------------------------------------------------------
# Keys are already normalized (stripped + casefolded) so lookups only ever
# need to normalize the incoming value, never the table.

_DEADLINE_POINTS: dict[str, int] = {
    "как можно скорее": 25,
    "в течение 3 дней": 20,
    "в течение недели": 12,
    "в течение 2 недель": 6,
    "в течение месяца": 2,
    "дата не принципиальна": 0,
    # Legacy aliases from the pre-AUREL freelance/IT-services form wording
    # (see frontend/src/options.ts history) - kept compatible, not inflated.
    "срочно (1–3 дня)": 20,
    "гибкий срок": 0,
}

_BUSINESS_SIZE_POINTS: dict[str, int] = {
    "более 20 автомобилей": 18,
    "6–20 автомобилей": 12,
    "2–5 автомобилей": 6,
    "один автомобиль": 0,
}

_TASK_SCOPE_POINTS: dict[str, int] = {
    "обслуживание автопарка": 18,
    "регулярный уход": 12,
    "комплексное обслуживание": 10,
    "нужна рекомендация специалиста": 4,
    "разовая услуга": 3,
}

_TASK_TYPE_POINTS: dict[str, int] = {
    "диагностика или ремонт": 15,
    "восстановление внешнего вида": 10,
    "подготовка к продаже": 8,
    "защита кузова или салона": 7,
    "подготовка к сезону": 5,
    "плановый уход": 3,
    "консультация": 1,
    "другое": 0,
}

_BUSINESS_NICHE_POINTS: dict[str, int] = {
    "автопарк компании": 12,
    "такси или коммерческие перевозки": 10,
    "автосалон или дилер": 10,
    "служебный автомобиль": 5,
    "личный автомобиль": 0,
    "семейный автомобиль": 0,
}

_REQUESTER_ROLE_POINTS: dict[str, int] = {
    "управляющий автопарком": 8,
    "представитель компании": 6,
    "представитель автосалона": 6,
    "владелец автомобиля": 2,
}

# Ordered descending by threshold - the first tier the budget clears wins.
_BUDGET_TIERS: tuple[tuple[Decimal, int], ...] = (
    (Decimal("50000"), 20),
    (Decimal("25000"), 15),
    (Decimal("15000"), 10),
    (Decimal("8000"), 5),
)


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    # Word-boundary match for a (possibly multi-word) phrase, so e.g.
    # "авария" doesn't fire from being a substring of some other word, and
    # "срочно" doesn't fire from inside "несрочно" (no boundary exists
    # between fused "не" and "срочно" - both are \w characters).
    words = phrase.split()
    return re.compile(r"\b" + r"\s+".join(re.escape(word) for word in words) + r"\b")


# "срочно" is handled separately (see _SROCHNO_RE / _SROCHNO_NEGATED_RE
# below) because it alone needs negation-awareness ("не срочно" must not
# count); the other phrases have no such false-positive risk.
_URGENCY_MARKER_PHRASES: tuple[str, ...] = (
    "как можно скорее",
    "сегодня",
    "завтра",
    "авария",
    "не заводится",
    "эвакуатор",
    "перед продажей",
    "перед поездкой",
)
_URGENCY_MARKER_PATTERNS: dict[str, re.Pattern[str]] = {
    phrase: _phrase_regex(phrase) for phrase in _URGENCY_MARKER_PHRASES
}

_SROCHNO_KEY = "срочно"
_SROCHNO_RE = re.compile(r"\bсрочно\b")
# Strips "не срочно" / "не очень срочно" (one optional intensifier word
# between the negation and "срочно") before the positive check runs, so
# neither counts as an urgency signal. "несрочно" (no space) already never
# matches _SROCHNO_RE in the first place - see _phrase_regex's docstring
# note above.
_SROCHNO_NEGATED_RE = re.compile(r"\bне\s+(?:\S+\s+)?срочно\b")

_PRE_SALE_MARKER = "перед продажей"
_PRE_SALE_TASK_TYPE = "подготовка к продаже"


def _matched_urgency_markers(combined: str) -> frozenset[str]:
    matched = {
        phrase for phrase, pattern in _URGENCY_MARKER_PATTERNS.items() if pattern.search(combined)
    }
    without_negated_srochno = _SROCHNO_NEGATED_RE.sub(" ", combined)
    if _SROCHNO_RE.search(without_negated_srochno):
        matched.add(_SROCHNO_KEY)
    return frozenset(matched)


_FLEET_BUSINESS_SIZES = frozenset({"6–20 автомобилей", "более 20 автомобилей"})
_CORPORATE_NICHES = frozenset(
    {
        "автопарк компании",
        "такси или коммерческие перевозки",
        "автосалон или дилер",
        "служебный автомобиль",
    }
)
_CORPORATE_REQUESTER_ROLES = frozenset({"представитель компании", "представитель автосалона"})

_RECOMMENDED_ACTIONS: dict[PriorityLevel, str] = {
    "hot": "Связаться в течение часа",
    "medium": "Связаться сегодня",
    "low": "Ответить в стандартном порядке",
}

_PRIORITY_LABELS: dict[PriorityLevel, str] = {
    "hot": "Горячая",
    "medium": "Средняя",
    "low": "Низкая",
}


def _as_decimal(budget: Decimal | None) -> Decimal | None:
    # Defensive only: callers are expected to pass a Decimal already (the
    # Application model and ApplicationRead both type budget as Decimal,
    # never None). None is tolerated here purely so a compatible
    # object/fixture missing budget doesn't raise - it just scores 0 for
    # this rule instead. Converting via str avoids float rounding
    # artifacts if a compatible structure ever hands in something else.
    if budget is None:
        return None
    return budget if isinstance(budget, Decimal) else Decimal(str(budget))


def _table_reason(code: str, label_prefix: str, raw_value: str, points_table: dict[str, int]) -> ScoringReason | None:
    points = points_table.get(_normalize(raw_value), 0)
    if points <= 0:
        return None
    return ScoringReason(code=code, points=points, label=f"{label_prefix}: {raw_value.strip()}")


def _deadline_reason(deadline: str) -> ScoringReason | None:
    return _table_reason("deadline", "Срок записи", deadline, _DEADLINE_POINTS)


def _budget_reason(budget: Decimal | None) -> ScoringReason | None:
    value = _as_decimal(budget)
    if value is None:
        return None
    for threshold, points in _BUDGET_TIERS:
        if value >= threshold:
            formatted = f"{int(threshold):,}".replace(",", " ")
            return ScoringReason(code="budget", points=points, label=f"Бюджет от {formatted} ₽")
    return None


def _business_size_reason(business_size: str) -> ScoringReason | None:
    return _table_reason(
        "business_size", "Количество автомобилей", business_size, _BUSINESS_SIZE_POINTS
    )


def _task_scope_reason(task_scope: str) -> ScoringReason | None:
    return _table_reason("task_scope", "Формат обслуживания", task_scope, _TASK_SCOPE_POINTS)


def _task_type_reason(task_type: str) -> ScoringReason | None:
    return _table_reason("task_type", "Тип обращения", task_type, _TASK_TYPE_POINTS)


def _business_niche_reason(business_niche: str) -> ScoringReason | None:
    return _table_reason(
        "business_niche", "Использование автомобиля", business_niche, _BUSINESS_NICHE_POINTS
    )


def _requester_role_reason(requester_role: str) -> ScoringReason | None:
    return _table_reason("requester_role", "Обращается", requester_role, _REQUESTER_ROLE_POINTS)


def _urgency_marker_reason(
    business_info: str, need_scope: str, comment: str | None, task_type: str
) -> ScoringReason | None:
    combined = " ".join(_normalize(text) for text in (business_info, need_scope, comment))
    if not combined:
        return None

    matched = set(_matched_urgency_markers(combined))
    if _normalize(task_type) == _PRE_SALE_TASK_TYPE:
        # "Подготовка к продаже" already earns +8 via the task_type rule -
        # don't also pay the text bonus for the same circumstance. Only
        # drop this one marker though: if an independent signal (e.g.
        # "срочно") is also present, the text bonus still applies to that.
        matched.discard(_PRE_SALE_MARKER)

    if not matched:
        return None
    return ScoringReason(
        code="urgency_text_marker",
        points=10,
        label="В тексте заявки есть признак срочности",
    )


# NOT NULL business fields on the applications table (see
# app/models/application.py) - id/created_at/updated_at are record
# metadata, not applicant input, and middle_name/comment are nullable by
# design, so none of those five count toward completeness.
_REQUIRED_COMPLETENESS_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "contact_data",
    "business_niche",
    "company_size",
    "business_info",
    "task_scope",
    "requester_role",
    "business_size",
    "need_scope",
    "deadline",
    "task_type",
    "interested_product",
    "budget",
    "preferred_contact_method",
    "preferred_contact_time",
)


def _is_present(value: object) -> bool:
    """A field counts as present if it's a non-blank-after-strip string,
    or any non-None non-string value - e.g. budget=Decimal("0") is
    present; only budget=None counts as missing."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _completeness_reason(application: ScorableApplication) -> ScoringReason | None:
    if all(_is_present(getattr(application, field)) for field in _REQUIRED_COMPLETENESS_FIELDS):
        return ScoringReason(
            code="completeness",
            points=5,
            label="Заявка заполнена полностью",
        )
    return None


def _priority_level(score: int) -> PriorityLevel:
    if score >= _HOT_THRESHOLD:
        return "hot"
    if score >= _MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def priority_score_bounds(level: PriorityLevel) -> tuple[int, int]:
    """Inclusive [min, max] priority_score range for `level` - the single
    source of truth for the score bands _priority_level() above already
    encodes, so a priority-level filter query (see
    app/crud/application.py::get_prioritized_applications_page) can never
    drift out of sync with what the score->level mapping (and thus the API/
    frontend badge for that same row) actually says."""
    if level == "hot":
        return (_HOT_THRESHOLD, _MAX_SCORE)
    if level == "medium":
        return (_MEDIUM_THRESHOLD, _HOT_THRESHOLD - 1)
    return (_MIN_SCORE, _MEDIUM_THRESHOLD - 1)


def _recommend_team(*, fleet_scope: bool, fleet_size: bool, niche: str, role: str, task_type: str) -> str:
    # Fleet has the highest priority: it must win even when the niche is
    # also one of _CORPORATE_NICHES (e.g. "автопарк компании" itself is a
    # corporate niche too) or the task_type would otherwise route to the
    # service consultant branch below.
    if fleet_scope or fleet_size or niche == "автопарк компании":
        return "Менеджер автопарков"
    if niche in _CORPORATE_NICHES or role in _CORPORATE_REQUESTER_ROLES:
        return "Менеджер корпоративных клиентов"
    if task_type == "диагностика или ремонт":
        return "Сервисный консультант"
    return "Специалист детейлинга"


def score_application(application: ScorableApplication) -> ApplicationScore:
    """Compute a deterministic 0..100 priority score with explanations.

    Pure function: reads only the given object's attributes, performs no
    I/O, and never mutates its input.
    """
    reasons: list[ScoringReason] = [
        reason
        for reason in (
            _deadline_reason(application.deadline),
            _budget_reason(application.budget),
            _business_size_reason(application.business_size),
            _task_scope_reason(application.task_scope),
            _task_type_reason(application.task_type),
            _business_niche_reason(application.business_niche),
            _requester_role_reason(application.requester_role),
            _urgency_marker_reason(
                application.business_info,
                application.need_scope,
                application.comment,
                application.task_type,
            ),
            _completeness_reason(application),
        )
        if reason is not None
    ]

    raw_score = sum(reason.points for reason in reasons)
    score = max(_MIN_SCORE, min(_MAX_SCORE, raw_score))
    level = _priority_level(score)

    niche = _normalize(application.business_niche)
    role = _normalize(application.requester_role)
    task_type = _normalize(application.task_type)
    fleet_scope = _normalize(application.task_scope) == "обслуживание автопарка"
    fleet_size = _normalize(application.business_size) in _FLEET_BUSINESS_SIZES
    regular_care = _normalize(application.task_scope) == "регулярный уход"
    corporate_niche = niche in _CORPORATE_NICHES

    requires_personal_manager = (
        score >= _HOT_THRESHOLD
        or fleet_scope
        or fleet_size
        or (regular_care and corporate_niche)
    )

    return ApplicationScore(
        raw_score=raw_score,
        score=score,
        level=level,
        label=_PRIORITY_LABELS[level],
        reasons=tuple(reasons),
        recommended_action=_RECOMMENDED_ACTIONS[level],
        recommended_team=_recommend_team(
            fleet_scope=fleet_scope, fleet_size=fleet_size, niche=niche, role=role, task_type=task_type
        ),
        requires_personal_manager=requires_personal_manager,
    )
