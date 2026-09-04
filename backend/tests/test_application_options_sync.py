"""Guards against app/schemas/application_options.py silently drifting from
frontend/src/options.ts.

No database or network access required - these always run, including when
TEST_DATABASE_URL is not set. app/schemas/application_options.py's own
module docstring explains why this pairing exists: if the frontend ever
adds/removes/renames a select/radio option without a matching backend
change, a legitimate frontend submission using the new option would start
being rejected as an invalid categorical value.
"""

import re
from pathlib import Path

from app.schemas import application_options as opts

_FRONTEND_OPTIONS_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "options.ts"

_EXPORT_PATTERN = re.compile(r"export const (\w+) = \[(.*?)\];", re.DOTALL)
_STRING_LITERAL_PATTERN = re.compile(r"'((?:[^'\\]|\\.)*)'")

# Maps each frontend exported option-list constant name to the matching
# backend Literal type (see app/schemas/application_options.py).
_TS_CONST_TO_PY_LITERAL = {
    "BUSINESS_NICHE_OPTIONS": opts.BusinessNiche,
    "COMPANY_SIZE_OPTIONS": opts.CompanySize,
    "BUSINESS_SIZE_OPTIONS": opts.BusinessSize,
    "REQUESTER_ROLE_OPTIONS": opts.RequesterRole,
    "TASK_SCOPE_OPTIONS": opts.TaskScope,
    "TASK_TYPE_OPTIONS": opts.TaskType,
    "DEADLINE_OPTIONS": opts.Deadline,
    "PREFERRED_CONTACT_METHOD_OPTIONS": opts.PreferredContactMethod,
    "PREFERRED_CONTACT_TIME_OPTIONS": opts.PreferredContactTime,
}


def _parse_frontend_options() -> dict[str, list[str]]:
    content = _FRONTEND_OPTIONS_PATH.read_text(encoding="utf-8")
    return {
        name: _STRING_LITERAL_PATTERN.findall(body)
        for name, body in _EXPORT_PATTERN.findall(content)
    }


def test_frontend_options_file_exists_and_is_parseable():
    assert _FRONTEND_OPTIONS_PATH.is_file(), _FRONTEND_OPTIONS_PATH
    parsed = _parse_frontend_options()
    assert set(parsed) == set(_TS_CONST_TO_PY_LITERAL), (
        "frontend/src/options.ts exported option-list names changed - "
        "update _TS_CONST_TO_PY_LITERAL above to match"
    )


def test_every_backend_literal_matches_its_frontend_option_list_exactly():
    parsed = _parse_frontend_options()
    for ts_name, literal_type in _TS_CONST_TO_PY_LITERAL.items():
        ts_values = parsed[ts_name]
        py_values = list(literal_type.__args__)
        assert py_values == ts_values, (
            f"{ts_name} (frontend) and its backend Literal type "
            f"(app/schemas/application_options.py) are out of sync:\n"
            f"  frontend: {ts_values}\n"
            f"  backend:  {py_values}"
        )
