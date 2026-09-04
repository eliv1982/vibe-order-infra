"""Stage 2 database-lifecycle administrative tooling.

Everything in this package connects with either the cluster bootstrap/admin
credential (POSTGRES_USER/POSTGRES_PASSWORD - the Postgres Docker image's
own superuser) or the migration/owner credential (MIGRATION_DB_USER/
MIGRATION_DB_PASSWORD) - never the runtime application credential
(app.core.config.Settings). Nothing here is imported by app.main or any
request-handling code path; it is only ever invoked as a one-shot script
(see docker-compose.yml's db-roles-bootstrap/db-roles-finalize/db-migrate
services) or directly from tests.
"""
