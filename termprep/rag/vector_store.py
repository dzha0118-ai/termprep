"""Medical vector store — ChromaDB wrapper with medical-specific collections.

Collections:
- medical_terms: 术语表（精确匹配 + 向量检索）
- medical_corpus: 语料库（向量检索）
- medical_documents: 文档元数据（不存储向量）
"""

from __future__ import annotations

import os
import warnings
from typing import Any

from .medical_schema import (
    CorpusChunk,
    Document,
    MedicalDomain,
    MedicalTerm,
    RetrievalResult,
    TermStatus,
)
from .embedder import get_embedder


# Default Chroma persist directory
DEFAULT_PERSIST_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "chroma_db")


class MedicalVectorStore:
    """ChromaDB wrapper for medical RAG data."""
    
    COLLECTION_TERMS = "medical_terms"
    COLLECTION_CORPUS = "medical_corpus"
    COLLECTION_DOCUMENTS = "medical_documents"
    
    def __init__(
        self,
        persist_dir: str | None = None,
        embedding_dimension: int = 1024,
    ):
        self.persist_dir = persist_dir or os.path.abspath(DEFAULT_PERSIST_DIR)
        self.embedding_dimension = embedding_dimension
        
        self._client = None
        self._terms_collection = None
        self._corpus_collection = None
        self._documents_collection = None
        
        self._init_chroma()
    
    def _init_chroma(self) -> None:
        """Initialize ChromaDB client and collections."""
        try:
            import chromadb
            from chromadb.config import Settings
            
            self._client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                ),
            )
            
            # Create/get collections with metadata
            self._terms_collection = self._client.get_or_create_collection(
                name=self.COLLECTION_TERMS,
                metadata={"hnsw:space": "cosine"},
            )
            self._corpus_collection = self._client.get_or_create_collection(
                name=self.COLLECTION_CORPUS,
                metadata={"hnsw:space": "cosine"},
            )
            self._documents_collection = self._client.get_or_create_collection(
                name=self.COLLECTION_DOCUMENTS,
                metadata={"hnsw:space": "cosine"},
            )
            
        except ImportError:
            warnings.warn(
                "chromadb not installed. Medical RAG will be disabled. "
                "Install with: pip install chromadb"
            )
            self._client = None
    
    # ── Terms (Glossary) ──
    
    def add_terms(self, terms: list[MedicalTerm]) -> None:
        """Add medical terms to the vector store."""
        if not self._client or not terms:
            return
        
        embedder = get_embedder()
        
        ids = []
        documents = []
        embeddings = []
        metadatas = []
        
        for term in terms:
            text = term.to_vector_text()
            emb = embedder.encode([text])[0]
            
            ids.append(f"term_{term.source}_{term.target}")
            documents.append(text)
            embeddings.append(emb)
            metadatas.append({
                "source": term.source,
                "target": term.target,
                "source_text": term.source,
                "target_text": term.target,
                "domain": term.domain.value,
                "abbreviation": term.abbreviation,
                "status": term.status.value,
                "frequency": term.frequency,
                "score": term.score,
            })
        
        self._terms_collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
    
    def search_terms(
        self,
        query: str,
        n_results: int = 10,
        domain: MedicalDomain | None = None,
        status: TermStatus | None = None,
    ) -> list[RetrievalResult]:
        """Semantic search for terms."""
        if not self._client:
            return []
        
        embedder = get_embedder()
        query_embedding = embedder.encode([query])[0]
        
        where_clause = {}
        if domain:
            where_clause["domain"] = domain.value
        if status:
            where_clause["status"] = status.value
        
        results = self._terms_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where_clause if where_clause else None,
            include=["documents", "metadatas", "distances"],
        )
        
        return self._parse_results(results, "term_fuzzy")
    
    def get_term_by_exact(self, source: str) -> MedicalTerm | None:
        """Exact match by source term."""
        if not self._client:
            return None
        
        results = self._terms_collection.get(
            where={"source": {"$eq": source}},
            include=["documents", "metadatas"],
        )
        
        if not results["ids"]:
            return None
        
        meta = results["metadatas"][0]
        return MedicalTerm(
            source=meta["source"],
            target=meta["target"],
            domain=MedicalDomain(meta.get("domain", "general")),
            abbreviation=meta.get("abbreviation", ""),
            status=TermStatus(meta.get("status", "extracted")),
            frequency=meta.get("frequency", 0),
            score=meta.get("score", 0.0),
        )
    
    # ── Corpus (Chunks) ──
    
    def add_corpus_chunks(self, chunks: list[CorpusChunk]) -> None:
        """Add corpus chunks to the vector store."""
        if not self._client or not chunks:
            return
        
        embedder = get_embedder()
        
        ids = []
        documents = []
        embeddings = []
        metadatas = []
        
        for chunk in chunks:
            text = chunk.to_embedding_text()
            emb = embedder.encode([text])[0]
            
            ids.append(chunk.id)
            documents.append(text)
            embeddings.append(emb)
            metadatas.append({
                "chunk_type": chunk.chunk_type.value,
                "language": chunk.language,
                "domain": chunk.domain.value,
                "source_document": chunk.source_document,
                "source_file": chunk.source_file,
                "position": chunk.position,
                "source_text": chunk.source_text,
                "target_text": chunk.target_text,
                "quality_score": chunk.quality_score,
            })
        
        self._corpus_collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )
    
    def search_corpus(
        self,
        query: str,
        n_results: int = 5,
        domain: MedicalDomain | None = None,
        source_document: str | None = None,
        min_quality: float = 0.0,
    ) -> list[RetrievalResult]:
        """Semantic search for corpus chunks."""
        if not self._client:
            return []
        
        embedder = get_embedder()
        query_embedding = embedder.encode([query])[0]
        
        where_clause = {}
        if domain:
            where_clause["domain"] = domain.value
        if source_document:
            where_clause["source_document"] = source_document
        if min_quality > 0:
            where_clause["quality_score"] = {"$gte": min_quality}
        
        results = self._corpus_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where_clause if where_clause else None,
            include=["documents", "metadatas", "distances"],
        )
        
        return self._parse_results(results, "vector")
    
    # ── Documents ──
    
    def add_document(self, doc: Document) -> None:
        """Add document metadata (no vector)."""
        if not self._client:
            return
        
        self._documents_collection.add(
            ids=[doc.id],
            documents=[doc.title],
            metadatas=[{
                "title": doc.title,
                "file_path": doc.file_path,
                "domain": doc.domain.value,
                "language": doc.language,
                "chunk_count": doc.chunk_count,
            }],
        )
    
    def get_document(self, doc_id: str) -> Document | None:
        """Get document metadata by ID."""
        if not self._client:
            return None
        
        results = self._documents_collection.get(
            ids=[doc_id],
            include=["metadatas"],
        )
        
        if not results["ids"]:
            return None
        
        meta = results["metadatas"][0]
        return Document(
            id=doc_id,
            title=meta.get("title", ""),
            file_path=meta.get("file_path", ""),
            domain=MedicalDomain(meta.get("domain", "general")),
            language=meta.get("language", ""),
            chunk_count=meta.get("chunk_count", 0),
        )
    
    # ── Helpers ──
    
    def _parse_results(
        self,
        raw_results: dict,
        source_type: str,
    ) -> list[RetrievalResult]:
        """Parse Chroma query results into RetrievalResult objects."""
        results = []
        
        ids = raw_results.get("ids", [[]])[0]
        documents = raw_results.get("documents", [[]])[0]
        metadatas = raw_results.get("metadatas", [[]])[0]
        distances = raw_results.get("distances", [[]])[0]
        
        for i, doc_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            dist = distances[i] if i < len(distances) else 1.0
            
            # Convert cosine distance to similarity score (0-1)
            # Chroma cosine distance: 0 = identical, 2 = opposite
            score = max(0, 1 - dist / 2)
            
            # Create chunk from metadata
            chunk = CorpusChunk(
                id=doc_id,
                text=documents[i] if i < len(documents) else "",
                chunk_type=meta.get("chunk_type", "paragraph"),
                language=meta.get("language", ""),
                domain=MedicalDomain(meta.get("domain", "general")),
                source_document=meta.get("source_document", ""),
                source_file=meta.get("source_file", ""),
                position=meta.get("position", 0),
                source_text=meta.get("source_text", ""),
                target_text=meta.get("target_text", ""),
                quality_score=meta.get("quality_score", 0.0),
            )
            
            results.append(RetrievalResult(
                chunk=chunk,
                score=score,
                rank=i + 1,
                source=source_type,
            ))
        
        return results
    
    def delete_document(self, doc_id: str) -> None:
        """Delete all chunks and metadata for a document."""
        if not self._client:
            return
        
        # Delete corpus chunks
        self._corpus_collection.delete(
            where={"source_document": doc_id},
        )
        
        # Delete document metadata
        self._documents_collection.delete(
            ids=[doc_id],
        )
    
    def count(self) -> dict[str, int]:
        """Return counts of stored items."""
        if not self._client:
            return {"terms": 0, "corpus": 0, "documents": 0}
        
        return {
            "terms": self._terms_collection.count(),
            "corpus": self._corpus_collection.count(),
            "documents": self._documents_collection.count(),
        }
    
    @property
    def is_available(self) -> bool:
        if self._client is None:
            return False
        try:
            from termprep.rag.embedder import get_embedder
            return get_embedder().is_available
        except Exception:
            return False


# Singleton instance
_vector_store_instance: MedicalVectorStore | None = None


def get_vector_store() -> MedicalVectorStore:
    """Get or create the global vector store instance."""
    global _vector_store_instance
    if _vector_store_instance is None:
        _vector_store_instance = MedicalVectorStore()
    return _vector_store_instance
