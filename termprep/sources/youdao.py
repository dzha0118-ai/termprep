"""Youdao Dictionary API integration.

Sign up for API keys at: https://ai.youdao.com/
Set env vars: TERMPREP_YOUDAO_KEY, TERMPREP_YOUDAO_SECRET

Note: appKey is the 应用ID, secret is the 密钥 (NOT the API Key field).
"""

import hashlib
import time
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
            List of SearchResult with translations, examples, web references.
        """
        if not self.available:
            return []

        data = self._call_api(term)
        if not data or data.get("errorCode") != "0":
            return []

        results: list[SearchResult] = []

        # §5.5 — gather vocabulary-level signals BEFORE building the translation
        # entries, so their ``definition`` carries a real gloss (phonetic + 释义).
        # The old code used ``definition=data.get("query", term)``, i.e. it echoed
        # the search term — which is exactly why the reported payload had
        # ``definition`` identical to nothing useful and no 释义/例句 at all.
        raw_basic = data.get("basic", {})
        basic: dict[str, Any] = raw_basic if isinstance(raw_basic, dict) else {}
        explains = [str(e) for e in (basic.get("explains") or [])]
        us_phonetic = basic.get("us-phonetic", "") or ""
        uk_phonetic = basic.get("uk-phonetic", "") or ""
        phonetic = f"/{us_phonetic or uk_phonetic}/" if (us_phonetic or uk_phonetic) else ""
        gloss = "; ".join(explains[:3])

        def _translation_definition() -> str:
            return " ".join(p for p in (phonetic, gloss) if p).strip()

        # Translation
        translation = data.get("translation", [])
        for t in translation[:limit]:
            results.append(SearchResult(
                query=term,
                word=t,
                word_type="translation",
                source="youdao",
                score=0.95,
                definition=_translation_definition(),
            ))

        # Basic dictionary entries (释义 / 音标)
        if basic:
            for exp in explains[:limit]:
                results.append(SearchResult(
                    query=term,
                    word=exp,
                    word_type="explanation",
                    source="youdao",
                    score=0.85,
                    definition=phonetic,
                ))
            if us_phonetic:
                results.append(SearchResult(
                    query=term, word=us_phonetic, word_type="phonetic",
                    source="youdao", score=0.7, definition="US"
                ))
            if uk_phonetic:
                results.append(SearchResult(
                    query=term, word=uk_phonetic, word_type="phonetic",
                    source="youdao", score=0.7, definition="UK"
                ))

        # Web references with examples
        web = data.get("web", [])
        for item in web[:limit]:
            word = item.get("key", "")
            values = item.get("value", [])
            for v in values[:3]:
                results.append(SearchResult(
                    query=term,
                    word=word,
                    word_type="collocation",
                    source="youdao",
                    score=0.7,
                    definition=v,
                ))

        return results

    def _call_api(self, term: str) -> dict[str, Any]:
        """Call Youdao API with proper signature (v3 + curtime)."""
        try:
            import requests

            salt = str(uuid.uuid4())
            curtime = str(int(time.time()))
            sign_str = self.api_key + term + salt + curtime + self.api_secret
            sign = hashlib.sha256(sign_str.encode()).hexdigest()

            params = {
                "q": term,
                "from": "auto",
                "to": "auto",
                "appKey": self.api_key,
                "salt": salt,
                "sign": sign,
                "signType": "v3",
                "curtime": curtime,
            }
            resp = requests.get(YOUDAO_API_URL, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data
            return {}
        except Exception:
            return {}
