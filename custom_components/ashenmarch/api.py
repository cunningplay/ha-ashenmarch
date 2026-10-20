"""The Ashenmarch API, as a personal access token sees it (ASH-3745).

Everything that talks to api.ashenmarch.com goes through here, so the rest of the
integration never touches the token. The token is never logged: only the path and
status of a request are.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import aiohttp

from .const import API_BASE, VERSION

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)


class AshenmarchError(Exception):
    """The API couldn't be reached or answered with an error."""


class AshenmarchAuthError(AshenmarchError):
    """401: the token is wrong, revoked, or its account is gone."""


class AshenmarchRateLimited(AshenmarchError):
    """429, or a 5xx: try again later."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AshenmarchNotAvailable(AshenmarchError):
    """404: the server doesn't have this route (yet)."""


@dataclass
class Fetched:
    """A conditional GET: data is None when the server said 304."""

    data: Any
    etag: str | None
    not_modified: bool = False


class AshenmarchApi:
    """A thin client for the five routes a personal access token may read."""

    def __init__(
        self, session: aiohttp.ClientSession, token: str, base_url: str = API_BASE
    ) -> None:
        self._session = session
        self._token = token
        self._base = base_url.rstrip("/")

    def _headers(self, etag: str | None = None, accept: str = "application/json") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
            "User-Agent": f"ha-ashenmarch/{VERSION}",
        }
        if etag:
            headers["If-None-Match"] = etag
        return headers

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        etag: str | None = None,
        binary: bool = False,
    ) -> Fetched:
        url = f"{self._base}{path}"
        try:
            async with self._session.get(
                url,
                params=params,
                headers=self._headers(etag, "image/png" if binary else "application/json"),
                timeout=TIMEOUT,
            ) as resp:
                _LOGGER.debug("GET %s -> %s", path, resp.status)
                new_etag = resp.headers.get("ETag") or etag
                if resp.status == 304:
                    return Fetched(None, new_etag, not_modified=True)
                if resp.status == 401:
                    raise AshenmarchAuthError(await _error_code(resp))
                if resp.status == 404:
                    raise AshenmarchNotAvailable(path)
                if resp.status == 429 or resp.status >= 500:
                    raise AshenmarchRateLimited(
                        f"{path}: HTTP {resp.status}", _retry_after(resp.headers.get("Retry-After"))
                    )
                if resp.status >= 400:
                    raise AshenmarchError(f"{path}: HTTP {resp.status} {await _error_code(resp)}")
                data = await resp.read() if binary else await resp.json()
                return Fetched(data, new_etag)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise AshenmarchError(f"{path}: {type(err).__name__}") from err

    async def get_me(self) -> dict[str, Any]:
        """The player's profile basics (id, house_name, locale, ...)."""
        return (await self._get("/players/me")).data

    async def get_holdings(self, etag: str | None = None) -> Fetched:
        """The player's holdings, soonest decay first (ASH-3754); 304 while unchanged."""
        return await self._get("/players/me/holdings", etag=etag)

    async def get_rank(self) -> dict[str, Any]:
        """Rank progress: points, fraction to the next rank, the ladder with display names."""
        return (await self._get("/players/me/rank")).data

    async def get_wrist_summary(self, etag: str | None = None) -> Fetched:
        """The tiny realm summary (ASH-3709); 304 while the realm is unchanged."""
        return await self._get("/players/me/wrist-summary", etag=etag)

    async def get_events(self, since: str | None, limit: int) -> dict[str, Any]:
        """One page of realm events after the cursor (ASH-3746): {events, next}."""
        params: dict[str, Any] = {"limit": limit}
        if since:
            params["since"] = since
        return (await self._get("/players/me/events", params=params)).data

    async def get_coat_of_arms(self, etag: str | None = None) -> Fetched:
        """The player's arms as a PNG (banner style); 304 while unchanged."""
        return await self._get(
            "/players/me/coat-of-arms.png", params={"style": "banner"}, etag=etag, binary=True
        )


async def _error_code(resp: aiohttp.ClientResponse) -> str:
    try:
        body = await resp.json(content_type=None)
    except (ValueError, aiohttp.ClientError):
        return ""
    return str(body.get("error", "")) if isinstance(body, dict) else ""


def _retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None
