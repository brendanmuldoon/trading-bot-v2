"""Typed broker error hierarchy (T07).

The critical distinction (T11 depends on it): a write whose outcome is
unknown (timeout / 5xx / connection drop during POST) raises
AmbiguousResultError — the order MAY exist broker-side, so callers
must verify before resubmitting (C11). Reads and clear rejections are
unambiguous.
"""


class BrokerError(Exception):
    """Base for everything raised by the broker client."""


class AuthError(BrokerError):
    """401/403 — bad key or missing scope."""


class RateLimitedError(BrokerError):
    """429 — over budget. Retry/backoff is the governor's job (T08)."""

    def __init__(self, message: str, reset_at: int | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class BrokerValidationError(BrokerError):
    """400 — request rejected deterministically (e.g. unsupported stop)."""


class NotFoundError(BrokerError):
    """404 — unknown resource (e.g. order id)."""


class ServerError(BrokerError):
    """5xx on a read — broker-side trouble; safe to retry the read."""


class BrokerTimeoutError(BrokerError):
    """Timeout on a read — safe to retry."""


class AmbiguousResultError(BrokerError):
    """A write may or may not have been applied. NEVER blindly retry —
    verify first (§8.4 step-2 recovery rule)."""
