from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from .error_classifier import classify_error

T = TypeVar("T")


def with_retry(operation: Callable[[], T], attempts: int = 3, base_delay: float = 0.5) -> T:
    """Retry only transient remote errors, respecting Retry-After when available."""
    last: Exception | None = None
    for attempt in range(1, max(3, attempts) + 1):
        try:
            return operation()
        except Exception as exc:
            last = exc
            kind, _ = classify_error(exc)
            if kind != "RETRYABLE_REMOTE_ERROR" or attempt >= max(3, attempts):
                raise
            retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("retry-after")
            try:
                delay = float(retry_after) if retry_after else base_delay * (2 ** (attempt - 1)) + random.random() * 0.25
            except ValueError:
                delay = base_delay * (2 ** (attempt - 1))
            time.sleep(min(delay, 30.0))
    assert last is not None
    raise last
