"""Medical context builder — assemble RAG results into LLM prompts.

Builds structured context blocks for medical translation:
1. Terminology block (exact + fuzzy matches)
2. Corpus block (similar paragraphs/sentences)
3. Style guide block (domain-specific rules)

Output format optimized for DeepSeek / OpenAI system prompts.
"""

from __future__ import annotations

from .medical_schema import MedicalDomain, RAGContext


# Domain-specific translation style guides
DOMAIN_STYLE_GUIDES = {
    MedicalDomain.CLINICAL: """
临床文本翻译原则：
- 使用标准医学术语，避免口语化
- 保持客观描述，避免主观判断
- 保留拉丁词和缩写（如 et al., i.e., e.g.）
- 数字和单位使用国际标准格式
- 症状描述保持精确，避免模糊表达
""",
    MedicalDomain.PHARMACOLOGY: """
药学文本翻译原则：
- 药物名称使用通用名（generic name），保留商品名括号标注
- 剂量单位统一（mg, g, mL, IU 等）
- 给药途径使用标准术语（口服、静脉注射、皮下注射等）
- 不良反应描述使用 MedDRA 标准术语
- 药代动力学参数保留英文缩写（Cmax, Tmax, AUC, t1/2）
""",
    MedicalDomain.RADIOLOGY: """
医学影像翻译原则：
- 影像术语使用标准中文（如 CT = 计算机断层扫描，MRI = 磁共振成像）
- 解剖部位使用标准解剖学术语
- 保留影像参数（层厚、窗宽、窗位等）
- 描述顺序：部位 → 征象 → 大小/范围 → 诊断印象
""",
    MedicalDomain.SURGERY: """
外科文本翻译原则：
- 手术名称使用标准术式名称
- 解剖结构使用标准术语
- 手术步骤描述清晰、逻辑顺序
- 保留器械名称和型号
- 术后并发症使用标准医学术语
""",
    MedicalDomain.ONCOLOGY: """
肿瘤学翻译原则：
- TNM 分期保留英文（T1, N0, M0）
- 病理分型使用 WHO 标准分类
- 治疗方案使用标准方案缩写（如 FOLFOX, AC-T）
- 生物标志物保留英文（HER2, PD-L1, EGFR）
- 疗效评估使用 RECIST 标准术语
""",
    MedicalDomain.TRADITIONAL_CHINESE_MEDICINE: """
中医文本翻译原则：
- 中医术语使用 WHO 国际标准术语
- 保留中医特色词汇（阴阳、五行、气血、经络）
- 方剂名保留拼音 + 英文翻译
- 穴位名称保留拼音 + 标准编号
- 证候名使用标准英译
""",
}

DEFAULT_STYLE_GUIDE = """
医学文本翻译通用原则：
- 术语统一：同一术语全文使用相同译法
- 准确性优先：宁可字面翻译，不可意译失准
- 保留专业符号：%, °C, mmHg, etc.
- 数字精确：小数点后位数与原文一致
- 人名地名：保留原文或音译（首次出现时附原文）
"""


class MedicalContextBuilder:
    """Build RAG-enhanced prompts for medical translation."""
    
    def __init__(
        self,
        max_terms: int = 20,
        max_chunks: int = 5,
        include_style_guide: bool = True,
        include_domain_hint: bool = True,
    ):
        self.max_terms = max_terms
        self.max_chunks = max_chunks
        self.include_style_guide = include_style_guide
        self.include_domain_hint = include_domain_hint
    
    def build_translation_prompt(
        self,
        source_text: str,
        target_language: str = "zh",  # or "en"
        domain: MedicalDomain = MedicalDomain.GENERAL,
        rag_context: RAGContext | None = None,
    ) -> str:
        """Build a complete translation prompt with RAG context."""
        parts = []
        
        # 1. System role
        parts.append("你是一位资深医学翻译专家。")
        
        # 2. Domain style guide
        if self.include_style_guide:
            style = DOMAIN_STYLE_GUIDES.get(domain, DEFAULT_STYLE_GUIDE)
            parts.append(style.strip())
        
        # 3. RAG context block
        if rag_context and not rag_context.is_empty():
            parts.append("\n【参考材料】")
            parts.append(rag_context.to_prompt(
                max_terms=self.max_terms,
                max_chunks=self.max_chunks,
            ))
        
        # 4. Translation instruction
        direction = "英译中" if target_language == "zh" else "中译英"
        parts.append(f"\n请翻译以下医学文本（{direction}）：")
        parts.append(f"\n原文：\n{source_text}")
        
        # 5. Output format
        parts.append("\n请直接输出译文，不要添加解释。保持术语与参考材料一致。")
        
        return "\n".join(parts)
    
    def build_term_extraction_prompt(
        self,
        text: str,
        domain: MedicalDomain = MedicalDomain.GENERAL,
        rag_context: RAGContext | None = None,
    ) -> str:
        """Build a prompt for medical term extraction."""
        parts = [
            "你是一位医学术语专家。请从以下文本中提取专业术语，并给出标准译法。",
        ]
        
        if self.include_domain_hint:
            parts.append(f"领域：{domain.value}")
        
        if rag_context and rag_context.exact_terms:
            parts.append("\n【已验证术语】（请保持以下译法一致）：")
            for t in rag_context.exact_terms[:self.max_terms]:
                parts.append(f"• {t.source} → {t.target}")
        
        parts.append(f"\n文本：\n{text}")
        parts.append("\n请输出 JSON 格式：")
        parts.append('{"terms": [{"source": "原文", "target": "译法", "domain": "领域", "confidence": 0.9}]}')
        
        return "\n".join(parts)
    
    def build_qa_prompt(
        self,
        question: str,
        domain: MedicalDomain = MedicalDomain.GENERAL,
        rag_context: RAGContext | None = None,
    ) -> str:
        """Build a prompt for medical Q&A with RAG."""
        parts = [
            "你是一位医学知识助手。请基于以下参考资料回答问题。",
        ]
        
        if rag_context and not rag_context.is_empty():
            parts.append("\n【参考资料】")
            parts.append(rag_context.to_prompt(
                max_terms=self.max_terms,
                max_chunks=self.max_chunks,
            ))
        
        parts.append(f"\n问题：{question}")
        parts.append("\n请基于参考资料回答。如果参考资料不足，请明确说明。")
        
        return "\n".join(parts)


# Singleton
_builder_instance: MedicalContextBuilder | None = None


def get_context_builder() -> MedicalContextBuilder:
    """Get or create the global context builder instance."""
    global _builder_instance
    if _builder_instance is None:
        _builder_instance = MedicalContextBuilder()
    return _builder_instance
