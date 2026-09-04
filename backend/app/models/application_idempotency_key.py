"""SQLAlchemy model for POST /applications idempotency keys.

Not a capability and not itself an authorization token - a row here only
lets a retried request with the *same* Idempotency-Key header and the same
request payload be recognized as a duplicate of an already-processed
request, instead of creating a second Application (see
app/crud/application.py::create_application_idempotent). Knowing a key
never authorizes reading, updating or deleting anything: this table is only
ever consulted from POST /applications, and only to decide "have I already
handled this exact submission", nothing else.

Stage 1B correction: only a SHA-256 digest of the client-supplied key is
ever persisted (app.core.security.hash_idempotency_key), never the raw key
itself. A digest is one-way: it lets a genuine retry (which still has the
raw key) be recognized, but reading the stored digest out of the database
gives no way to reconstruct or submit a working key (submitting the digest
string itself as the header just hashes to something else again, and is
treated as an unrelated, never-before-seen key).

A second, independent correction (also Stage 1B): even holding the raw key
itself never mints or recovers a behavior-metrics capability. A replayed
POST /applications (see app.crud.application.create_application_idempotent)
only ever returns the original Application - never a capability, whether
the original one is still unconsumed or was already used. This table is
therefore not authorization material even in plaintext form: knowing a key
lets a caller recognize "have I already handled this exact submission",
nothing more.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_base import Base


class ApplicationIdempotencyKey(Base):
    """
    Example DDL (PostgreSQL):

    CREATE TABLE application_idempotency_keys (
        id SERIAL PRIMARY KEY,
        idempotency_key_hash VARCHAR(64) NOT NULL UNIQUE,
        request_hash VARCHAR(64) NOT NULL,
        application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    idempotency_key_hash is UNIQUE and is the SHA-256 hex digest of the
    client-supplied Idempotency-Key header value (format/length validated in
    app/routes/applications.py before it ever reaches the database - see
    that module's minimum-length requirement, chosen for collision
    resistance between unrelated keys, not because the key is treated as a
    secret) - the UNIQUE index is what makes "claim this key" a single
    atomic operation two concurrent identical requests can race on safely
    (see app.crud.application.create_application_idempotent). request_hash
    is a separate SHA-256 hex digest (app.core.security.
    hash_idempotency_payload) of the canonicalized request body, used to
    detect a reused key with a different payload, which is rejected rather
    than silently served. application_id is nullable only for the brief
    in-transaction window between claiming the key and finishing the
    Application insert in the same transaction - a *committed* row always
    has it set, since both inserts share one commit.
    """

    __tablename__ = "application_idempotency_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
