"""One shared AsyncClient (connection reuse) plus retry on transient
failures for idempotent calls. Closed in main.py's lifespan."""

import asyncio

import httpx

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def post_with_retry(url: str, *, retries: int = 2, **kwargs) -> httpx.Response:
    """Retries connect errors, timeouts and 5xx with backoff. ONLY use for
    reads / validations - never for the booking POST itself."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await client().post(url, **kwargs)
            if resp.status_code < 500:
                return resp
            last = httpx.HTTPStatusError("server error", request=resp.request, response=resp)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last = exc
        if attempt < retries:
            await asyncio.sleep(0.3 * (2 ** attempt))
    assert last is not None
    raise last
