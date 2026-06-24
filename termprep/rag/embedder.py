"""Medical embedder — wrapper for sentence-transformers / API embedding models.

Supports:
- BGE-M3 (local, recommended for medical CN-EN mixed text)
- OpenAI text-embedding-3-small (API, requires key)
- DeepSeek embedding (API)
- Fallback: if no model available, return zero vectors (graceful degradation)
"""

from __future__ import annotations

import hashlib
import os
import warnings
from typing import Any

import numpy as np


class MedicalEmbedder:
    """Embedding wrapper with fallback chain."""
    
    # Model priority: try local first, then API
    DEFAULT_LOCAL_MODEL = "BAAI/bge-m3"  # 1024-dim, good for CN-EN mixed
    FALLBACK_LOCAL_MODELS = [
        "BAAI/bge-large-zh-v1.5",
        "BAAI/bge-small-zh-v1.5",
    ]
    
    def __init__(
        self,
        model_name: str | None = None,      # "BAAI/bge-m3" or "openai" or "deepseek"
        device: str = "cpu",                   # "cpu" or "cuda"
        cache_dir: str | None = None,     # HF cache dir
    ):
        self.model_name = model_name or self.DEFAULT_LOCAL_MODEL
        self.device = device
        self.cache_dir = cache_dir
        
        self._model = None
        self._tokenizer = None
        self._embedding_fn = None
        self._dim = 1024  # default for BGE-M3
        
        self._load_model()
    
    def _load_model(self) -> None:
        """Try to load embedding model with fallback chain."""
        if self.model_name == "openai":
            self._load_openai()
        elif self.model_name == "deepseek":
            self._load_deepseek()
        else:
            self._load_local_model()
    
    def _load_local_model(self) -> None:
        """Load sentence-transformers model locally."""
        try:
            from sentence_transformers import SentenceTransformer
            
            model_path = self.model_name
            # Try primary model
            try:
                self._model = SentenceTransformer(
                    model_path,
                    device=self.device,
                    cache_folder=self.cache_dir,
                )
                self._dim = self._model.get_sentence_embedding_dimension()
                self._embedding_fn = self._model.encode
                return
            except Exception as e:
                warnings.warn(f"Failed to load {model_path}: {e}")
            
            # Try fallback models
            for fallback in self.FALLBACK_LOCAL_MODELS:
                try:
                    self._model = SentenceTransformer(
                        fallback,
                        device=self.device,
                        cache_folder=self.cache_dir,
                    )
                    self._dim = self._model.get_sentence_embedding_dimension()
                    self._embedding_fn = self._model.encode
                    self.model_name = fallback
                    warnings.warn(f"Using fallback model: {fallback}")
                    return
                except Exception:
                    continue
            
            # All local models failed
            raise RuntimeError("No local embedding model available")
            
        except ImportError:
            warnings.warn(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )
            self._embedding_fn = None
    
    def _load_openai(self) -> None:
        """Load OpenAI embedding API."""
        try:
            import openai
            api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("TERMPREP_AI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY not set")
            
            self._client = openai.OpenAI(api_key=api_key)
            self._dim = 1536  # text-embedding-3-small
            self._embedding_fn = self._embed_openai
            self.model_name = "openai"
        except Exception as e:
            warnings.warn(f"OpenAI embedding failed: {e}")
            self._embedding_fn = None
    
    def _load_deepseek(self) -> None:
        """Load DeepSeek embedding API."""
        try:
            import openai
            api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("TERMPREP_AI_API_KEY")
            base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
            if not api_key:
                raise ValueError("DEEPSEEK_API_KEY not set")
            
            self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
            self._dim = 1024  # assumed
            self._embedding_fn = self._embed_deepseek
            self.model_name = "deepseek"
        except Exception as e:
            warnings.warn(f"DeepSeek embedding failed: {e}")
            self._embedding_fn = None
    
    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        """Embed via OpenAI API."""
        # Batch size limit: 2048 for OpenAI
        batch_size = 100
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = self._client.embeddings.create(
                model="text-embedding-3-small",
                input=batch,
            )
            for item in response.data:
                all_embeddings.append(item.embedding)
        return all_embeddings
    
    def _embed_deepseek(self, texts: list[str]) -> list[list[float]]:
        """Embed via DeepSeek API."""
        batch_size = 100
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = self._client.embeddings.create(
                model="deepseek-embedding",
                input=batch,
            )
            for item in response.data:
                all_embeddings.append(item.embedding)
        return all_embeddings
    
    def encode(self, texts: str | list[str]) -> list[list[float]]:
        """Encode text(s) into embeddings."""
        if isinstance(texts, str):
            texts = [texts]
        
        if not texts or not any(t.strip() for t in texts):
            return [[0.0] * self._dim for _ in texts]
        
        if self._embedding_fn is None:
            # Fallback: hash-based deterministic embedding (not useful for similarity,
            # but prevents crashes during development)
            warnings.warn("No embedding model available. Using hash fallback.")
            return self._hash_fallback(texts)
        
        try:
            if self.model_name in ("openai", "deepseek"):
                embeddings = self._embedding_fn(texts)
            else:
                embeddings = self._embedding_fn(
                    texts,
                    convert_to_numpy=True,
                    normalize_embeddings=True,  # L2 normalize for cosine similarity
                    show_progress_bar=False,
                )
                # Convert numpy to list
                if hasattr(embeddings, 'tolist'):
                    embeddings = embeddings.tolist()
            
            return embeddings
        except Exception as e:
            warnings.warn(f"Embedding failed: {e}. Using fallback.")
            return self._hash_fallback(texts)
    
    def _hash_fallback(self, texts: list[str]) -> list[list[float]]:
        """Deterministic fallback embedding (not semantically meaningful, but stable)."""
        embeddings = []
        for text in texts:
            # Use hash to generate pseudo-random vector
            seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
            np.random.seed(seed)
            vec = np.random.randn(self._dim).astype(np.float32)
            vec = vec / np.linalg.norm(vec)  # normalize
            embeddings.append(vec.tolist())
        return embeddings
    
    @property
    def dimension(self) -> int:
        return self._dim
    
    @property
    def is_available(self) -> bool:
        return self._embedding_fn is not None


# Singleton instance (lazy-loaded)
_embedder_instance: MedicalEmbedder | None = None


def get_embedder() -> MedicalEmbedder:
    """Get or create the global embedder instance."""
    global _embedder_instance
    if _embedder_instance is None:
        # Try to infer model from env
        model = os.environ.get("RAG_EMBEDDER_MODEL", "BAAI/bge-m3")
        if model == "openai" or os.environ.get("OPENAI_API_KEY"):
            model = "openai"
        elif model == "deepseek" or os.environ.get("DEEPSEEK_API_KEY"):
            model = "deepseek"
        _embedder_instance = MedicalEmbedder(model_name=model)
    return _embedder_instance
