# Centralized configuration for TermPrep.
#
# Uses environment variables with TERMPREP_ prefix.
# Automatically loads .env file from project root if present.
#
# Environment variables (prefix TERMPREP_):
#   TERMPREP_AI_PROVIDER      → ai_provider
#   TERMPREP_AI_API_KEY       → ai_api_key
#   TERMPREP_AI_MODEL         → ai_model
#   TERMPREP_AI_BASE_URL      → ai_base_url
#   TERMPREP_AI_TEMPERATURE   → ai_temperature
#   TERMPREP_YOUDAO_KEY       → youdao_key
#   TERMPREP_YOUDAO_SECRET    → youdao_secret
#   TERMPREP_WEBSTER_KEY      → webster_key
#   TERMPREP_DB_DIR           → db_dir
#   TERMPREP_TRANSLATION_CACHE → translation_cache (bool)

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


# ── Auto-load .env file ──

def _load_dotenv() -> None:
    """Load .env file from project root into os.environ."""
    # Find project root: termprep/config.py → parent is project root
    proj_root = Path(__file__).parent.parent
    env_path = proj_root / ".env"
    if not env_path.exists():
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                # Only set if not already set by real env var
                os.environ.setdefault(key, val)
    except Exception:
        pass


_load_dotenv()


def _env_bool(val: str | None) -> bool:
    if val is None:
        return False
    return val.lower() in ("1", "true", "yes", "on")


class TermPrepConfig:
    """Configuration container. Use as singleton: from termprep.config import settings"""

    def __init__(self) -> None:
        # AI / LLM
        self.ai_provider: str = os.environ.get("TERMPREP_AI_PROVIDER", "")
        self.ai_api_key: str = os.environ.get("TERMPREP_AI_API_KEY", "")
        self.ai_model: str = os.environ.get("TERMPREP_AI_MODEL", "")
        self.ai_base_url: str = os.environ.get("TERMPREP_AI_BASE_URL", "")
        self.ai_temperature: float = float(os.environ.get("TERMPREP_AI_TEMPERATURE", "0.3"))
        self.ai_max_tokens: int = int(os.environ.get("TERMPREP_AI_MAX_TOKENS", "4096"))
        self.ai_timeout: int = int(os.environ.get("TERMPREP_AI_TIMEOUT", "60"))
        self.ai_priority: int = int(os.environ.get("TERMPREP_AI_PRIORITY", "5"))

        # Youdao
        self.youdao_key: str = os.environ.get("TERMPREP_YOUDAO_KEY", "")
        self.youdao_secret: str = os.environ.get("TERMPREP_YOUDAO_SECRET", "")
        self.youdao_priority: int = int(os.environ.get("TERMPREP_YOUDAO_PRIORITY", "20"))
        self.youdao_timeout: int = int(os.environ.get("TERMPREP_YOUDAO_TIMEOUT", "10"))

        # Google
        self.google_priority: int = int(os.environ.get("TERMPREP_GOOGLE_PRIORITY", "10"))
        self.google_timeout: int = int(os.environ.get("TERMPREP_GOOGLE_TIMEOUT", "3"))

        # Paths
        self.db_dir: Path = Path(os.environ.get("TERMPREP_DB_DIR", Path(__file__).parent.parent / "data"))

        # Features
        self.translation_cache: bool = _env_bool(os.environ.get("TERMPREP_TRANSLATION_CACHE"))
        self.max_fallbacks: int = int(os.environ.get("TERMPREP_MAX_FALLBACKS", "3"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ai_provider": self.ai_provider,
            "ai_api_key": self.ai_api_key,
            "ai_model": self.ai_model,
            "ai_base_url": self.ai_base_url,
            "ai_temperature": self.ai_temperature,
            "ai_max_tokens": self.ai_max_tokens,
            "ai_timeout": self.ai_timeout,
            "ai_priority": self.ai_priority,
            "youdao_key": self.youdao_key,
            "youdao_secret": self.youdao_secret,
            "youdao_priority": self.youdao_priority,
            "youdao_timeout": self.youdao_timeout,
            "google_priority": self.google_priority,
            "google_timeout": self.google_timeout,
            "db_dir": str(self.db_dir),
            "translation_cache": self.translation_cache,
            "max_fallbacks": self.max_fallbacks,
        }


# Global singleton — imported by engines and services
settings = TermPrepConfig()
