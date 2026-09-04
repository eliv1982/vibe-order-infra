"""Integration tests for GET /api/applications/prioritized against a real
PostgreSQL test database.

Requires TEST_DATABASE_URL, exactly like test_api.py; the whole module is
skipped with an explicit reason if it's unset. The scoring rules themselves
are unit-tested in test_application_scoring.py without a database; this
file focuses on the HTTP/DB wiring - auth, response shape, sort order and
pagination.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.models.application import Application
from tests.db_safety_guard import get_test_database_url
from tests.test_api import _application_payload

TEST_DATABASE_URL = get_test_database_url()

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set - skipping PostgreSQL integration tests",
)


def _insert_application(db_session, created_at: datetime | None = None, **overrides) -> Application:
    """Insert an Application directly via the ORM, bypassing the public API.

    Used only to pin an exact created_at for sort-order tests - the public
    POST endpoint has no way to control that timestamp, and it is
    otherwise driven purely by DB server time within the test's savepoint.
    """
    payload = _application_payload(**overrides)
    payload["budget"] = Decimal(str(payload["budget"]))
    # Bypasses ApplicationCreate/crud.create_application entirely (direct
    # ORM insert), so interested_product - normally derived server-side
    # from the looked-up service (see app/crud/application.py) - must be
    # set explicitly here.
    payload["interested_product"] = "Test Default Service"
    application = Application(**payload)
    if created_at is not None:
        application.created_at = created_at
    db_session.add(application)
    db_session.commit()
    db_session.refresh(application)
    return application


def test_prioritized_requires_token(client):
    response = client.get("/api/applications/prioritized")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_prioritized_rejects_corrupted_token(client):
    response = client.get(
        "/api/applications/prioritized", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def test_prioritized_with_valid_admin_token_returns_200(client, admin_auth_headers):
    client.post("/api/applications", json=_application_payload())
    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    assert response.status_code == 200


def test_prioritized_response_matches_schema(client, admin_auth_headers):
    client.post("/api/applications", json=_application_payload())
    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert set(body.keys()) == {"items", "total", "skip", "limit"}
    assert body["skip"] == 0
    assert body["limit"] == 100
    assert body["total"] >= 1
    assert len(body["items"]) >= 1

    item = body["items"][0]
    assert set(item.keys()) == {
        "application",
        "priority_score",
        "priority_level",
        "priority_label",
        "reasons",
        "recommended_action",
        "recommended_team",
        "requires_personal_manager",
    }
    assert isinstance(item["priority_score"], int)
    assert 0 <= item["priority_score"] <= 100
    assert item["priority_level"] in {"hot", "medium", "low"}
    assert isinstance(item["reasons"], list)
    for reason in item["reasons"]:
        assert set(reason.keys()) == {"code", "points", "label"}
        assert isinstance(reason["points"], int)
    assert isinstance(item["application"]["id"], int)
    assert "password_hash" not in response.text


def test_prioritized_sorts_by_score_descending(client, admin_auth_headers):
    low_id = client.post("/api/applications", json=_application_payload()).json()["id"]
    hot_id = client.post(
        "/api/applications",
        json=_application_payload(
            deadline="Как можно скорее",
            budget="100000.00",
            business_size="Более 20 автомобилей",
            task_scope="Обслуживание автопарка",
            task_type="Диагностика или ремонт",
            business_niche="Автопарк компании",
            requester_role="Управляющий автопарком",
            business_info="Авария, нужен эвакуатор",
        ),
    ).json()["id"]

    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    items = response.json()["items"]
    ids_in_order = [item["application"]["id"] for item in items]

    assert ids_in_order.index(hot_id) < ids_in_order.index(low_id)
    scores = [item["priority_score"] for item in items]
    assert scores == sorted(scores, reverse=True)


def test_prioritized_tie_break_by_older_created_at_first(client, admin_auth_headers, db_session):
    now = datetime.now(timezone.utc)
    # Insertion order (and thus id order) is deliberately the opposite of
    # created_at order, so a passing test proves created_at drives the tie
    # break rather than id happening to already match it.
    newer_but_lower_id = _insert_application(db_session, created_at=now)
    older_but_higher_id = _insert_application(db_session, created_at=now - timedelta(days=1))
    assert newer_but_lower_id.id < older_but_higher_id.id

    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    items = response.json()["items"]
    ids_in_order = [item["application"]["id"] for item in items]

    assert ids_in_order.index(older_but_higher_id.id) < ids_in_order.index(newer_but_lower_id.id)


def test_prioritized_full_tie_break_by_id_ascending(client, admin_auth_headers, db_session):
    same_created_at = datetime.now(timezone.utc)
    first = _insert_application(db_session, created_at=same_created_at)
    second = _insert_application(db_session, created_at=same_created_at)

    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    items = response.json()["items"]
    ids_in_order = [item["application"]["id"] for item in items]

    assert ids_in_order.index(first.id) < ids_in_order.index(second.id)


def test_prioritized_skip_and_limit_apply_after_scoring_and_sorting(client, admin_auth_headers):
    for i in range(5):
        client.post("/api/applications", json=_application_payload(budget=str(1000 * (i + 1))))

    full_response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    full_ids_in_order = [item["application"]["id"] for item in full_response.json()["items"]]

    paged_response = client.get(
        "/api/applications/prioritized", params={"skip": 2, "limit": 2}, headers=admin_auth_headers
    )
    paged_body = paged_response.json()
    assert paged_body["skip"] == 2
    assert paged_body["limit"] == 2
    assert len(paged_body["items"]) == 2

    paged_ids = [item["application"]["id"] for item in paged_body["items"]]
    assert paged_ids == full_ids_in_order[2:4]
    assert paged_body["total"] == full_response.json()["total"]


def test_prioritized_total_matches_full_application_count(client, admin_auth_headers):
    for _ in range(3):
        client.post("/api/applications", json=_application_payload())

    response = client.get(
        "/api/applications/prioritized", params={"limit": 1}, headers=admin_auth_headers
    )
    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] >= 3


@pytest.mark.parametrize("params", [{"skip": -1}, {"limit": 0}, {"limit": 101}])
def test_prioritized_pagination_rejects_invalid_params(client, admin_auth_headers, params):
    response = client.get(
        "/api/applications/prioritized", params=params, headers=admin_auth_headers
    )
    assert response.status_code == 422


def test_prioritized_does_not_500_on_unknown_legacy_values(
    client, admin_auth_headers, db_session
):
    """Stage 1B made business_niche/company_size/deadline/etc. closed
    Literal enums (see app/schemas/application.py), so the public API can no
    longer be used to store an unknown/legacy value for them (see
    tests/test_api.py::test_application_create_rejects_invalid_categorical_value).
    The scoring/listing pipeline must still never 500 on such a value,
    though - e.g. a row inserted directly (bypassing the API) before this
    stage, by a maintenance script, or by a future relaxation of the enum -
    so this now inserts directly via the ORM rather than through POST, and
    keeps proving the defensive property score_application/list-prioritized
    already guarantees for any string (see
    test_application_scoring.py::test_unknown_and_legacy_values_do_not_raise_and_score_zero)."""
    payload = _application_payload(
        deadline="Совершенно неизвестное значение",
        business_size="Индивидуальный предприниматель",
        task_scope="Пробный проект",
        task_type="Разработка с нуля",
        business_niche="Другое",
        requester_role="Сотрудник",
    )
    payload["budget"] = Decimal(str(payload["budget"]))
    payload["interested_product"] = "Test Default Service"
    db_session.add(Application(**payload))
    db_session.commit()

    response = client.get("/api/applications/prioritized", headers=admin_auth_headers)
    assert response.status_code == 200


def test_create_application_stays_public_alongside_prioritized_endpoint(client):
    response = client.post("/api/applications", json=_application_payload())
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Stage 4: real pagination/performance proof.
#
# Previously GET /applications/prioritized loaded every row into Python,
# scored and sorted all of them there, then sliced skip:skip+limit out of
# that in-memory list (see git history of app/routes/applications.py) - a
# correctness/scalability defect that just happened to be invisible at this
# project's small data volumes. Stage 4 moved ordering, OFFSET and LIMIT into
# PostgreSQL itself (see app/models/application.py's priority_score column
# and app/crud/application.py::get_prioritized_applications_page) - the
# tests below insert materially more rows than a single page and prove both
# correctness (no gaps/duplicates across pages, a genuine final partial
# page, preserved score-descending/created_at/id ordering) and, separately,
# that the SQL sent to PostgreSQL is itself bounded by LIMIT - not merely
# that the HTTP response happens to be short.
# ---------------------------------------------------------------------------

# Five distinct scoring profiles (varying deadline/budget/business_size/
# task_scope/task_type/business_niche/requester_role/business_info - see
# app/services/application_scoring.py's rule tables) so 250 inserted
# applications span a real, non-trivial range of priority_score values
# instead of all landing on the same score - a monotonic-order assertion
# over rows that were all tied would prove nothing.
_SCORE_PROFILES: tuple[dict, ...] = (
    {},  # the plain low-scoring default payload
    {"deadline": "В течение недели", "budget": "5000.00"},
    {
        "deadline": "В течение 3 дней",
        "budget": "20000.00",
        "business_size": "2–5 автомобилей",
        "task_type": "Подготовка к продаже",
    },
    {
        "deadline": "Как можно скорее",
        "budget": "60000.00",
        "business_size": "6–20 автомобилей",
        "task_scope": "Регулярный уход",
        "task_type": "Диагностика или ремонт",
        "business_niche": "Такси или коммерческие перевозки",
        "requester_role": "Представитель компании",
    },
    {
        "deadline": "Как можно скорее",
        "budget": "100000.00",
        "business_size": "Более 20 автомобилей",
        "task_scope": "Обслуживание автопарка",
        "task_type": "Диагностика или ремонт",
        "business_niche": "Автопарк компании",
        "requester_role": "Управляющий автопарком",
        "business_info": "Авария, нужен эвакуатор",
    },
)


def _bulk_insert_applications(db_session, count: int) -> list[int]:
    """Insert `count` Application rows directly via the ORM in a single
    commit (fast - avoids `count` separate savepoints/round-trips), cycling
    through _SCORE_PROFILES so the set has real score diversity. Returns the
    inserted ids in insertion order (== id order, since these are the only
    rows this test's isolated db_session transaction ever creates - see
    conftest.py's db_session fixture)."""
    rows = []
    for i in range(count):
        payload = _application_payload(**_SCORE_PROFILES[i % len(_SCORE_PROFILES)])
        payload["budget"] = Decimal(str(payload["budget"]))
        payload["interested_product"] = "Test Default Service"
        rows.append(Application(**payload))
    db_session.add_all(rows)
    db_session.commit()
    for row in rows:
        db_session.refresh(row)
    return [row.id for row in rows]


def _bulk_insert_with_overrides(
    db_session, count: int, overrides: dict | None = None, name_prefix: str = "Bulk"
) -> list[Application]:
    """Like _bulk_insert_applications, but every row shares the exact same
    (optional) score-affecting overrides - so the whole batch lands in one
    priority band, deterministically - while first_name is unique per row
    (name_prefix + index) so a search can be made to target exactly one, all,
    or none of them. Single commit (fast), rows returned in insertion (== id)
    order."""
    rows = []
    for i in range(count):
        payload = _application_payload(first_name=f"{name_prefix}{i}", **(overrides or {}))
        payload["budget"] = Decimal(str(payload["budget"]))
        payload["interested_product"] = "Test Default Service"
        rows.append(Application(**payload))
    db_session.add_all(rows)
    db_session.commit()
    for row in rows:
        db_session.refresh(row)
    return rows


def test_prioritized_pagination_has_no_gaps_or_duplicates_across_pages(
    client, admin_auth_headers, db_session
):
    inserted_ids = set(_bulk_insert_applications(db_session, 250))

    seen_ids: list[int] = []
    seen_scores: list[int] = []
    for skip, expected_count in ((0, 100), (100, 100), (200, 50)):
        response = client.get(
            "/api/applications/prioritized",
            params={"skip": skip, "limit": 100},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 250
        assert body["skip"] == skip
        assert len(body["items"]) == expected_count, f"page at skip={skip} has the wrong size"
        seen_ids.extend(item["application"]["id"] for item in body["items"])
        seen_scores.extend(item["priority_score"] for item in body["items"])

    # No duplicate rows, and no row missing - the three pages together are
    # exactly the 250 rows this test created, each exactly once.
    assert len(seen_ids) == 250
    assert len(set(seen_ids)) == 250
    assert set(seen_ids) == inserted_ids

    # Ordering is preserved *across* page boundaries, not just within one
    # page: the full concatenated score sequence is non-increasing.
    assert seen_scores == sorted(seen_scores, reverse=True)


def test_prioritized_final_page_is_a_genuine_partial_page(client, admin_auth_headers, db_session):
    _bulk_insert_applications(db_session, 250)

    response = client.get(
        "/api/applications/prioritized",
        params={"skip": 200, "limit": 100},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 250
    assert len(body["items"]) == 50  # 250 - 200, not padded/truncated to 100


def test_prioritized_query_is_bounded_by_limit_at_the_sql_level(
    client, admin_auth_headers, db_session, db_engine
):
    """Behavioral proof (not an ORM-internals assertion): captures the exact
    SQL PostgreSQL receives for this request. The applications-table SELECT
    this endpoint issues must itself carry a LIMIT clause - i.e. PostgreSQL
    is asked for only the requested page, not for every one of the 250 rows
    this test inserted. (The separate COUNT(*) statement used for `total` is
    expected and deliberately excluded below - it scans the table by design,
    but never materializes a row into Python.)"""
    _bulk_insert_applications(db_session, 250)

    captured_statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured_statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", _capture)
    try:
        response = client.get(
            "/api/applications/prioritized",
            params={"skip": 0, "limit": 25},
            headers=admin_auth_headers,
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", _capture)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 25

    application_row_selects = [
        stmt
        for stmt in captured_statements
        if "FROM applications" in stmt and "count(" not in stmt.lower()
    ]
    assert application_row_selects, "expected a row-fetching SELECT ... FROM applications"
    for stmt in application_row_selects:
        assert "LIMIT" in stmt.upper(), f"query against applications has no LIMIT clause: {stmt!r}"


# ---------------------------------------------------------------------------
# Stage 4 correction: server-side search/priority filtering.
#
# Independent audit finding: admin search and priority filtering ran only
# against whatever page of GET /applications/prioritized was already loaded
# in the browser (frontend/src/pages/adminApplications.ts's now-removed
# filterApplications/matchesSearch) while the UI presented the result as
# application-wide - with >100 applications and the only match on page 2,
# page 1 showed "Ничего не найдено" even though a real match existed. The
# fix moves both criteria into the SQL WHERE clause (see
# app/crud/application.py::get_prioritized_applications_page), applied
# before COUNT and before OFFSET/LIMIT, so `total` and every page's rows
# always describe the same filtered corpus. These tests reproduce the exact
# failure mode and its corrected behavior, plus the surrounding contract
# (multi-page search, multi-page priority filtering, combined criteria,
# literal-substring/case-insensitive search semantics, and validation).
# ---------------------------------------------------------------------------


def test_prioritized_search_finds_match_on_a_later_unfiltered_page(
    client, admin_auth_headers, db_session
):
    """Direct reproduction of the independent audit's finding: >100
    applications, the only search match positioned well beyond the first
    *unfiltered* page (same score/created_at -> id-ascending tie-break, and
    inserted last), request page 1 WITH the search criterion active."""
    _bulk_insert_with_overrides(db_session, 150, name_prefix="ОбычнаяЗаявка")
    match = _insert_application(db_session, first_name="УникальныйКлиент")

    # Confirm the premise: in the *unfiltered* order this row is on page 2,
    # not page 1 - exactly the audit's "1-100 из 101" / page-2-only match
    # setup, generalized to 151 rows.
    unfiltered_page1 = client.get(
        "/api/applications/prioritized",
        params={"skip": 0, "limit": 100},
        headers=admin_auth_headers,
    ).json()
    assert match.id not in {item["application"]["id"] for item in unfiltered_page1["items"]}

    response = client.get(
        "/api/applications/prioritized",
        params={"skip": 0, "limit": 100, "search": "УникальныйКлиент"},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    # The filtered corpus is exactly 1 row - never the false-empty result the
    # page-only-filtering defect produced, and never the unfiltered total.
    assert body["total"] == 1
    assert body["skip"] == 0
    assert len(body["items"]) == 1
    assert body["items"][0]["application"]["id"] == match.id


def test_prioritized_search_pagination_has_no_gaps_or_duplicates_across_pages(
    client, admin_auth_headers, db_session
):
    matching = _bulk_insert_with_overrides(db_session, 130, name_prefix="МаркерПоиска")
    _bulk_insert_with_overrides(db_session, 40, name_prefix="ШумБезСовпадения")
    matching_ids = {row.id for row in matching}

    seen_ids: list[int] = []
    for skip, expected_count in ((0, 100), (100, 30)):
        response = client.get(
            "/api/applications/prioritized",
            params={"skip": skip, "limit": 100, "search": "МаркерПоиска"},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 130
        assert body["skip"] == skip
        assert len(body["items"]) == expected_count, f"page at skip={skip} has the wrong size"
        seen_ids.extend(item["application"]["id"] for item in body["items"])

    assert len(seen_ids) == 130
    assert len(set(seen_ids)) == 130
    assert set(seen_ids) == matching_ids


def test_prioritized_priority_filter_spans_multiple_pages_and_excludes_other_levels(
    client, admin_auth_headers, db_session
):
    hot_rows = _bulk_insert_with_overrides(db_session, 120, _SCORE_PROFILES[4], name_prefix="Горячий")
    _bulk_insert_with_overrides(db_session, 30, name_prefix="Обычный")  # default profile -> low
    hot_ids = {row.id for row in hot_rows}

    seen_ids: list[int] = []
    for skip, expected_count in ((0, 100), (100, 20)):
        response = client.get(
            "/api/applications/prioritized",
            params={"skip": skip, "limit": 100, "priority": "hot"},
            headers=admin_auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 120
        assert len(body["items"]) == expected_count, f"page at skip={skip} has the wrong size"
        for item in body["items"]:
            assert item["priority_level"] == "hot"
        seen_ids.extend(item["application"]["id"] for item in body["items"])

    assert len(seen_ids) == 120
    assert len(set(seen_ids)) == 120
    assert set(seen_ids) == hot_ids


def test_prioritized_combined_search_and_priority_filter_intersect(
    client, admin_auth_headers, db_session
):
    hot_overrides = _SCORE_PROFILES[4]
    target = _insert_application(db_session, first_name="ПересечениеЦель", **hot_overrides)
    # Matches the search text but is not "hot" -> excluded by the priority filter.
    _insert_application(db_session, first_name="ПересечениеЦель")
    # Is "hot" but does not match the search text -> excluded by the search filter.
    _insert_application(db_session, first_name="ДругоеИмя", **hot_overrides)

    response = client.get(
        "/api/applications/prioritized",
        params={"skip": 0, "limit": 100, "search": "ПересечениеЦель", "priority": "hot"},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["application"]["id"] == target.id


def test_prioritized_zero_matches_returns_empty_items_and_zero_total(
    client, admin_auth_headers, db_session
):
    """A real corpus-wide zero: items empty, filtered total 0, pager/range
    stays coherent (skip echoed back unchanged) - the "Ничего не найдено"
    state is only ever correct when this is what the backend reports."""
    _bulk_insert_applications(db_session, 5)

    response = client.get(
        "/api/applications/prioritized",
        params={"search": "СовершенноНесуществующийЗапрос12345"},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["skip"] == 0


def test_prioritized_blank_search_behaves_as_no_search(client, admin_auth_headers, db_session):
    _bulk_insert_applications(db_session, 5)
    unfiltered_total = client.get("/api/applications/prioritized", headers=admin_auth_headers).json()[
        "total"
    ]

    for blank in ("", "   ", "\t\n "):
        response = client.get(
            "/api/applications/prioritized", params={"search": blank}, headers=admin_auth_headers
        )
        assert response.status_code == 200
        assert response.json()["total"] == unfiltered_total


def test_prioritized_search_is_case_insensitive(client, admin_auth_headers, db_session):
    match = _insert_application(db_session, first_name="Анна")

    response = client.get(
        "/api/applications/prioritized", params={"search": "АННА"}, headers=admin_auth_headers
    )
    assert response.status_code == 200
    ids = {item["application"]["id"] for item in response.json()["items"]}
    assert match.id in ids


def test_prioritized_search_percent_is_a_literal_character_not_a_wildcard(
    client, admin_auth_headers, db_session
):
    """If '%' were passed straight into LIKE, "20%" would match any text
    starting with "20" followed by anything - it must instead only match
    applications whose text contains the literal substring "20%"."""
    literal_match = _insert_application(db_session, business_info="Скидка 20% на услугу")
    non_match = _insert_application(db_session, business_info="Скидка 20 рублей на услугу")

    response = client.get(
        "/api/applications/prioritized", params={"search": "20%"}, headers=admin_auth_headers
    )
    assert response.status_code == 200
    ids = {item["application"]["id"] for item in response.json()["items"]}
    assert literal_match.id in ids
    assert non_match.id not in ids


def test_prioritized_search_underscore_is_a_literal_character_not_a_wildcard(
    client, admin_auth_headers, db_session
):
    """If '_' were passed straight into LIKE, it would match any single
    character - "A_B" must only match the literal substring "A_B", not
    "AXB"."""
    literal_match = _insert_application(db_session, business_info="Код A_B в описании")
    non_match = _insert_application(db_session, business_info="Код AXB в описании")

    response = client.get(
        "/api/applications/prioritized", params={"search": "A_B"}, headers=admin_auth_headers
    )
    assert response.status_code == 200
    ids = {item["application"]["id"] for item in response.json()["items"]}
    assert literal_match.id in ids
    assert non_match.id not in ids


def test_prioritized_filtered_query_is_bounded_by_limit_at_the_sql_level(
    client, admin_auth_headers, db_session, db_engine
):
    """Same behavioral proof as
    test_prioritized_query_is_bounded_by_limit_at_the_sql_level, but with an
    active search criterion: the filter must be applied inside the same
    bounded SQL query, not by fetching every matching row into Python."""
    _bulk_insert_with_overrides(db_session, 250, name_prefix="БольшойНабор")

    captured_statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        captured_statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", _capture)
    try:
        response = client.get(
            "/api/applications/prioritized",
            params={"skip": 0, "limit": 25, "search": "БольшойНабор"},
            headers=admin_auth_headers,
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", _capture)

    assert response.status_code == 200
    assert len(response.json()["items"]) == 25

    application_row_selects = [
        stmt
        for stmt in captured_statements
        if "FROM applications" in stmt and "count(" not in stmt.lower()
    ]
    assert application_row_selects, "expected a row-fetching SELECT ... FROM applications"
    for stmt in application_row_selects:
        assert "ILIKE" in stmt.upper(), f"query against applications has no ILIKE clause: {stmt!r}"
        assert "LIMIT" in stmt.upper(), f"query against applications has no LIMIT clause: {stmt!r}"


@pytest.mark.parametrize(
    "params",
    [
        {"priority": "blazing"},
        {"priority": "HOT"},  # closed enum is case-sensitive lowercase, not an open string
        {"priority": ""},
    ],
)
def test_prioritized_rejects_invalid_priority_value(client, admin_auth_headers, params):
    response = client.get(
        "/api/applications/prioritized", params=params, headers=admin_auth_headers
    )
    assert response.status_code == 422


def test_prioritized_rejects_search_longer_than_max_length(client, admin_auth_headers):
    response = client.get(
        "/api/applications/prioritized",
        params={"search": "a" * 201},
        headers=admin_auth_headers,
    )
    assert response.status_code == 422


def test_prioritized_accepts_search_at_max_length(client, admin_auth_headers):
    response = client.get(
        "/api/applications/prioritized",
        params={"search": "a" * 200},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200


def test_list_applications_endpoint_keeps_previous_contract(client, admin_auth_headers):
    client.post("/api/applications", json=_application_payload())
    response = client.get("/api/applications", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert "priority_score" not in body[0]
    assert set(body[0].keys()) == {
        "id",
        "first_name",
        "last_name",
        "middle_name",
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
        "service_id",
        "interested_product",
        "budget",
        "preferred_contact_method",
        "preferred_contact_time",
        "comment",
        "created_at",
        "updated_at",
    }
