"""Domain-level exceptions raised by the CRUD layer.

Kept free of FastAPI/HTTPException so responsibility stays separated:
crud raises these, routes translate them into the appropriate HTTP response.
"""


class DomainError(Exception):
    """Base class for errors raised by CRUD functions."""


class ConflictError(DomainError):
    """A create/update/delete violated a uniqueness or integrity constraint.

    Also used to convert a race-condition IntegrityError (e.g. two
    concurrent requests both passing an existence/uniqueness check before
    either has committed) into a well-defined 409 at the route layer
    instead of letting a raw IntegrityError surface as a 500.
    """


class DomainValidationError(DomainError):
    """A business rule was violated once update data is merged with the
    persisted state (e.g. budget_min > budget_max after a partial PATCH)."""
