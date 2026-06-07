"""Merriam-Webster Dictionary API integration.

Sign up for API keys at: https://dictionaryapi.com/
Set env var: TERMPREP_WEBSTER_KEY
"""

from typing import Any

from termprep.sources.base import DictSource, SearchResult


WEBSTER_API_URL = "https://www.dictionaryapi.com/api/v3/references/collegiate/json/"


class WebsterSource(DictSource):
    """Merriam-Webster Collegiate Dictionary API source."""

    name = "webster"

    def search(self, term: str, limit: int = 10) -> list[SearchResult]:
        """Search an English word via Merriam-Webster API.

        Args:
            term: English word to look up.
            limit: Max results.

        Returns:
            List of SearchResult with definitions, synonyms, antonyms.
        """
        if not self.available:
            return []

        data = self._call_api(term)
        if not data:
            return []

        results: list[SearchResult] = []

        for entry in data[:1]:  # Usually one entry per word
            if not isinstance(entry, dict):
                continue

            # Short definitions
            shortdefs = entry.get("shortdef", [])
            for d in shortdefs[:limit]:
                results.append(SearchResult(
                    query=term,
                    word=d,
                    word_type="definition",
                    source="webster",
                    score=0.9,
                    definition=d,
                ))

            # Synonyms & antonyms from thesaurus
            meta = entry.get("meta", {})
            stems = meta.get("stems", [])
            if stems:
                for s in stems[:limit]:
                    results.append(SearchResult(
                        query=term,
                        word=s,
                        word_type="root",
                        source="webster",
                        score=0.6,
                    ))

            # Synonyms from thesaurus references
            syns = entry.get("meta", {}).get("syns", [])
            if syns:
                for group in syns:
                    for syn in group[:limit]:
                        results.append(SearchResult(
                            query=term,
                            word=syn,
                            word_type="synonym",
                            source="webster",
                            score=0.75,
                        ))

        return results

    def _call_api(self, term: str) -> list[Any]:
        """Call Merriam-Webster API."""
        try:
            import requests

            url = f"{WEBSTER_API_URL}{term}?key={self.api_key}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception:
            return []
