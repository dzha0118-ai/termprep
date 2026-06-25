"""
RAG Seed Data Initializer

Loads pre-seeded medical terms from data/medical_seed_terms.json into the vector store
on first startup. This ensures the RAG module has data even on fresh deployments
(e.g., Hugging Face Space container restarts, which wipe the ChromaDB directory).
"""

import json
import logging
import os

from termprep.rag.medical_schema import MedicalDomain, MedicalTerm, MedicalTermStatus
from termprep.rag.vector_store import get_vector_store

logger = logging.getLogger("termprep.rag")


def _find_seed_file() -> str | None:
    """Find the seed JSON file using multiple search paths."""
    candidates = [
        # When running from project root
        os.path.join(os.getcwd(), "data", "medical_seed_terms.json"),
        # When module is inside termprep/rag/
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "medical_seed_terms.json"),
        # Fallback: one more level up
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "medical_seed_terms.json"),
        # Absolute path from package root
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "medical_seed_terms.json"),
    ]
    for path in candidates:
        resolved = os.path.normpath(path)
        if os.path.exists(resolved):
            return resolved
    return None


def init_rag_seed_data() -> dict:
    """Initialize RAG vector store with seed data if empty.

    Returns:
        {"terms_added": int, "corpus_added": int, "status": str}
    """
    try:
        store = get_vector_store()
        if not store.is_available:
            return {"terms_added": 0, "corpus_added": 0, "status": "vector_store_unavailable"}

        counts = store.count()
        # Only seed if the store is completely empty (no terms and no corpus)
        if counts.get("terms", 0) > 0:
            return {"terms_added": 0, "corpus_added": 0, "status": "already_seeded"}

        seed_path = _find_seed_file()
        if not seed_path:
            logger.warning("Seed file not found at any known path")
            return {"terms_added": 0, "corpus_added": 0, "status": "seed_file_missing"}

        with open(seed_path, "r", encoding="utf-8") as f:
            raw_terms = json.load(f)

        terms = []
        for item in raw_terms:
            try:
                domain = MedicalDomain(item.get("domain", "general"))
            except ValueError:
                domain = MedicalDomain.GENERAL

            term = MedicalTerm(
                source=item["source"],
                target=item["target"],
                domain=domain,
                abbreviation=item.get("abbreviation", ""),
                definition=item.get("definition", ""),
                example_sentences=[
                    item.get("example_en", ""),
                    item.get("example_zh", ""),
                ],
                status=MedicalTermStatus.APPROVED,
                frequency=1.0,
                score=1.0,
            )
            terms.append(term)

        store.add_terms(terms)

        # Also add corpus chunks from example sentences for richer retrieval
        from termprep.rag.medical_schema import CorpusChunk
        corpus_chunks = []
        for item in raw_terms:
            en_example = item.get("example_en", "")
            zh_example = item.get("example_zh", "")
            if en_example and zh_example:
                try:
                    domain = MedicalDomain(item.get("domain", "general"))
                except ValueError:
                    domain = MedicalDomain.GENERAL
                chunk = CorpusChunk(
                    text=f"{en_example}\n{zh_example}",
                    domain=domain,
                    language="bilingual",
                    is_bilingual_pair=True,
                )
                corpus_chunks.append(chunk)

        if corpus_chunks:
            store.add_corpus_chunks(corpus_chunks)

        counts_after = store.count()
        result = {
            "terms_added": len(terms),
            "corpus_added": len(corpus_chunks),
            "status": "seeded",
            "total_terms": counts_after.get("terms", 0),
            "total_corpus": counts_after.get("corpus", 0),
        }
        logger.info("RAG seed data loaded: %s", result)
        return result

    except Exception as e:
        logger.warning("RAG seed initialization failed: %s", e)
        return {"terms_added": 0, "corpus_added": 0, "status": f"error: {e}"}
