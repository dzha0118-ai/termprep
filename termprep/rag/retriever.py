"""Medical retriever — hybrid search combining exact match, fuzzy match, and vector search.

Three-layer retrieval:
1. Exact term match (O(1) hash lookup) — highest priority
2. Fuzzy term match (vector similarity) — medium priority
3. Corpus semantic search (vector) — lowest priority

The retriever combines all three into a single RAGContext.
"""

from __future__ import annotations

import re
from typing import Any

from .medical_schema import (
    MedicalDomain,
    MedicalTerm,
    RAGContext,
    RetrievalResult,
    TermStatus,
)
from .vector_store import get_vector_store


class MedicalRetriever:
    """Hybrid medical retriever with term + corpus search."""
    
    def __init__(
        self,
        max_exact_terms: int = 20,
        max_fuzzy_terms: int = 10,
        max_corpus_chunks: int = 5,
        term_score_threshold: float = 0.5,   # min similarity for fuzzy terms
        corpus_score_threshold: float = 0.3,    # min similarity for corpus
    ):
        self.max_exact_terms = max_exact_terms
        self.max_fuzzy_terms = max_fuzzy_terms
        self.max_corpus_chunks = max_corpus_chunks
        self.term_score_threshold = term_score_threshold
        self.corpus_score_threshold = corpus_score_threshold
        
        self._store = get_vector_store()
    
    def retrieve(
        self,
        query_text: str,
        domain: MedicalDomain | None = None,
        source_document: str | None = None,
    ) -> RAGContext:
        """Retrieve RAG context for a given query text."""
        context = RAGContext(query_text=query_text)
        
        # 1. Exact term match: extract keywords from query, check glossary
        context.exact_terms = self._retrieve_exact_terms(query_text, domain)
        
        # 2. Fuzzy term match: semantic search for related terms
        context.fuzzy_terms = self._retrieve_fuzzy_terms(query_text, domain)
        
        # 3. Corpus semantic search: find similar paragraphs/sentences
        context.corpus_chunks = self._retrieve_corpus(
            query_text, domain, source_document
        )
        
        context.total_terms = len(context.exact_terms) + len(context.fuzzy_terms)
        context.total_chunks = len(context.corpus_chunks)
        
        return context
    
    def _retrieve_exact_terms(
        self,
        query_text: str,
        domain: MedicalDomain | None = None,
    ) -> list[MedicalTerm]:
        """Extract candidate terms from query and check exact glossary matches."""
        if not self._store.is_available:
            return []
        
        # Extract candidate terms from query
        candidates = self._extract_term_candidates(query_text)
        
        exact_matches = []
        for candidate in candidates:
            term = self._store.get_term_by_exact(candidate)
            if term and (domain is None or term.domain == domain):
                exact_matches.append(term)
        
        # Sort by frequency (most used first)
        exact_matches.sort(key=lambda t: t.frequency, reverse=True)
        return exact_matches[:self.max_exact_terms]
    
    def _retrieve_fuzzy_terms(
        self,
        query_text: str,
        domain: MedicalDomain | None = None,
    ) -> list[MedicalTerm]:
        """Semantic search for related terms."""
        if not self._store.is_available:
            return []
        
        results = self._store.search_terms(
            query=query_text,
            n_results=self.max_fuzzy_terms * 2,
            domain=domain,
            status=TermStatus.VERIFIED,  # prefer verified terms
        )
        
        # Filter by threshold and deduplicate with exact matches
        fuzzy = []
        seen = set()
        for r in results:
            if r.score >= self.term_score_threshold:
                key = f"{r.chunk.source_text}:{r.chunk.target_text}"
                if key not in seen:
                    seen.add(key)
                    term = MedicalTerm(
                        source=r.chunk.source_text or r.chunk.text,
                        target=r.chunk.target_text or "",
                        domain=domain or MedicalDomain.GENERAL,
                        score=r.score,
                    )
                    fuzzy.append(term)
        
        return fuzzy[:self.max_fuzzy_terms]
    
    def _retrieve_corpus(
        self,
        query_text: str,
        domain: MedicalDomain | None = None,
        source_document: str | None = None,
    ) -> list[RetrievalResult]:
        """Semantic search for corpus chunks."""
        if not self._store.is_available:
            return []
        
        results = self._store.search_corpus(
            query=query_text,
            n_results=self.max_corpus_chunks * 2,
            domain=domain,
            source_document=source_document,
            min_quality=0.3,
        )
        
        # Filter by threshold
        filtered = [r for r in results if r.score >= self.corpus_score_threshold]
        return filtered[:self.max_corpus_chunks]
    
    def _extract_term_candidates(self, text: str) -> list[str]:
        """Extract candidate terms from text for exact matching."""
        candidates = []
        
        # 1. English phrases (2-5 words, capitalized or lowercase)
        en_phrases = re.findall(r'\b[a-zA-Z][a-zA-Z\s\-]{2,40}[a-zA-Z]\b', text)
        candidates.extend(en_phrases)
        
        # 2. Chinese phrases (2-8 characters)
        cn_phrases = re.findall(r'[\u4e00-\u9fff]{2,8}', text)
        candidates.extend(cn_phrases)
        
        # 3. Mixed phrases (e.g., "MRI 检查")
        mixed = re.findall(r'[a-zA-Z]{2,10}\s*[\u4e00-\u9fff]{2,6}', text)
        candidates.extend(mixed)
        
        # 4. Single English words (if 3+ chars, might be medical terms)
        single_words = re.findall(r'\b[a-zA-Z]{3,20}\b', text)
        candidates.extend(single_words)
        
        # Deduplicate and clean
        seen = set()
        cleaned = []
        for c in candidates:
            c_lower = c.strip().lower()
            if c_lower and c_lower not in seen and len(c_lower) > 2:
                seen.add(c_lower)
                cleaned.append(c.strip())
        
        return cleaned


# Singleton
_retriever_instance: MedicalRetriever | None = None


def get_retriever() -> MedicalRetriever:
    """Get or create the global retriever instance."""
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = MedicalRetriever()
    return _retriever_instance
