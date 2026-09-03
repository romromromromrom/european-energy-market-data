from __future__ import annotations

import httpx


RETRYABLE = {408, 429, 500, 502, 503, 504}


def classify_error(exc: Exception) -> tuple[str, int | None]:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in RETRYABLE:
            return "RETRYABLE_REMOTE_ERROR", status
        if status in {401, 403}:
            return "ACCESS_RESTRICTED", status
        return "REMOTE_CLIENT_ERROR", status
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return "RETRYABLE_REMOTE_ERROR", None
    if isinstance(exc, (OSError, ValueError)):
        return "LOCAL_CLIENT_ERROR", None
    return "MANUAL_REVIEW", None
