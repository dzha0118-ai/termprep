"""Base classes for dictionary/data sources."""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SearchResult:
    """A single search result from a dictionary source."""
    query: str
    word: str
    word_type: str = ""  # synonym, antonym, collocation, translation, example
    source: str = ""      # youdao, webster, local
    score: float = 1.0
    definition: str = ""  # optional definition/explanation


class DictSource(ABC):
    """Abstract base class for dictionary API sources."""

    name: str = "base"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None):
        self.api_key = api_key or self._env_key()
        self.api_secret = api_secret or self._env_secret()

    def _env_key(self) -> str:
        """Read API key from environment variable."""
        env_name = f"TERMPREP_{self.name.upper()}_KEY"
        return os.environ.get(env_name, "")

    def _env_secret(self) -> str:
        """Read API secret from environment variable."""
        env_name = f"TERMPREP_{self.name.upper()}_SECRET"
        return os.environ.get(env_name, "")

    @property
    def available(self) -> bool:
        """Check if this source is configured and available."""
        return bool(self.api_key)

    @abstractmethod
    def search(self, term: str, limit: int = 10) -> list[SearchResult]:
        """Search a term and return results."""
        ...
