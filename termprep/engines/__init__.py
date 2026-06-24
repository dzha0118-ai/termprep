"""Translation engines registry."""

# Import to trigger registration
from termprep.engines import google, youdao, ai_translator
from termprep.engines.base import (
    register_engine,
    get_engine,
    list_engines,
    build_available_engines,
)

__all__ = [
    "register_engine",
    "get_engine",
    "list_engines",
    "build_available_engines",
    "google",
    "youdao",
    "ai_translator",
]
