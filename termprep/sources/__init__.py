"""Online dictionary source integrations."""

from termprep.sources.base import SearchResult, DictSource
from termprep.sources.youdao import YoudaoSource
from termprep.sources.webster import WebsterSource

__all__ = ["SearchResult", "DictSource", "YoudaoSource", "WebsterSource"]


def get_available_sources() -> list[DictSource]:
    """Return all available and configured dictionary sources."""
    sources: list[DictSource] = []
    sources.append(YoudaoSource())
    sources.append(WebsterSource())
    return sources
