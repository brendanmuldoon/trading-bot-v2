"""T212 HTTP client core (T07) — the only code that talks to T212 (§8).

HTTP Basic auth (C4), env-driven base URL (C1), typed error mapping.
No retry logic here: retries, throttling, and prioritization belong to
the rate-limit governor (T08). Credentials are never logged.
"""

import logging
from typing import Any, Literal

import httpx

from backend.broker.errors import (
    AmbiguousResultError,
    AuthError,
    BrokerTimeoutError,
    BrokerValidationError,
    NotFoundError,
    RateLimitedError,
    ServerError,
)
from backend.broker.models import AccountSummary, Cash

logger = logging.getLogger("bot.broker")

BASE_URLS = {
    "demo": "https://demo.trading212.com",
    "live": "https://live.trading212.com",
}
API = "/api/v0"

# Writes (POST/DELETE) with unknown outcomes raise AmbiguousResultError.
_WRITE_METHODS = {"POST", "DELETE"}


class T212Client:
    """Thin typed wrapper over the §8.1 endpoints used in R1."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        env: Literal["demo", "live"] = "demo",
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.env = env
        self.base_url = BASE_URLS[env]
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            auth=(api_key, api_secret),
            timeout=timeout,
            transport=transport,  # type: ignore[arg-type]
        )

    def __repr__(self) -> str:  # never expose credentials
        return f"T212Client(env={self.env!r})"

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- request core ------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Single request with typed error mapping. The governor (T08)
        is the layer that decides when/whether to call this."""
        is_write = method.upper() in _WRITE_METHODS
        try:
            resp = await self._http.request(method, path, json=json, params=params)
        except httpx.TimeoutException as exc:
            if is_write:
                raise AmbiguousResultError(f"{method} {path}: timeout — outcome unknown") from exc
            raise BrokerTimeoutError(f"{method} {path}: timeout") from exc
        except httpx.TransportError as exc:
            if is_write:
                raise AmbiguousResultError(
                    f"{method} {path}: transport error — outcome unknown"
                ) from exc
            raise BrokerTimeoutError(f"{method} {path}: transport error") from exc

        if resp.status_code in (401, 403):
            raise AuthError(f"{method} {path}: {resp.status_code}")
        if resp.status_code == 429:
            reset = resp.headers.get("x-ratelimit-reset")
            raise RateLimitedError(
                f"{method} {path}: rate limited",
                reset_at=int(reset) if reset and reset.isdigit() else None,
            )
        if resp.status_code == 400:
            raise BrokerValidationError(f"{method} {path}: {_safe_error(resp)}")
        if resp.status_code == 404:
            raise NotFoundError(f"{method} {path}: not found")
        if resp.status_code >= 500 or resp.status_code == 408:
            if is_write:
                raise AmbiguousResultError(f"{method} {path}: {resp.status_code} — outcome unknown")
            raise ServerError(f"{method} {path}: {resp.status_code}")
        return resp

    # ---- account (§8.1) ------------------------------------------------------

    async def get_account_summary(self) -> AccountSummary:
        resp = await self.request("GET", f"{API}/equity/account/summary")
        return AccountSummary.model_validate(resp.json())

    async def get_account_cash(self) -> Cash:
        """Contract drift: /equity/account/cash no longer exists in the
        bundle; cash is derived from the summary (docs/t212-contract-notes.md)."""
        return (await self.get_account_summary()).cash


def _safe_error(resp: httpx.Response) -> str:
    try:
        body: dict[str, Any] = resp.json()
        return str(body.get("errorMessage", "validation error"))
    except ValueError:
        return "validation error"
