"""Unit tests for the pure application-priority scoring service.

No database or network access required - these always run, including when
TEST_DATABASE_URL is not set. score_application() only reads attributes off
its input, so a SimpleNamespace stands in for an Application/ApplicationRead
without needing SQLAlchemy or a real request.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.application_scoring import (
    ApplicationScore,
    _normalize,
    score_application,
)

_MARKER_FREE_TEXT = "Клиент интересуется услугами детейлинга для автомобиля."


def _make_application(**overrides) -> SimpleNamespace:
    base = dict(
        # Personal-identity fields are included to prove scoring ignores
        # them (see test_scoring_ignores_personal_identity_fields below);
        # they are not part of ScorableApplication's required shape.
        first_name="Ivan",
        last_name="Petrov",
        middle_name=None,
        contact_data="+7 900 000-00-00",
        business_niche="Личный автомобиль",
        company_size="Седан или универсал",
        business_info=_MARKER_FREE_TEXT,
        task_scope="Разовая услуга",
        requester_role="Владелец автомобиля",
        business_size="Один автомобиль",
        need_scope=_MARKER_FREE_TEXT,
        deadline="Дата не принципиальна",
        task_type="Плановый уход",
        interested_product="Мойка",
        budget=Decimal("5000"),
        preferred_contact_method="Телефон",
        preferred_contact_time="В любое время",
        comment=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _reason_points(score: ApplicationScore) -> dict[str, int]:
    return {reason.code: reason.points for reason in score.reasons}


# --- _normalize -----------------------------------------------------------


def test_normalize_strips_and_casefolds():
    assert _normalize("  Как Можно Скорее  ") == "как можно скорее"


def test_normalize_handles_none_without_raising():
    assert _normalize(None) == ""


def test_normalize_handles_empty_string():
    assert _normalize("") == ""
    assert _normalize("   ") == ""


# --- Explicitly hot / medium / low applications ----------------------------


def test_explicitly_hot_application():
    app = _make_application(
        deadline="Как можно скорее",
        budget=Decimal("100000"),
        business_size="Более 20 автомобилей",
        task_scope="Обслуживание автопарка",
        task_type="Диагностика или ремонт",
        business_niche="Автопарк компании",
        requester_role="Управляющий автопарком",
        business_info="Авария, нужен эвакуатор",
        need_scope=_MARKER_FREE_TEXT,
        comment="Пожалуйста, свяжитесь сегодня",
    )
    score = score_application(app)

    assert score.raw_score == 25 + 20 + 18 + 18 + 15 + 12 + 8 + 10 + 5
    assert score.score == 100  # capped
    assert score.level == "hot"
    assert score.label == "Горячая"
    assert score.recommended_action == "Связаться в течение часа"
    assert score.recommended_team == "Менеджер автопарков"
    assert score.requires_personal_manager is True
    # Cap must not hide reasons: every rule that fired is still listed.
    assert sum(reason.points for reason in score.reasons) == score.raw_score


def test_explicitly_medium_application():
    app = _make_application(
        deadline="В течение 3 дней",
        budget=Decimal("30000"),
        business_size="6–20 автомобилей",
        task_scope="Комплексное обслуживание",
        task_type="Защита кузова или салона",
        business_niche="Личный автомобиль",
        requester_role="Доверенное лицо",
    )
    score = score_application(app)

    assert score.raw_score == 20 + 15 + 12 + 10 + 7 + 5  # + completeness
    assert score.score == 69
    assert score.level == "medium"
    assert score.label == "Средняя"
    assert score.recommended_action == "Связаться сегодня"


def test_explicitly_low_application():
    app = _make_application(
        deadline="В течение 3 дней",
        budget=Decimal("18000"),
        task_scope="Разовая услуга",
        task_type="Консультация",
        requester_role="Доверенное лицо",
    )
    score = score_application(app)

    assert score.raw_score == 20 + 10 + 3 + 1 + 5  # + completeness
    assert score.score == 39
    assert score.level == "low"
    assert score.label == "Низкая"
    assert score.recommended_action == "Ответить в стандартном порядке"


# --- Priority level boundaries ---------------------------------------------


def test_priority_level_boundary_exactly_70_is_hot():
    app = _make_application(
        deadline="В течение 3 дней",  # 20
        budget=Decimal("60000"),  # 20
        business_size="6–20 автомобилей",  # 12
        task_scope="Разовая услуга",  # 3
        task_type="Диагностика или ремонт",  # 15
        requester_role="Доверенное лицо",  # 0 - not in the point table
        need_scope="",  # breaks completeness so total lands exactly on 70
    )
    score = score_application(app)
    assert score.raw_score == 70
    assert score.score == 70
    assert score.level == "hot"
    assert score.label == "Горячая"


def test_priority_level_boundary_69_is_medium():
    app = _make_application(
        deadline="В течение 3 дней",  # 20
        budget=Decimal("30000"),  # 15
        business_size="6–20 автомобилей",  # 12
        task_scope="Комплексное обслуживание",  # 10
        task_type="Защита кузова или салона",  # 7
        requester_role="Доверенное лицо",  # 0 - not in the point table
    )
    score = score_application(app)
    assert score.raw_score == 69
    assert score.score == 69
    assert score.level == "medium"


def test_priority_level_boundary_40_is_medium():
    app = _make_application(
        deadline="В течение 3 дней",  # 20
        budget=Decimal("18000"),  # 10
        task_scope="Разовая услуга",  # 3
        task_type="Другое",  # 0
        requester_role="Владелец автомобиля",  # 2
    )
    score = score_application(app)
    assert score.raw_score == 40
    assert score.score == 40
    assert score.level == "medium"


def test_priority_level_boundary_39_is_low():
    app = _make_application(
        deadline="В течение 3 дней",  # 20
        budget=Decimal("18000"),  # 10
        task_scope="Разовая услуга",  # 3
        task_type="Консультация",  # 1
        requester_role="Доверенное лицо",  # 0
    )
    score = score_application(app)
    assert score.raw_score == 39
    assert score.score == 39
    assert score.level == "low"


# --- Individual rule categories --------------------------------------------


def test_urgent_deadline_awards_expected_points():
    app = _make_application(deadline="Как можно скорее")
    score = score_application(app)
    assert _reason_points(score)["deadline"] == 25


def test_high_budget_awards_expected_points():
    app = _make_application(budget=Decimal("75000"))
    score = score_application(app)
    assert _reason_points(score)["budget"] == 20


def test_fleet_business_niche_and_scope_award_expected_points_and_team():
    app = _make_application(
        business_niche="Автопарк компании",
        task_scope="Обслуживание автопарка",
        business_size="Более 20 автомобилей",
    )
    score = score_application(app)
    points = _reason_points(score)
    assert points["business_niche"] == 12
    assert points["task_scope"] == 18
    assert points["business_size"] == 18
    assert score.recommended_team == "Менеджер автопарков"
    assert score.requires_personal_manager is True


def test_regular_care_task_scope_awards_expected_points():
    app = _make_application(task_scope="Регулярный уход")
    score = score_application(app)
    assert _reason_points(score)["task_scope"] == 12


def test_diagnostics_task_type_awards_expected_points_and_recommends_service_consultant():
    app = _make_application(
        task_type="Диагностика или ремонт",
        business_niche="Личный автомобиль",
        requester_role="Владелец автомобиля",
        business_size="Один автомобиль",
        task_scope="Разовая услуга",
    )
    score = score_application(app)
    assert _reason_points(score)["task_type"] == 15
    assert score.recommended_team == "Сервисный консультант"


# --- Text urgency markers ---------------------------------------------------


def test_urgency_text_marker_detected_in_business_info():
    app = _make_application(business_info="Машина не заводится, помогите", need_scope="")
    score = score_application(app)
    assert _reason_points(score)["urgency_text_marker"] == 10


def test_urgency_text_marker_detected_in_comment_case_insensitive():
    app = _make_application(comment="СРОЧНО нужна помощь")
    score = score_application(app)
    assert _reason_points(score)["urgency_text_marker"] == 10


def test_urgency_text_marker_not_awarded_when_absent():
    app = _make_application()  # marker-free text by default
    score = score_application(app)
    assert "urgency_text_marker" not in _reason_points(score)


def test_urgency_marker_awarded_only_once_for_multiple_hits():
    app = _make_application(
        business_info="Авария! Нужен эвакуатор срочно, машина не заводится",
        need_scope="Перед продажей и перед поездкой нужна диагностика сегодня",
        comment="Завтра тоже подходит",
    )
    score = score_application(app)
    urgency_reasons = [r for r in score.reasons if r.code == "urgency_text_marker"]
    assert len(urgency_reasons) == 1
    assert urgency_reasons[0].points == 10


# --- Urgency marker: negated "срочно" must not count (audit fix 1) --------


def test_nesrochno_fused_word_is_not_a_marker():
    app = _make_application(business_info="Это несрочно, можно подождать", need_scope="")
    score = score_application(app)
    assert "urgency_text_marker" not in _reason_points(score)


def test_ne_srochno_two_words_is_not_a_marker():
    app = _make_application(business_info="Это не срочно, можно подождать", need_scope="")
    score = score_application(app)
    assert "urgency_text_marker" not in _reason_points(score)


def test_ne_ochen_srochno_is_not_a_marker():
    app = _make_application(business_info="Это не очень срочно, но было бы неплохо", need_scope="")
    score = score_application(app)
    assert "urgency_text_marker" not in _reason_points(score)


def test_uppercase_srochno_is_a_marker():
    app = _make_application(business_info="СРОЧНО нужна помощь", need_scope="")
    score = score_application(app)
    assert _reason_points(score)["urgency_text_marker"] == 10


def test_multiple_real_markers_still_award_bonus_only_once():
    app = _make_application(
        business_info="Авария, машина не заводится",
        need_scope="Нужен эвакуатор сегодня",
        comment="Срочно!",
    )
    score = score_application(app)
    urgency_reasons = [r for r in score.reasons if r.code == "urgency_text_marker"]
    assert len(urgency_reasons) == 1
    assert urgency_reasons[0].points == 10


# --- Urgency marker: no double-counting "перед продажей" (audit fix 2) ----


def test_pre_sale_task_type_with_only_pre_sale_text_gets_no_separate_urgency_bonus():
    app = _make_application(
        task_type="Подготовка к продаже",
        business_info="Хочу подготовить авто перед продажей",
        need_scope="",
    )
    score = score_application(app)
    points = _reason_points(score)
    assert points["task_type"] == 8
    assert "urgency_text_marker" not in points
    assert sum(r.points for r in score.reasons) == score.raw_score


def test_other_task_type_with_pre_sale_text_still_gets_urgency_bonus():
    app = _make_application(
        task_type="Восстановление внешнего вида",
        business_info="Хочу подготовить авто перед продажей",
        need_scope="",
    )
    score = score_application(app)
    points = _reason_points(score)
    assert points["urgency_text_marker"] == 10
    assert sum(r.points for r in score.reasons) == score.raw_score


def test_pre_sale_task_type_with_independent_marker_still_gets_urgency_bonus():
    app = _make_application(
        task_type="Подготовка к продаже",
        business_info="Перед продажей нужно сделать всё срочно",
        need_scope="",
    )
    score = score_application(app)
    points = _reason_points(score)
    assert points["task_type"] == 8
    assert points["urgency_text_marker"] == 10
    assert sum(r.points for r in score.reasons) == score.raw_score


# --- Legacy / unknown values -------------------------------------------------


def test_unknown_and_legacy_values_do_not_raise_and_score_zero():
    app = _make_application(
        deadline="Совершенно новый неизвестный вариант",
        business_size="Индивидуальный предприниматель",  # pre-AUREL legacy value
        task_scope="Пробный проект",  # pre-AUREL legacy value
        task_type="Разработка с нуля",  # pre-AUREL legacy value, must not score high
        business_niche="Другое",
        requester_role="Сотрудник",  # pre-AUREL legacy value
    )
    score = score_application(app)  # must not raise

    points = _reason_points(score)
    for code in ("deadline", "business_size", "task_scope", "task_type", "business_niche", "requester_role"):
        assert code not in points


def test_legacy_deadline_aliases_are_supported():
    legacy = score_application(_make_application(deadline="Срочно (1–3 дня)"))
    current = score_application(_make_application(deadline="В течение 3 дней"))
    assert _reason_points(legacy)["deadline"] == _reason_points(current)["deadline"] == 20

    legacy_flexible = score_application(_make_application(deadline="Гибкий срок"))
    assert "deadline" not in _reason_points(legacy_flexible)


def test_fully_empty_application_scores_zero_without_raising():
    app = _make_application(
        deadline="",
        budget=Decimal("0"),
        business_size="",
        task_scope="",
        task_type="",
        business_niche="",
        requester_role="",
        business_info="",
        need_scope="",
        comment="",
        contact_data="",
        preferred_contact_method="",
        preferred_contact_time="",
    )
    score = score_application(app)
    assert score.raw_score == 0
    assert score.score == 0
    assert score.reasons == ()
    assert score.level == "low"
    assert score.requires_personal_manager is False


# --- Case / whitespace insensitivity ----------------------------------------


def test_case_and_whitespace_variations_score_identically():
    canonical = score_application(_make_application(deadline="Как можно скорее"))
    padded_upper = score_application(_make_application(deadline="  КАК МОЖНО СКОРЕЕ  "))
    assert canonical.score == padded_upper.score
    assert _reason_points(canonical) == _reason_points(padded_upper)


# --- None / Decimal edge cases ----------------------------------------------


def test_comment_none_does_not_raise():
    app = _make_application(comment=None)
    score = score_application(app)  # must not raise
    assert isinstance(score.score, int)


def test_decimal_budget_boundaries_are_exact_no_float_drift():
    assert _reason_points(score_application(_make_application(budget=Decimal("50000.00"))))["budget"] == 20
    assert _reason_points(score_application(_make_application(budget=Decimal("49999.99"))))["budget"] == 15
    assert _reason_points(score_application(_make_application(budget=Decimal("25000.00"))))["budget"] == 15
    assert _reason_points(score_application(_make_application(budget=Decimal("15000.00"))))["budget"] == 10
    assert _reason_points(score_application(_make_application(budget=Decimal("8000.00"))))["budget"] == 5
    assert "budget" not in _reason_points(score_application(_make_application(budget=Decimal("7999.99"))))


@pytest.mark.parametrize(
    "budget, expected_points",
    [
        (Decimal("7999.99"), 0),
        (Decimal("8000"), 5),
        (Decimal("14999.99"), 5),
        (Decimal("15000"), 10),
        (Decimal("24999.99"), 10),
        (Decimal("25000"), 15),
        (Decimal("49999.99"), 15),
        (Decimal("50000"), 20),
    ],
)
def test_budget_tier_boundaries_are_exact(budget, expected_points):
    score = score_application(_make_application(budget=budget))
    points = _reason_points(score)
    if expected_points == 0:
        assert "budget" not in points
    else:
        assert points["budget"] == expected_points


# --- Score bounds and cap ----------------------------------------------------


def test_score_is_always_within_0_and_100():
    deadlines = ["Как можно скорее", "Дата не принципиальна", "неизвестное значение"]
    budgets = [Decimal("0"), Decimal("15000"), Decimal("1000000")]
    business_sizes = ["Один автомобиль", "Более 20 автомобилей"]
    task_scopes = ["Разовая услуга", "Обслуживание автопарка"]

    for deadline in deadlines:
        for budget in budgets:
            for business_size in business_sizes:
                for task_scope in task_scopes:
                    app = _make_application(
                        deadline=deadline,
                        budget=budget,
                        business_size=business_size,
                        task_scope=task_scope,
                        business_niche="Автопарк компании",
                        task_type="Диагностика или ремонт",
                        requester_role="Управляющий автопарком",
                        business_info="Авария эвакуатор срочно",
                    )
                    score = score_application(app)
                    assert 0 <= score.score <= 100


def test_cap_at_100_does_not_hide_reasons():
    app = _make_application(
        deadline="Как можно скорее",
        budget=Decimal("100000"),
        business_size="Более 20 автомобилей",
        task_scope="Обслуживание автопарка",
        task_type="Диагностика или ремонт",
        business_niche="Автопарк компании",
        requester_role="Управляющий автопарком",
        business_info="Авария",
    )
    score = score_application(app)
    assert score.raw_score > 100
    assert score.score == 100
    assert len(score.reasons) == 9  # every rule that fired is still reported
    assert sum(r.points for r in score.reasons) == score.raw_score


def test_no_empty_reasons_are_added_for_zero_point_matches():
    app = _make_application(
        business_size="Один автомобиль",  # matches table but worth 0
        deadline="Дата не принципиальна",  # matches table but worth 0
    )
    score = score_application(app)
    points = _reason_points(score)
    assert "business_size" not in points
    assert "deadline" not in points


# --- Determinism -------------------------------------------------------------


def test_same_input_gives_same_result():
    app = _make_application(
        deadline="В течение недели", budget=Decimal("12345.67"), business_niche="Служебный автомобиль"
    )
    first = score_application(app)
    second = score_application(app)
    assert first == second


# --- requires_personal_manager ------------------------------------------------


def test_requires_personal_manager_true_for_fleet_scope_even_at_low_score():
    app = _make_application(task_scope="Обслуживание автопарка", need_scope="")
    score = score_application(app)
    assert score.score < 70
    assert score.requires_personal_manager is True


def test_requires_personal_manager_true_for_fleet_size_even_at_low_score():
    app = _make_application(business_size="Более 20 автомобилей", need_scope="")
    score = score_application(app)
    assert score.score < 70
    assert score.requires_personal_manager is True


def test_requires_personal_manager_true_for_regular_care_corporate_client():
    app = _make_application(
        task_scope="Регулярный уход", business_niche="Служебный автомобиль", need_scope=""
    )
    score = score_application(app)
    assert score.score < 70
    assert score.requires_personal_manager is True


def test_requires_personal_manager_false_for_regular_care_without_corporate_niche():
    app = _make_application(
        task_scope="Регулярный уход", business_niche="Личный автомобиль", need_scope=""
    )
    score = score_application(app)
    assert score.requires_personal_manager is False


def test_requires_personal_manager_false_for_plain_private_low_score():
    app = _make_application()
    score = score_application(app)
    assert score.requires_personal_manager is False


# --- Recommended team priority ------------------------------------------------


def test_fleet_team_takes_priority_over_corporate_niche():
    app = _make_application(business_niche="Автосалон или дилер", business_size="Более 20 автомобилей")
    score = score_application(app)
    assert score.recommended_team == "Менеджер автопарков"


def test_corporate_team_for_dealer_niche_without_fleet_signals():
    app = _make_application(
        business_niche="Автосалон или дилер",
        business_size="Один автомобиль",
        task_scope="Разовая услуга",
    )
    score = score_application(app)
    assert score.recommended_team == "Менеджер корпоративных клиентов"


def test_corporate_team_for_company_representative_role():
    app = _make_application(
        requester_role="Представитель компании",
        business_niche="Личный автомобиль",
        business_size="Один автомобиль",
        task_scope="Разовая услуга",
    )
    score = score_application(app)
    assert score.recommended_team == "Менеджер корпоративных клиентов"


def test_default_team_is_detailing_specialist():
    app = _make_application()
    score = score_application(app)
    assert score.recommended_team == "Специалист детейлинга"


# --- Recommended team: служебный автомобиль routes to corporate (fix 3) ---


def test_service_vehicle_niche_routes_to_corporate_manager():
    app = _make_application(
        business_niche="Служебный автомобиль",
        business_size="Один автомобиль",
        task_scope="Разовая услуга",
    )
    score = score_application(app)
    assert score.recommended_team == "Менеджер корпоративных клиентов"


def test_service_vehicle_with_diagnostics_still_routes_to_corporate_manager():
    # Corporate niche must outrank the service-consultant task_type branch.
    app = _make_application(
        business_niche="Служебный автомобиль",
        business_size="Один автомобиль",
        task_scope="Разовая услуга",
        task_type="Диагностика или ремонт",
    )
    score = score_application(app)
    assert score.recommended_team == "Менеджер корпоративных клиентов"


def test_fleet_scope_still_outranks_service_vehicle_niche():
    app = _make_application(
        business_niche="Служебный автомобиль",
        task_scope="Обслуживание автопарка",
    )
    score = score_application(app)
    assert score.recommended_team == "Менеджер автопарков"


# --- Fairness: private customers never get negative points -------------------


def test_private_single_vehicle_owner_gets_no_negative_points():
    app = _make_application(
        business_niche="Личный автомобиль",
        business_size="Один автомобиль",
        task_scope="Разовая услуга",
        requester_role="Владелец автомобиля",
        deadline="Дата не принципиальна",
        budget=Decimal("0"),
        task_type="Другое",
    )
    score = score_application(app)
    assert score.raw_score >= 0
    assert all(reason.points > 0 for reason in score.reasons)


# --- No analysis of sensitive/personal-identity characteristics --------------


def test_scoring_ignores_personal_identity_fields():
    shared = dict(
        deadline="В течение недели",
        budget=Decimal("20000"),
        business_niche="Служебный автомобиль",
        task_scope="Регулярный уход",
        task_type="Плановый уход",
        requester_role="Владелец автомобиля",
        business_size="2–5 автомобилей",
    )
    app_a = _make_application(
        first_name="Anna", last_name="Ivanova", contact_data="anna@example.com", **shared
    )
    app_b = _make_application(
        first_name="Boris", last_name="Sidorov", contact_data="+79990000000", **shared
    )
    assert score_application(app_a) == score_application(app_b)


# --- Completeness ------------------------------------------------------------


def test_completeness_bonus_awarded_once_when_all_required_fields_present():
    app = _make_application()
    score = score_application(app)
    assert _reason_points(score)["completeness"] == 5


def test_completeness_bonus_not_awarded_when_a_required_field_is_blank():
    app = _make_application(need_scope="   ")
    score = score_application(app)
    assert "completeness" not in _reason_points(score)


@pytest.mark.parametrize(
    "field_name, blank_value",
    [
        ("first_name", ""),
        ("last_name", "   "),
        ("contact_data", ""),
        ("business_niche", "   "),
        ("company_size", ""),
        ("business_info", "   "),
        ("task_scope", ""),
        ("requester_role", "   "),
        ("business_size", ""),
        ("need_scope", "   "),
        ("deadline", ""),
        ("task_type", "   "),
        ("interested_product", ""),
        ("budget", None),
        ("preferred_contact_method", ""),
        ("preferred_contact_time", "   "),
    ],
)
def test_completeness_bonus_requires_every_required_business_field(field_name, blank_value):
    app = _make_application(**{field_name: blank_value})
    score = score_application(app)  # must not raise even for budget=None
    assert "completeness" not in _reason_points(score)


def test_completeness_bonus_ignores_optional_middle_name_and_comment():
    app = _make_application(middle_name=None, comment=None)
    score = score_application(app)
    assert _reason_points(score)["completeness"] == 5


def test_completeness_bonus_treats_zero_budget_as_present_not_missing():
    app = _make_application(budget=Decimal("0"))
    score = score_application(app)
    assert _reason_points(score)["completeness"] == 5
