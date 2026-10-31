"""The only network client for non-Anthropic hosts.

Guarantees: per-host rate limiting, strictly serial requests, 429/Retry-After
and 5xx backoff with jitter, a hard stop after repeated failures, an on-disk
SQLite response cache so no query is ever repeated, and a User-Agent that
identifies the project and a contact address.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

DEFAULT_RATE = 1.0  # requests per second for hosts not listed
RETRYABLE = {429, 500, 502, 503, 504}


class RateLimiter:
    """Per-host minimum spacing between requests (a 1-token bucket)."""

    def __init__(
        self,
        rates: Mapping[str, float] | None = None,
        default_rate: float = DEFAULT_RATE,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rates = dict(rates or {})
        self.default_rate = default_rate
        self._clock = clock
        self._sleep = sleeper
        self._next_allowed: dict[str, float] = {}
        self.slept_total = 0.0

    def interval(self, host: str) -> float:
        return 1.0 / self.rates.get(host, self.default_rate)

    def acquire(self, host: str) -> None:
        now = self._clock()
        allowed = self._next_allowed.get(host, now)
        if allowed > now:
            wait = allowed - now
            self.slept_total += wait
            self._sleep(wait)
            now = allowed
        self._next_allowed[host] = now + self.interval(host)


@dataclass
class Response:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    from_cache: bool
    fetched_at: float

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def require_ok(self, what: str) -> "Response":
        """Raise unless 2xx. A failed page must never read as an empty one."""
        if not self.ok:
            raise HttpError(f"{what} failed: HTTP {self.status} ({self.url})")
        return self


class HttpError(RuntimeError):
    """A request that came back non-2xx after the client's own retries."""


class HttpCache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        # The cache can be shared with a dev tool running alongside a command;
        # without a busy timeout the second connection raises "database is
        # locked" and aborts the run.
        self._db.execute("PRAGMA busy_timeout = 30000")
        # DELETE only moves pages to a freelist, so the file never shrinks.
        # Enabling incremental vacuum needs one VACUUM to take effect, which is
        # cheap at tens of megabytes and unpleasant at gigabytes, so do it now.
        if self._db.execute("PRAGMA auto_vacuum").fetchone()[0] == 0:
            self._db.execute("PRAGMA auto_vacuum = INCREMENTAL")
            self._db.execute("VACUUM")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses ("
            "key TEXT PRIMARY KEY, url TEXT, status INTEGER, headers TEXT, "
            "body BLOB, fetched_at REAL)"
        )
        self._db.commit()

    @staticmethod
    def key(method: str, url: str, body: bytes | None) -> str:
        h = hashlib.sha256()
        h.update(method.encode())
        h.update(b"\0")
        h.update(url.encode())
        h.update(b"\0")
        if body:
            h.update(body)
        return h.hexdigest()

    def get(self, key: str, ttl: float | None) -> Response | None:
        row = self._db.execute(
            "SELECT url, status, headers, body, fetched_at FROM responses WHERE key=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        url, status, headers, body, fetched_at = row
        if ttl is not None and time.time() - fetched_at > ttl:
            return None
        return Response(url, status, json.loads(headers), body, True, fetched_at)

    def put(self, key: str, resp: Response) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,?)",
            (key, resp.url, resp.status, json.dumps(resp.headers), resp.body, resp.fetched_at),
        )
        self._db.commit()


class ResponseTooLarge(RuntimeError):
    """A streamed body exceeded the caller's byte cap."""


class DownloadTimeout(RuntimeError):
    """A streamed body was still arriving past the caller's total deadline."""


class TooManyFailures(RuntimeError):
    pass


class PoliteClient:
    """Serial, rate-limited, cached HTTP client."""

    def __init__(
        self,
        user_agent: str,
        cache_path: Path,
        rates: Mapping[str, float] | None = None,
        max_attempts: int = 5,
        timeout: float = 60.0,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.cache = HttpCache(cache_path)
        self.limiter = limiter or RateLimiter(rates)
        self.max_attempts = max_attempts
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            timeout=timeout,
            follow_redirects=True,
        )
        self.network_requests = 0
        self.cache_hits = 0

    def close(self) -> None:
        self._http.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        ttl: float | None = None,
        cacheable: bool = True,
    ) -> Response:
        req = self._http.build_request(method, url, params=params, content=data, headers=headers)
        full_url = str(req.url)
        key = HttpCache.key(method, full_url, data)
        if cacheable:
            cached = self.cache.get(key, ttl)
            if cached is not None:
                self.cache_hits += 1
                return cached
        host = urlsplit(full_url).hostname or ""
        failures = 0
        while True:
            self.limiter.acquire(host)
            self.network_requests += 1
            try:
                r = self._http.send(req)
            except httpx.HTTPError as e:
                failures += 1
                log.warning("%s %s: %s (attempt %d)", method, full_url, e, failures)
                if failures >= self.max_attempts:
                    raise TooManyFailures(f"{method} {full_url}: {e}") from e
                self.limiter._sleep(self._backoff(failures, None))
                continue
            if r.status_code in RETRYABLE:
                failures += 1
                retry_after = r.headers.get("Retry-After")
                log.warning("%s %s -> %d (attempt %d)", method, full_url, r.status_code, failures)
                if failures >= self.max_attempts:
                    raise TooManyFailures(f"{method} {full_url}: HTTP {r.status_code}")
                self.limiter._sleep(self._backoff(failures, retry_after))
                continue
            resp = Response(full_url, r.status_code, dict(r.headers), r.content, False, time.time())
            if cacheable and r.status_code < 500:
                self.cache.put(key, resp)
            return resp

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return float(retry_after) + random.uniform(0, 1)
            except ValueError:
                pass
        return min(60.0, (2 ** attempt) + random.uniform(0, 1))

    def get(self, url: str, **kw) -> Response:
        return self.request("GET", url, **kw)

    def download(
        self,
        url: str,
        *,
        max_bytes: int,
        deadline_s: float = 120.0,
        params: Mapping[str, str] | None = None,
        rate_key: str | None = None,
        read_timeout: float | None = None,
        max_attempts: int | None = None,
    ) -> Response:
        """Stream a large binary body, bounded by size and by wall clock.

        `request()` buffers the whole response, and its httpx timeout applies to
        each individual read rather than the transfer as a whole, so a server
        that dribbles bytes holds the connection open indefinitely. Some Europe
        PMC supplement bundles do exactly that: about 3 MB arrives and the
        transfer never finishes. A per-read timeout cannot catch that; a total deadline
        can, and streaming also lets an oversized body be abandoned rather than
        read into memory and then discarded.

        `rate_key` rate-limits this call under its own bucket instead of the
        hostname's. Some endpoints do real work per request -- Europe PMC builds
        the supplement zip on demand -- and deserve a far slower rate than the
        rest of the host.

        Raises `TooManyFailures` after `max_attempts` network errors or retryable
        statuses, `ResponseTooLarge` past `max_bytes`, and `DownloadTimeout` past
        `deadline_s`. Never cached: these bodies are large and the caller stores
        what it keeps.
        """
        req = self._http.build_request("GET", url, params=params)
        full_url = str(req.url)
        bucket = rate_key or (urlsplit(full_url).hostname or "")
        timeout = httpx.Timeout(read_timeout) if read_timeout else httpx.USE_CLIENT_DEFAULT
        attempts = max_attempts or self.max_attempts
        failures = 0
        while True:
            self.limiter.acquire(bucket)
            self.network_requests += 1
            started = time.time()
            chunks: list[bytes] = []
            total = 0
            try:
                with self._http.stream("GET", full_url, timeout=timeout) as r:
                    if r.status_code in RETRYABLE:
                        retry_after = r.headers.get("Retry-After")
                        r.close()
                        failures += 1
                        log.warning("GET %s -> %d (attempt %d)", full_url, r.status_code, failures)
                        if failures >= attempts:
                            raise TooManyFailures(f"GET {full_url}: HTTP {r.status_code}")
                        self.limiter._sleep(self._backoff(failures, retry_after))
                        continue
                    if r.status_code >= 400:
                        r.close()
                        return Response(full_url, r.status_code, dict(r.headers), b"", False, time.time())
                    for chunk in r.iter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total > max_bytes:
                            raise ResponseTooLarge(f"GET {full_url}: over {max_bytes} bytes")
                        if time.time() - started > deadline_s:
                            raise DownloadTimeout(
                                f"GET {full_url}: still receiving after {deadline_s:.0f}s ({total} bytes)")
                    status, headers = r.status_code, dict(r.headers)
            except httpx.HTTPError as e:
                failures += 1
                log.warning("GET %s: %s (attempt %d)", full_url, e, failures)
                if failures >= attempts:
                    raise TooManyFailures(f"GET {full_url}: {e}") from e
                self.limiter._sleep(self._backoff(failures, None))
                continue
            return Response(full_url, status, headers, b"".join(chunks), False, time.time())
