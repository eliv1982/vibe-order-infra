"""Shared read-only schema-introspection helpers.

Both app/db_admin/adopt_legacy.py's legacy fingerprint check and
app/core/schema_check.py's startup guard need to compare a live database's
actual column/constraint shape against a small, fixed expected shape, in the
same handful of ways (type/length/precision/nullability/server-default/PK/
unique/FK). Kept in one place instead of duplicated between the two.

This is deliberately NOT a general schema-diff/autogenerate framework - just
field-by-field comparisons against an explicit, hand-written spec for a
small fixed set of tables. Callers own their own spec data; this module only
owns the comparison logic.
"""

from __future__ import annotations

import re
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, ContextManager

from sqlalchemy import Connection, text
from sqlalchemy.engine import Inspector

# The one schema this project's application tables live in. Deliberately a
# fixed literal, never derived from inspector.default_schema_name/current
# user/search_path/connection defaults - PostgreSQL's default search_path
# ("$user", public) means those all silently follow whatever schema happens
# to be first for the connected role, which is exactly the ambiguity a
# same-named non-public schema (e.g. one named after the connecting role)
# can exploit: reflection would inspect - and adoption/ownership changes
# would mutate - that shadow schema instead of the real "public" one. Every
# caller in this module and its siblings (app.core.schema_check,
# app.db_admin.adopt_legacy, app.db_admin.bootstrap_roles) shares this one
# constant instead of each deriving its own notion of "the" schema.
APPLICATION_SCHEMA = "public"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    # "INTEGER" | "VARCHAR" | "TEXT" | "BOOLEAN" | "NUMERIC" | "TIMESTAMPTZ" | "JSONB"
    kind: str
    nullable: bool
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    # None = don't check server-side default presence at all; True = must
    # have one (e.g. created_at's now(), or a SERIAL pk's nextval); False =
    # must NOT have one (every other column in this project's models - their
    # defaults, where they have any, are applied by SQLAlchemy at INSERT
    # time, not by PostgreSQL).
    server_default: bool | None = None
    # True only for a legacy SERIAL-backed INTEGER primary-key column. The
    # server_default=True check above only proves *some* server-side default
    # exists - a column whose default was replaced with a constant (e.g.
    # `DEFAULT 7`) still passes it. serial_pk=True additionally verifies real
    # PostgreSQL sequence semantics via _serial_sequence_problems: the
    # default is genuinely nextval() of a sequence PostgreSQL considers
    # OWNED BY this exact column, with ordinary ascending non-cycling integer
    # behavior, and whose next generated value cannot collide with a row
    # already in the table.
    serial_pk: bool = False


class _CheckNoAction:
    """Sentinel for ForeignKeySpec.ondelete: expect PostgreSQL's default FK
    delete behavior (ON DELETE NO ACTION), as distinct from `None` (meaning:
    don't check ondelete at all). These must not be conflated: PostgreSQL/
    SQLAlchemy reflect an FK with no ON DELETE clause and one with an
    explicit `ON DELETE NO ACTION` clause identically (neither ever sets
    options['ondelete']) - both are this value, and both differ from
    genuinely not caring what the delete action is."""

    def __repr__(self) -> str:
        return "CHECK_NO_ACTION"


CHECK_NO_ACTION = _CheckNoAction()


@dataclass(frozen=True)
class ForeignKeySpec:
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]
    # Every table this project's specs describe lives in "public". Comparison
    # normalizes the reflected value the same way (see describe_table_
    # problems) before comparing, since PostgreSQL/SQLAlchemy reflection may
    # report a same-schema FK's referred_schema as either None or "public"
    # depending on whether an explicit schema= was passed to reflection.
    ref_schema: str = APPLICATION_SCHEMA
    # None = don't check ondelete at all. CHECK_NO_ACTION = expect
    # PostgreSQL's default delete behavior (no CASCADE/SET NULL/SET DEFAULT/
    # RESTRICT). A literal string (e.g. "CASCADE") = expect exactly that
    # explicit action.
    ondelete: str | _CheckNoAction | None = None


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[ColumnSpec, ...]
    primary_key: tuple[str, ...]
    unique_constraints: tuple[frozenset[str], ...] = ()
    foreign_keys: tuple[ForeignKeySpec, ...] = ()


def _type_matches(actual: Any, spec: ColumnSpec) -> bool:
    cls_name = type(actual).__name__.upper()
    if spec.kind == "INTEGER":
        return cls_name == "INTEGER"
    if spec.kind == "VARCHAR":
        return cls_name == "VARCHAR" and getattr(actual, "length", None) == spec.length
    if spec.kind == "TEXT":
        return cls_name == "TEXT"
    if spec.kind == "BOOLEAN":
        return cls_name == "BOOLEAN"
    if spec.kind == "NUMERIC":
        return (
            cls_name == "NUMERIC"
            and getattr(actual, "precision", None) == spec.precision
            and getattr(actual, "scale", None) == spec.scale
        )
    if spec.kind == "TIMESTAMPTZ":
        return cls_name in ("TIMESTAMP", "DATETIME") and bool(getattr(actual, "timezone", False))
    if spec.kind == "JSONB":
        return cls_name == "JSONB"
    raise ValueError(f"unknown ColumnSpec.kind {spec.kind!r}")


_NEXTVAL_DEFAULT_RE = re.compile(r"^nextval\('((?:[^']|'')*)'::regclass\)$")


def _inspector_connection(inspector: Inspector) -> ContextManager[Connection]:
    """A context manager yielding a Connection usable for raw catalog SQL,
    from an Inspector's bind - whichever of Engine/Connection it happens to
    be (both occur across this module's callers). Never closes a Connection
    this function did not itself open."""
    bind = inspector.bind
    if isinstance(bind, Connection):
        return nullcontext(bind)
    return bind.connect()


def _serial_sequence_problems(
    conn: Connection, schema: str, table: str, column: str, actual_default: str | None
) -> list[str]:
    """Read-only. Verifies that `schema.table.column` (already known to be a
    legacy SERIAL-backed INTEGER primary key per ColumnSpec.serial_pk) has
    real PostgreSQL sequence semantics, not merely *some* server-side
    default - see ColumnSpec.serial_pk's docstring for exactly what
    "has a default" alone misses. Never calls setval() or otherwise mutates
    anything; a sequence found behind the table's current max id is reported
    as a problem, never silently repaired."""
    label = f"table {table!r} column {column!r}"

    if actual_default is None:
        return []  # already reported by the generic server_default=True check above

    match = _NEXTVAL_DEFAULT_RE.match(actual_default)
    if match is None:
        return [
            f"{label} is expected to be a legacy SERIAL primary key generated by a sequence "
            f"(nextval()), but its actual default is {actual_default!r} - not sequence-backed at all "
            "(e.g. a constant literal such as a hand-set `DEFAULT 7`)"
        ]
    default_seq_ref = match.group(1).replace("''", "'")

    qualified_table = f"{schema}.{table}"
    owned_seq_name = conn.execute(
        text("SELECT pg_get_serial_sequence(:qualified_table, :column)"),
        {"qualified_table": qualified_table, "column": column},
    ).scalar()

    if owned_seq_name is None:
        return [
            f"{label}'s default {actual_default!r} references a sequence, but PostgreSQL does not "
            "consider any sequence to be OWNED BY this exact column (a detached/freestanding "
            "sequence, or one actually owned by a different column/table)"
        ]

    # to_regclass() never raises for a name that doesn't resolve to a real
    # object (unlike an explicit ::regclass cast) - comparing OIDs this way
    # sidesteps schema-qualification/quoting differences between the two
    # textual forms entirely, safely, even if default_seq_ref turns out not
    # to name a real object at all.
    same_object = conn.execute(
        text("SELECT to_regclass(:owned) = to_regclass(:default_ref)"),
        {"owned": owned_seq_name, "default_ref": default_seq_ref},
    ).scalar()
    if not same_object:
        return [
            f"{label}'s default references sequence {default_seq_ref!r}, but the sequence "
            f"PostgreSQL considers OWNED BY this column is a different object ({owned_seq_name!r}) - "
            "not owned/dependency-bound to the expected table/column"
        ]

    problems: list[str] = []

    seq_row = conn.execute(
        text(
            "SELECT data_type, increment_by, cycle FROM pg_sequences "
            "WHERE schemaname || '.' || sequencename = :owned"
        ),
        {"owned": owned_seq_name},
    ).one()
    if seq_row.data_type != "integer" or seq_row.increment_by != 1 or seq_row.cycle:
        problems.append(
            f"{label}'s sequence {owned_seq_name!r} has unexpected behavior for an INTEGER SERIAL "
            f"primary key (data_type={seq_row.data_type!r}, increment_by={seq_row.increment_by!r}, "
            f"cycle={seq_row.cycle!r})"
        )

    # owned_seq_name comes straight from PostgreSQL's own pg_get_serial_
    # sequence() - already schema-qualified/quoted by PostgreSQL specifically
    # for reinsertion into SQL (exactly the idiom `currval(pg_get_serial_
    # sequence(...))` relies on), never attacker/user input, so it is safe to
    # interpolate directly here.
    seq_state = conn.execute(text(f"SELECT last_value, is_called FROM {owned_seq_name}")).one()
    next_value = (
        seq_state.last_value if not seq_state.is_called else seq_state.last_value + seq_row.increment_by
    )

    # schema/table/column are always this module's own hardcoded TableSpec/
    # ColumnSpec literals (never attacker/user input) - see this file's
    # module docstring; direct interpolation here matches how the rest of
    # this module already treats these same fixed identifiers.
    max_id = conn.execute(text(f'SELECT max("{column}") FROM "{schema}"."{table}"')).scalar()
    if max_id is not None and next_value <= max_id:
        problems.append(
            f"{label}'s sequence {owned_seq_name!r} would next generate {next_value}, but "
            f"{table!r} already has a row with {column}={max_id} - inserting a new row would collide "
            "(refusing rather than silently repairing via setval())"
        )

    return problems


def describe_table_problems(inspector: Inspector, spec: TableSpec) -> list[str]:
    """Read-only. Returns a list of human-readable problem descriptions -
    empty iff the live table already-known-to-exist in `inspector` matches
    `spec` closely enough. Callers must check the table exists before
    calling this (it does not itself special-case a missing table)."""
    problems: list[str] = []
    table = spec.name
    # The one fixed application schema (see APPLICATION_SCHEMA's docstring
    # above) - used both to reflect every object below explicitly in that
    # schema (rather than inspector.default_schema_name, which follows the
    # connection's actual search_path/current user and could resolve to a
    # same-named non-public schema) and to normalize an FK's reflected
    # referred_schema, which PostgreSQL/SQLAlchemy may report as None for a
    # same-schema reference depending on whether reflection was passed
    # schema= at all. A same-named object sitting in some other schema must
    # never compare equal to the intended public object - deriving `schema`
    # from the connection's own ambient default would defeat exactly that.
    schema = APPLICATION_SCHEMA

    actual_columns = {c["name"]: c for c in inspector.get_columns(table, schema=schema)}
    expected_names = {c.name for c in spec.columns}
    unexpected = set(actual_columns) - expected_names
    if unexpected:
        problems.append(f"table {table!r} has unexpected column(s) {sorted(unexpected)!r}")

    serial_pk_checks: list[tuple[str, str | None]] = []
    for col_spec in spec.columns:
        actual = actual_columns.get(col_spec.name)
        if actual is None:
            problems.append(f"table {table!r} is missing expected column {col_spec.name!r}")
            continue
        if not _type_matches(actual["type"], col_spec):
            problems.append(
                f"table {table!r} column {col_spec.name!r} has type {actual['type']!r}, expected "
                f"kind={col_spec.kind!r} length={col_spec.length!r} precision={col_spec.precision!r} "
                f"scale={col_spec.scale!r}"
            )
        if actual["nullable"] != col_spec.nullable:
            problems.append(
                f"table {table!r} column {col_spec.name!r} nullable={actual['nullable']!r}, "
                f"expected nullable={col_spec.nullable!r}"
            )
        if col_spec.server_default is not None:
            has_default = actual.get("default") is not None
            if has_default != col_spec.server_default:
                verb = "has an unexpected" if has_default else "is missing the expected"
                problems.append(f"table {table!r} column {col_spec.name!r} {verb} server-side default")
        if col_spec.serial_pk:
            serial_pk_checks.append((col_spec.name, actual.get("default")))

    if serial_pk_checks:
        with _inspector_connection(inspector) as conn:
            for column_name, actual_default in serial_pk_checks:
                problems.extend(_serial_sequence_problems(conn, schema, table, column_name, actual_default))

    pk = inspector.get_pk_constraint(table, schema=schema)
    actual_pk = set(pk.get("constrained_columns") or ())
    if actual_pk != set(spec.primary_key):
        problems.append(
            f"table {table!r} primary key is {sorted(actual_pk)!r}, expected {sorted(spec.primary_key)!r}"
        )

    actual_uniques = {
        frozenset(uc["column_names"]) for uc in inspector.get_unique_constraints(table, schema=schema)
    }
    for expected_unique in spec.unique_constraints:
        if expected_unique not in actual_uniques:
            problems.append(
                f"table {table!r} is missing expected UNIQUE constraint on {sorted(expected_unique)!r}"
            )

    actual_fks = inspector.get_foreign_keys(table, schema=schema)
    for fk_spec in spec.foreign_keys:
        match = next(
            (fk for fk in actual_fks if tuple(fk.get("constrained_columns") or ()) == fk_spec.columns),
            None,
        )
        if match is None:
            problems.append(f"table {table!r} is missing expected FOREIGN KEY on {fk_spec.columns!r}")
            continue
        # PostgreSQL/SQLAlchemy may report a same-schema FK's referred_schema
        # as either None or the literal schema name, regardless of the
        # schema= passed to get_foreign_keys above - normalized to the fixed
        # APPLICATION_SCHEMA (never inspector.default_schema_name/the
        # connection's ambient search_path) before comparing, so a genuinely
        # different schema (e.g. "shadow", or one named after the connecting
        # role) can never be mistaken for it.
        actual_ref_schema = match.get("referred_schema") or schema
        actual_ref = (actual_ref_schema, match.get("referred_table"), tuple(match.get("referred_columns") or ()))
        expected_ref = (fk_spec.ref_schema, fk_spec.ref_table, fk_spec.ref_columns)
        if actual_ref != expected_ref:
            problems.append(
                f"table {table!r} FOREIGN KEY on {fk_spec.columns!r} references {actual_ref!r}, "
                f"expected {expected_ref!r}"
            )
        elif fk_spec.ondelete is not None:
            actual_ondelete = (match.get("options") or {}).get("ondelete")
            if fk_spec.ondelete is CHECK_NO_ACTION:
                if actual_ondelete is not None:
                    problems.append(
                        f"table {table!r} FOREIGN KEY on {fk_spec.columns!r} has ondelete="
                        f"{actual_ondelete!r}, expected ON DELETE NO ACTION (PostgreSQL's default - "
                        "no explicit CASCADE/SET NULL/SET DEFAULT/RESTRICT)"
                    )
            elif actual_ondelete != fk_spec.ondelete:
                problems.append(
                    f"table {table!r} FOREIGN KEY on {fk_spec.columns!r} has ondelete="
                    f"{actual_ondelete!r}, expected {fk_spec.ondelete!r}"
                )

    return problems
