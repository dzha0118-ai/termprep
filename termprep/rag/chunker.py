"""Medical text chunker — split documents into chunks optimized for medical RAG.

Strategy:
1. Preserve paragraph boundaries (medical texts are often structured by paragraph)
2. For long paragraphs, split by sentence while keeping context
3. For bilingual texts, split into source-target pairs
4. Special handling for medical abbreviations and numbered lists
"""

from __future__ import annotations

import re
import uuid
from typing import Iterator

from .medical_schema import CorpusChunk, ChunkType, MedicalDomain


# Medical sentence delimiters (more conservative than general text)
SENTENCE_END = re.compile(r'(?<=[.!?。！？])\n+(?=[A-Z\u4e00-\u9fff])')

# Medical abbreviations that might be mistaken for sentence ends
MEDICAL_ABBREVIATIONS = {
    "e.g.", "i.e.", "et al.", "etc.", "vs.", "cf.",
    "Dr.", "Mr.", "Mrs.", "Ms.", "Prof.",
    "Fig.", "Table.", "Eq.", "No.",
    "U.S.", "U.K.", "E.U.",
    # Chinese medical abbreviations
    "等.", "例.", "如.",
}


class MedicalChunker:
    """Chunk medical texts with domain-aware splitting."""
    
    def __init__(
        self,
        max_chunk_size: int = 512,      # tokens (roughly)
        min_chunk_size: int = 50,       # minimum chars
        overlap: int = 50,                # overlap between chunks
        preserve_bilingual: bool = True,  # keep source-target pairs together
    ):
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.overlap = overlap
        self.preserve_bilingual = preserve_bilingual
    
    def chunk_document(
        self,
        text: str,
        source_document: str = "",
        source_file: str = "",
        domain: MedicalDomain = MedicalDomain.GENERAL,
    ) -> Iterator[CorpusChunk]:
        """Split a document into chunks."""
        # 1. Split by paragraph (medical texts are paragraph-structured)
        paragraphs = self._split_paragraphs(text)
        
        position = 0
        for para in paragraphs:
            if not para.strip():
                continue
            
            # 2. Detect if this is bilingual (CN/EN mixed)
            if self.preserve_bilingual and self._is_bilingual_paragraph(para):
                for chunk in self._chunk_bilingual(para, source_document, source_file, domain, position):
                    yield chunk
                    position += 1
            else:
                # 3. Regular paragraph: chunk by size
                for chunk in self._chunk_paragraph(para, source_document, source_file, domain, position):
                    yield chunk
                    position += 1
    
    def _split_paragraphs(self, text: str) -> list[str]:
        """Split text into paragraphs preserving blank lines."""
        # Split by double newline or single newline (medical texts often use single newline)
        paragraphs = re.split(r'\n\s*\n', text)
        return [p.strip() for p in paragraphs if p.strip()]
    
    def _is_bilingual_paragraph(self, text: str) -> bool:
        """Detect if paragraph contains both Chinese and English."""
        has_cn = bool(re.search(r'[\u4e00-\u9fff]', text))
        has_en = bool(re.search(r'[a-zA-Z]{3,}', text))  # at least 3 letters
        return has_cn and has_en
    
    def _chunk_bilingual(
        self,
        para: str,
        source_document: str,
        source_file: str,
        domain: MedicalDomain,
        position: int,
    ) -> Iterator[CorpusChunk]:
        """Chunk bilingual paragraphs into source-target pairs."""
        # Try to detect source-target pairs (e.g., "English sentence\nChinese sentence")
        lines = para.split('\n')
        
        pairs = []
        i = 0
        while i < len(lines) - 1:
            line1 = lines[i].strip()
            line2 = lines[i + 1].strip()
            
            if not line1 or not line2:
                i += 1
                continue
            
            # Check if line1 is EN and line2 is CN (or vice versa)
            is_en1 = bool(re.search(r'[a-zA-Z]{3,}', line1)) and not re.search(r'[\u4e00-\u9fff]', line1)
            is_cn2 = bool(re.search(r'[\u4e00-\u9fff]', line2))
            
            if is_en1 and is_cn2:
                pairs.append((line1, line2))
                i += 2
            else:
                # Check reverse: CN first, EN second
                is_cn1 = bool(re.search(r'[\u4e00-\u9fff]', line1))
                is_en2 = bool(re.search(r'[a-zA-Z]{3,}', line2)) and not re.search(r'[\u4e00-\u9fff]', line2)
                if is_cn1 and is_en2:
                    pairs.append((line2, line1))  # source=EN, target=CN
                    i += 2
                else:
                    i += 1
        
        if pairs:
            # Yield each pair as a bilingual chunk
            for idx, (src, tgt) in enumerate(pairs):
                yield CorpusChunk(
                    id=str(uuid.uuid4()),
                    text=f"{src}\n{tgt}",
                    chunk_type=ChunkType.BILINGUAL_PAIR,
                    language="mixed",
                    domain=domain,
                    source_document=source_document,
                    source_file=source_file,
                    position=position + idx,
                    source_text=src,
                    target_text=tgt,
                )
        else:
            # Fallback: treat as regular paragraph
            for chunk in self._chunk_paragraph(para, source_document, source_file, domain, position):
                yield chunk
    
    def _chunk_paragraph(
        self,
        para: str,
        source_document: str,
        source_file: str,
        domain: MedicalDomain,
        position: int,
    ) -> Iterator[CorpusChunk]:
        """Chunk a single paragraph by sentence or size."""
        if len(para) <= self.max_chunk_size:
            yield CorpusChunk(
                id=str(uuid.uuid4()),
                text=para,
                chunk_type=ChunkType.PARAGRAPH,
                language=self._detect_language(para),
                domain=domain,
                source_document=source_document,
                source_file=source_file,
                position=position,
            )
            return
        
        # Split by sentence for long paragraphs
        sentences = self._split_sentences(para)
        current_chunk = []
        current_len = 0
        chunk_idx = 0
        
        for sent in sentences:
            sent_len = len(sent)
            
            if current_len + sent_len > self.max_chunk_size and current_chunk:
                # Yield current chunk
                chunk_text = ' '.join(current_chunk) if self._detect_language(' '.join(current_chunk)) == 'en' else ''.join(current_chunk)
                yield CorpusChunk(
                    id=str(uuid.uuid4()),
                    text=chunk_text,
                    chunk_type=ChunkType.SENTENCE,
                    language=self._detect_language(chunk_text),
                    domain=domain,
                    source_document=source_document,
                    source_file=source_file,
                    position=position + chunk_idx,
                )
                chunk_idx += 1
                
                # Start new chunk with overlap
                overlap_sentences = current_chunk[-2:] if len(current_chunk) >= 2 else current_chunk[-1:]
                current_chunk = overlap_sentences + [sent]
                current_len = sum(len(s) for s in current_chunk)
            else:
                current_chunk.append(sent)
                current_len += sent_len
        
        # Yield remaining
        if current_chunk:
            chunk_text = ' '.join(current_chunk) if self._detect_language(' '.join(current_chunk)) == 'en' else ''.join(current_chunk)
            yield CorpusChunk(
                id=str(uuid.uuid4()),
                text=chunk_text,
                chunk_type=ChunkType.SENTENCE,
                language=self._detect_language(chunk_text),
                domain=domain,
                source_document=source_document,
                source_file=source_file,
                position=position + chunk_idx,
            )
    
    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences, handling medical abbreviations."""
        # Protect abbreviations
        protected = {}
        for i, abbr in enumerate(MEDICAL_ABBREVIATIONS):
            placeholder = f"__ABBR_{i}__"
            protected[placeholder] = abbr
            text = text.replace(abbr, placeholder)
        
        # Split by sentence end
        sentences = SENTENCE_END.split(text)
        
        # Restore abbreviations
        result = []
        for sent in sentences:
            for placeholder, abbr in protected.items():
                sent = sent.replace(placeholder, abbr)
            sent = sent.strip()
            if sent:
                result.append(sent)
        
        return result
    
    def _detect_language(self, text: str) -> str:
        """Detect dominant language of text."""
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        en_chars = len(re.findall(r'[a-zA-Z]', text))
        total = len(text.strip())
        if total == 0:
            return ""
        cn_ratio = cn_chars / total
        en_ratio = en_chars / total
        if cn_ratio > 0.3 and en_ratio > 0.3:
            return "mixed"
        elif cn_ratio > 0.15:
            return "zh"
        elif en_ratio > 0.3:
            return "en"
        return "mixed"


def chunk_text_for_rag(
    text: str,
    source_document: str = "",
    source_file: str = "",
    domain: MedicalDomain = MedicalDomain.GENERAL,
    max_chunk_size: int = 512,
) -> list[CorpusChunk]:
    """Convenience function to chunk text for RAG."""
    chunker = MedicalChunker(max_chunk_size=max_chunk_size)
    return list(chunker.chunk_document(text, source_document, source_file, domain))
