"""Wikipedia API source for concept explanations.

No API key required. Fetches article summaries from Wikipedia.
"""

from typing import Any

from termprep.sources.base import DictSource, SearchResult


WIKIPEDIA_API = "https://en.wikipedia.org/api/rest_v1/page/summary"
WIKIPEDIA_API_ZH = "https://zh.wikipedia.org/api/rest_v1/page/summary"


class WikipediaSource(DictSource):
    """Wikipedia article summary source. No API key needed."""

    name = "wikipedia"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None):
        # Wikipedia is always "available" (no API key required)
        super().__init__(api_key="wikipedia-free", api_secret="")

    def search(self, term: str, limit: int = 10) -> list[SearchResult]:
        """Search Wikipedia for a term and return article summaries.

        Args:
            term: The concept/term to look up.
            limit: Max results.

        Returns:
            List of SearchResult with Wikipedia summaries.
        """
        results: list[SearchResult] = []

        # Try English first, then Chinese
        for lang, api_url in [("en", WIKIPEDIA_API), ("zh", WIKIPEDIA_API_ZH)]:
            try:
                summary = self._fetch_summary(api_url, term)
                if summary:
                    results.append(SearchResult(
                        query=term,
                        word=summary.get("title", term),
                        word_type="concept",
                        source=f"wikipedia-{lang}",
                        score=0.8,
                        definition=summary.get("extract", ""),
                    ))
                    if len(results) >= limit:
                        break
            except Exception:
                continue

        return results

    def _fetch_summary(self, api_url: str, term: str) -> dict[str, Any] | None:
        """Fetch article summary from Wikipedia REST API."""
        import requests

        # URL-encode the term
        import urllib.parse
        encoded = urllib.parse.quote(term.strip())

        url = f"{api_url}/{encoded}"
        headers = {
            "User-Agent": "TermPrep/0.4 (translation tool; for educational use)"
        }

        try:
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("type") != "disambiguation" and data.get("extract"):
                    return data
            return None
        except Exception:
            return None
