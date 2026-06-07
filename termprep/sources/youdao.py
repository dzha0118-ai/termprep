"""Youdao Dictionary API integration.

Sign up for API keys at: https://ai.youdao.com/
Set env vars: TERMPREP_YOUDAO_KEY, TERMPREP_YOUDAO_SECRET
"""

import hashlib
import uuid
from typing import Any

from termprep.sources.base import DictSource, SearchResult


YOUDAO_API_URL = "https://openapi.youdao.com/api"


class YoudaoSource(DictSource):
    """Youdao Fanyi / Dictionary API source."""

    name = "youdao"

    def search(self, term: str, limit: int = 10) -> list[SearchResult]:
        """Search a term via Youdao API.

        Args:
            term: The word/phrase to look up.
            limit: Max results.

        Returns:
            List of SearchResult with translations, collocations, examples.
        """
        if not self.available:
            return []

        data = self._call_api(term)
        if not data:
            return []

        results: list[SearchResult] = []

        # Basic translation
        translation = data.get("translation", [])
        for t in translation[:limit]:
            results.append(SearchResult(
                query=term,
                word=t,
                word_type="translation",
                source="youdao",
                score=0.95,
                definition=data.get("query", term),
            ))

        # Web references with examples
        web = data.get("web", [])
        for item in web[:limit]:
            word = item.get("key", "")
            values = item.get("value", [])
            for v in values:
                results.append(SearchResult(
                    query=term,
                    word=word,
                    word_type="collocation",
                    source="youdao",
                    score=0.7,
                    definition=v,
                ))

        # Basic dictionary explanation
        basic = data.get("basic", {})
        explains = basic.get("explains", [])
        for exp in explains[:limit]:
            results.append(SearchResult(
                query=term,
                word=exp,
                word_type="explanation",
                source="youdao",
                score=0.85,
                definition=term,
            ))

        return results

    def _call_api(self, term: str) -> dict[str, Any]:
        """Call Youdao API with proper signature."""
        try:
            import requests

            salt = str(uuid.uuid4())
            sign_str = self.api_key + term + salt + self.api_secret
            sign = hashlib.sha256(sign_str.encode()).hexdigest()

            params = {
                "q": term,
                "from": "auto",
                "to": "auto",
                "appKey": self.api_key,
                "salt": salt,
                "sign": sign,
                "signType": "v3",
                "dicts": '{"count": 5, "dicts": [["ec", "ce", "ee"]]}',
            }
            resp = requests.get(YOUDAO_API_URL, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return {}
        except Exception:
            return {}
