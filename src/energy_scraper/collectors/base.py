from __future__ import annotations

import httpx

from energy_scraper.core.config import Settings
from energy_scraper.core.retry_policy import with_retry


class ApiCollector:
    def __init__(self, settings: Settings):
        self.settings = settings

    def get_json(self, url: str, params: dict[str, str], headers: dict[str, str]) -> tuple[dict, str]:
        def request() -> httpx.Response:
            response = httpx.get(url, params=params, headers=headers, timeout=self.settings.timeout, follow_redirects=True)
            response.raise_for_status()
            return response

        response = with_retry(request, self.settings.max_attempts)
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
        return payload, str(response.url)
