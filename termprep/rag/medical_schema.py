"""Medical RAG data models — typed dataclasses for terms, chunks, documents, and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class MedicalDomain(str, Enum):
    """Medical sub-domains for filtering and classification."""
    GENERAL = "general"
    CLINICAL = "clinical"           # 临床医学
    PHARMACOLOGY = "pharmacology"   # 药学
    RADIOLOGY = "radiology"         # 医学影像
    PATHOLOGY = "pathology"         # 病理学
    SURGERY = "surgery"             # 外科学
    ONCOLOGY = "oncology"           # 肿瘤学
    CARDIOLOGY = "cardiology"       # 心血管
    NEUROLOGY = "neurology"         # 神经学
    PEDIATRICS = "pediatrics"       # 儿科学
    GYNECOLOGY = "gynecology"       # 妇产科学
    DERMATOLOGY = "dermatology"     # 皮肤病学
    OPHTHALMOLOGY = "ophthalmology" # 眼科学
    ENT = "ent"                     # 耳鼻喉
    DENTISTRY = "dentistry"         # 口腔医学
    PSYCHIATRY = "psychiatry"       # 精神病学
    REHABILITATION = "rehabilitation" # 康复医学
    NUTRITION = "nutrition"         # 营养学
    EPIDEMIOLOGY = "epidemiology"   # 流行病学
    BIOMEDICAL = "biomedical"       # 生物医学工程
    TRADITIONAL_CHINESE_MEDICINE = "tcm"  # 中医


class TermStatus(str, Enum):
    """Validation status of a medical term."""
    EXTRACTED = "extracted"         # 自动提取，未验证
    REVIEWED = "reviewed"           # 人工审核
    VERIFIED = "verified"           # 专家确认
    DEPRECATED = "deprecated"     # 已废弃


class ChunkType(str, Enum):
    """Type of corpus chunk."""
    PARAGRAPH = "paragraph"         # 完整段落
    SENTENCE = "sentence"           # 单句
    BILINGUAL_PAIR = "bilingual"  # 双语对照
    TERM_DEFINITION = "definition" # 术语定义
    ABBREVIATION = "abbreviation" # 缩写


@dataclass
class MedicalTerm:
    """A validated medical term entry (glossary item)."""
    source: str                     # 源语（如英文）
    target: str                     # 目标语（如中文）
    domain: MedicalDomain = MedicalDomain.GENERAL
    context: str = ""             # 例句/上下文
    definition: str = ""          # 定义说明
    abbreviation: str = ""      # 缩写（如 CT = computed tomography）
    status: TermStatus = TermStatus.EXTRACTED
    frequency: int = 0            # 出现频率
    score: float = 0.0            # 置信度/质量分
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_vector_text(self) -> str:
        """Text representation for embedding (includes term + context)."""
        parts = [f"{self.source} — {self.target}"]
        if self.definition:
            parts.append(f"定义: {self.definition}")
        if self.context:
            parts.append(f"例句: {self.context}")
        if self.abbreviation:
            parts.append(f"缩写: {self.abbreviation}")
        return "\n".join(parts)
    
    def to_prompt_line(self) -> str:
        """Single-line format for prompt injection."""
        abbr = f" ({self.abbreviation})" if self.abbreviation else ""
        ctx = f" — 例句: {self.context}" if self.context else ""
        return f"• {self.source}{abbr} → {self.target}{ctx}"


@dataclass
class CorpusChunk:
    """A chunk of text (paragraph/sentence) stored in the vector database."""
    id: str
    text: str                      # 文本内容
    chunk_type: ChunkType = ChunkType.PARAGRAPH
    language: str = ""            # zh / en / mixed
    domain: MedicalDomain = MedicalDomain.GENERAL
    source_document: str = ""   # 来源文档 ID
    source_file: str = ""       # 原始文件名
    position: int = 0             # 在文档中的位置
    metadata: dict[str, Any] = field(default_factory=dict)
    
    # 翻译相关（如果是双语对照）
    source_text: str = ""       # 原文
    target_text: str = ""       # 译文
    quality_score: float = 0.0  # 翻译质量分（0-1）
    
    def to_embedding_text(self) -> str:
        """Text used for embedding — prefer source_text if bilingual."""
        if self.source_text and self.target_text:
            return f"原文: {self.source_text}\n译文: {self.target_text}"
        return self.text


@dataclass
class Document:
    """A source document (e.g., uploaded medical paper, manual)."""
    id: str
    title: str = ""
    file_path: str = ""
    domain: MedicalDomain = MedicalDomain.GENERAL
    language: str = ""          # 主要语言
    chunk_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RetrievalResult:
    """Result from a single retrieval query."""
    chunk: CorpusChunk
    score: float                  # 相似度分数
    rank: int = 0                 # 排名
    source: str = ""            # 检索来源："term_exact", "term_fuzzy", "vector", "hybrid"


@dataclass
class RAGContext:
    """Aggregated context for RAG-enhanced translation."""
    query_text: str = ""          # 原始查询文本
    
    # 三层检索结果
    exact_terms: list[MedicalTerm] = field(default_factory=list)      # 精确匹配术语
    fuzzy_terms: list[MedicalTerm] = field(default_factory=list)    # 模糊匹配术语
    corpus_chunks: list[RetrievalResult] = field(default_factory=list)  # 语料检索
    
    # 聚合统计
    total_terms: int = 0
    total_chunks: int = 0
    
    def to_prompt(self, max_terms: int = 20, max_chunks: int = 5) -> str:
        """Build the RAG context block for LLM prompt."""
        lines = []
        
        # 1. 精确术语（最高优先级）
        if self.exact_terms:
            lines.append("【参考术语表】（精确匹配）")
            for t in self.exact_terms[:max_terms]:
                lines.append(t.to_prompt_line())
            lines.append("")
        
        # 2. 模糊术语（次优先级）
        if self.fuzzy_terms and len(self.exact_terms) < max_terms:
            lines.append("【相关术语】（语义相似）")
            remaining = max_terms - len(self.exact_terms)
            for t in self.fuzzy_terms[:remaining]:
                lines.append(t.to_prompt_line())
            lines.append("")
        
        # 3. 语料上下文
        if self.corpus_chunks:
            lines.append("【参考语料】")
            for r in self.corpus_chunks[:max_chunks]:
                c = r.chunk
                if c.source_text and c.target_text:
                    lines.append(f"原文: {c.source_text}")
                    lines.append(f"译文: {c.target_text}")
                else:
                    lines.append(f"参考文本: {c.text}")
                lines.append("")
        
        return "\n".join(lines)
    
    def is_empty(self) -> bool:
        return not self.exact_terms and not self.fuzzy_terms and not self.corpus_chunks
