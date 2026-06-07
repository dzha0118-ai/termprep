"""Online dictionary source integrations."""

from termprep.sources.base import SearchResult, DictSource
from termprep.sources.youdao import YoudaoSource
from termprep.sources.webster import WebsterSource
from termprep.sources.wikipedia import WikipediaSource

__all__ = ["SearchResult", "DictSource", "YoudaoSource", "WebsterSource",
           "WikipediaSource"]


def get_available_sources() -> list[DictSource]:
    """Return all available and configured dictionary sources."""
    sources: list[DictSource] = []
    sources.append(YoudaoSource())
    sources.append(WebsterSource())
    sources.append(WikipediaSource())
    return sources
