"""Translation engine abstract interfaces."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TranslationRequest:
    """Standardized input for any translator."""
    text: str
    source_lang: str = "auto"          # e.g. "en", "zh", "auto"
    target_lang: str = "auto"          # e.g. "zh", "en", "auto"
    domain: str = "general"            # e.g. "legal", "medical", "it"
    style: str = "general"             # e.g. "formal", "fluent"
    glossary: list[dict] = field(default_factory=list)
    max_tokens: int = 0
    temperature: float = 0.3
    use_rag: bool = False              # 启用 RAG 检索增强
    rag_context: str = ""            # 预构建的 RAG 上下文（由服务层填充）


@dataclass
class TranslationResponse:
    """Standardized output from any translator."""
    text: str = ""
    source_lang: str = ""
    target_lang: str = ""
    engine: str = ""                   # e.g. "google", "youdao", "kimi"
    confidence: float = 0.0
    segments: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    rag_metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class Translator(ABC):
    """Abstract base for all translation engines.

    Implementations must be stateless (or thread-safe) so that a single
    instance can be reused across concurrent requests.
    """

    name: str = "base"

    @property
    @abstractmethod
    def available(self) -> bool:
        """Return True if this engine is configured and ready to use."""
        ...

    @property
    @abstractmethod
    def priority(self) -> int:
        """Execution priority in a fallback chain. Lower = higher priority."""
        ...

    @abstractmethod
    def translate(self, request: TranslationRequest) -> TranslationResponse:
        """Translate a single request. Must not raise; errors go into response.errors."""
        ...

    def health_check(self) -> bool:
        """Optional lightweight probe (e.g. ping API)."""
        return self.available
