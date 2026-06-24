"""Full-text translation.

EN→ZH: Google Translate (high quality, free, handles full sentences)
CN→EN: Youdao API (reliable, already configured)
"""

import re, os, hashlib, time, uuid, logging
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger("termprep.translator")


def _safe(default=None, log_msg=""):
    """Decorator-like wrapper: catches exceptions and logs them."""
    def decorator(fn):
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:
                if log_msg:
                    _log.warning(f"{log_msg}: {e}")
                return default() if callable(default) else default
        return wrapper
    return decorator


@dataclass
class TranslatedSegment:
    index: int
    source: str
    target: str
    highlights: list[dict] = field(default_factory=list)


@dataclass
class TranslationResult:
    source_text: str
    translated_text: str
    source_lang: str
    target_lang: str
    domain: str
    style_used: str
    segments: list[TranslatedSegment] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rag_metadata: dict[str, Any] = field(default_factory=dict)


STYLE_PROMPTS = {
    "legal": "使用正式的法律文书风格，术语准确，句式严谨。",
    "medical": "使用专业的医学文献风格，术语准确。",
    "finance": "使用正式的财经报告风格，术语准确，数据表达规范。",
    "it": "使用技术文档风格，术语保持一致性。",
    "academic": "使用学术论文风格，表达严谨。",
    "news": "使用新闻体风格，语言简洁有力。",
    "general": "",
}


# ── File parsers ──

def parse_file(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".txt": return _parse_txt(filepath)
        if ext == ".docx": return _parse_docx(filepath)
        if ext == ".doc": return _parse_doc(filepath)
        if ext == ".xlsx": return _parse_xlsx(filepath)
        if ext == ".pdf": return _parse_pdf(filepath)
    except Exception as e:
        raise ValueError(f"文件解析失败: {type(e).__name__}: {e}")
    raise ValueError(f"不支持的文件格式: {ext}")

def _parse_txt(fp): return open(fp, encoding="utf-8").read()

def _parse_doc(fp):
    """Parse .doc (Word 97-2003 binary format) via olefile + antiword/catdoc fallback."""
    # Try antiword (Linux) or catdoc first
    import subprocess
    for cmd in [['antiword', fp], ['catdoc', fp]]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        except Exception as e: _log.debug(f"non-critical: {e}")

    # Try olefile for manual text extraction
    try:
        import olefile
        ole = olefile.OleFileIO(fp)
        # Read WordDocument stream
        if ole.exists('WordDocument'):
            data = ole.openstream('WordDocument').read()
            # Extract readable text (UTF-16LE encoded)
            text = data.decode('utf-16-le', errors='ignore')
            # Filter by ignoring non-printable chars
            import re
            lines = re.findall(r'[\x20-\x7E\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]{4,}', text)
            if lines:
                result = '\n'.join(l.strip() for l in lines if l.strip())
                if len(result) > 20:
                    return result
        ole.close()
    except ImportError:
        pass
    except Exception as e: _log.debug(f"non-critical: {e}")

    raise ValueError("无法解析 .doc 文件。建议用 Word 另存为 .docx 格式后上传。")


def _parse_docx(fp):
    """Extract text from Word document using raw XML parsing (no lxml dependency)."""
    import zipfile, re

    # Parse DOCX XML directly — avoid python-docx lxml crash
    texts = []
    has_images = False
    try:
        with zipfile.ZipFile(fp) as z:
            # Check for embedded images
            images = [n for n in z.namelist() if 'media/' in n.lower()]
            if images:
                has_images = True

            # Read document.xml
            with z.open('word/document.xml') as f:
                xml = f.read().decode('utf-8', errors='replace')

            # Extract all text spans
            # Match <w:t ...>text</w:t> or <w:t>text</w:t>
            spans = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', xml)
            if spans:
                # Reconstruct paragraphs: paragraphs end with </w:p>
                paras = re.split(r'</w:p>', xml)
                for para in paras:
                    para_spans = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', para)
                    text = ''.join(para_spans).strip()
                    if text:
                        texts.append(text)

            # If no paragraphs found, concatenate all spans
            if not texts and spans:
                texts.append(' '.join(s for s in spans if s.strip()))

    except Exception as e:
        _log.debug(f"non-critical: {e}")

    # Fallback: try python-docx if raw parsing failed
    if not texts:
        try:
            from docx import Document
            doc = Document(fp)
            for p in doc.paragraphs:
                if p.text.strip():
                    texts.append(p.text)
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text for cell in row.cells if cell.text.strip())
                    if row_text.strip():
                        texts.append(row_text)
        except Exception as e:
            _log.debug(f"non-critical: {e}")

    # OCR fallback for image-based files
    if not texts and has_images:
        ocr_parts = _ocr_from_docx(fp)
        if ocr_parts:
            return "\n\n".join(ocr_parts)

    if not texts:
        raise ValueError("无法从文件中提取文本")

    return "\n\n".join(texts)


def _ocr_from_docx(fp) -> list[str]:
    """Extract images from docx and OCR them."""
    try:
        import zipfile, io, tempfile
        from PIL import Image
        import pytesseract

        texts = []
        with zipfile.ZipFile(fp) as z:
            for name in z.namelist():
                if 'media/' in name.lower() and any(name.lower().endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.bmp', '.tiff')):
                    data = z.read(name)
                    img = Image.open(io.BytesIO(data))
                    lang = 'chi_sim+eng'
                    txt = pytesseract.image_to_string(img, lang=lang).strip()
                    if txt:
                        texts.append(txt)
        return texts
    except Exception:
        return []


def _parse_xlsx(fp):
    """Extract text from Excel with merged cell handling."""
    import openpyxl
    wb = openpyxl.load_workbook(fp, data_only=True)
    texts = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        texts.append(f"--- {sheet_name} ---")
        # Get all merged cell ranges
        merged = set()
        for m in ws.merged_cells.ranges:
            merged.add(str(m))
        for row in ws.iter_rows(values_only=True):
            row_vals = [str(c) for c in row if c is not None]
            if row_vals:
                texts.append(" | ".join(row_vals))
    return "\n".join(texts)
def _parse_pdf(fp):
    """Extract text from PDF. Falls back to OCR for scanned/image-based files."""
    texts = []
    # Try PyMuPDF first
    try:
        import fitz
        doc = fitz.open(fp)
        for page in doc:
            t = page.get_text("text")
            if t.strip():
                texts.append(t)
            else:
                blocks = page.get_text("blocks")
                texts.append(" ".join(b[4] for b in blocks if b[6] == 0))
        doc.close()
    except ImportError:
        pass

    # Try PyPDF2 if PyMuPDF failed
    if not texts or not "".join(texts).strip():
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(fp)
            texts = [p.extract_text() or "" for p in reader.pages]
        except ImportError:
            pass

    result = "\n\n".join(t for t in texts if t.strip())
    if result.strip():
        return result

    # OCR fallback for scanned PDFs
    try:
        import fitz
        from PIL import Image
        import pytesseract, io
        doc = fitz.open(fp)
        ocr_texts = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            txt = pytesseract.image_to_string(img, lang='chi_sim+eng').strip()
            if txt:
                ocr_texts.append(txt)
        doc.close()
        result = "\n\n".join(ocr_texts)
        if result.strip():
            return result
    except Exception as e:
        _log.debug(f"non-critical: {e}")

    raise ImportError("Unable to extract text from PDF (try PyMuPDF or PyPDF2)")


# ── Translation ──

def translate_text(text: str, source_lang: str = "auto", target_lang: str = "auto", domain: str = "general") -> TranslationResult:
    """Translate text using best engine per direction."""
    from termprep.analyzer import analyze

    analysis = analyze(text)
    detected_lang = analysis.lang
    detected_domain = analysis.domain if domain == "general" else domain

    if target_lang == "auto":
        target_lang = "en" if detected_lang in ("zh", "mixed") else "zh"

    style = STYLE_PROMPTS.get(detected_domain, "")
    result = TranslationResult(
        source_text=text,
        translated_text="",
        source_lang=detected_lang,
        target_lang=target_lang,
        domain=detected_domain,
        style_used=style[:50] + "..." if style else "通用风格",
    )

    is_en_to_zh = not bool(re.search(r'[\u4e00-\u9fff]', text))

    if is_en_to_zh:
        # Google Translate: handles full sentences natively
        trans = _google_translate(text, "en", "zh-CN")
        # Verify translation actually contains Chinese — Google sometimes returns original text silently
        if trans and bool(re.search(r'[\u4e00-\u9fff]', trans)):
            result.translated_text = trans
            result.segments.append(TranslatedSegment(index=0, source=text, target=trans,
                highlights=_generate_highlights(text, trans, detected_domain)))
            return result
        # Retry with shorter segments
        if not trans or not re.search(r'[\u4e00-\u9fff]', trans):
            retry = _google_translate(text[:1500], "en", "zh-CN")
            if retry and re.search(r'[\u4e00-\u9fff]', retry):
                result.translated_text = retry
                result.segments.append(TranslatedSegment(index=0, source=text, target=retry,
                    highlights=_generate_highlights(text, retry, detected_domain)))
                return result
        # Fallback: Youdao word-by-word (better than nothing)
        fallback_text = _youdao_word_by_word_en(text)
        result.translated_text = fallback_text
        result.segments.append(TranslatedSegment(index=0, source=text, target=fallback_text,
            highlights=_generate_highlights(text, fallback_text, detected_domain)))
        return result
    else:
        # CN→EN: try Google Translate first (fast, handles full texts)
        trans = _google_translate(text, "zh-CN", "en")
        if trans:
            result.translated_text = trans
            result.segments.append(TranslatedSegment(index=0, source=text, target=trans,
                highlights=_generate_highlights(text, trans, detected_domain)))
            return result
        # Fallback: Youdao per-sentence (slow but reliable)
        segments = []
        for si, s in enumerate(_split_sentences(text)):
            if not s.strip(): continue
            if si > 0: time.sleep(0.3)
            tr = _youdao_cn_to_en(s)
            if not tr:
                time.sleep(0.5)
                tr = _youdao_cn_to_en(s)
            target = tr if tr else f"[{s[:20]}...]"
            segments.append(TranslatedSegment(
                index=si, source=s, target=target,
                highlights=_generate_highlights(s, target, detected_domain)
            ))
        if segments:
            result.translated_text = " ".join(seg.target for seg in segments)
            result.segments = segments
            return result

    if not result.segments:
        result.segments.append(TranslatedSegment(index=0, source=text, target=result.translated_text,
            highlights=_generate_highlights(text, result.translated_text, detected_domain)))

    return result


_google_available: bool | None = None


def _google_translate_available() -> bool:
    """Probe Google Translate once; cache the result."""
    global _google_available
    if _google_available is not None:
        return _google_available
    try:
        import urllib.parse, requests
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=zh-CN&dt=t&q=test"
        r = requests.get(url, timeout=2, headers={"User-Agent": "Mozilla/5.0"})
        _google_available = r.status_code == 200
    except Exception:
        _google_available = False
    return _google_available


def _mark_google_unavailable() -> None:
    global _google_available
    _google_available = False


def _google_translate(text: str, src: str, tgt: str) -> str:
    """Google Translate via direct API. 1000-char chunks for reliability."""
    if not _google_translate_available():
        return ""
    import requests, urllib.parse, re, time

    def _call(chunk: str) -> str:
        try:
            encoded = urllib.parse.quote(chunk[:1000])  # hard limit 1000
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={src}&tl={tgt}&dt=t&q={encoded}"
            resp = requests.get(url, timeout=2, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data and isinstance(data[0], list):
                    parts = [s[0] for s in data[0] if isinstance(s, (list, tuple)) and s and s[0]]
                    result = " ".join(parts) if parts else ""
                    # Verify translation succeeded (has chars of target language)
                    has_tgt = bool(re.search(r'[\u4e00-\u9fff]', result)) if tgt.startswith('zh') else True
                    if result and has_tgt:
                        return result
        except Exception as e:
            _log.debug(f"non-critical: {e}")
            _mark_google_unavailable()
        return ""

    # Chunk at 1000 chars to reduce requests
    CHUNK_SIZE = 1000
    chunks = []
    buf = ""
    for s in re.split(r'(?<=[.!?。！？])\s+', text):
        if not s.strip(): continue
        if len(buf) + len(s) < CHUNK_SIZE:
            buf += s
        else:
            if buf.strip(): chunks.append(buf.strip())
            buf = s
    if buf.strip(): chunks.append(buf.strip())

    results = []
    for c in chunks:
        tr = _call(c)
        if tr:
            results.append(tr)
        time.sleep(0.05)
    return "\n\n".join(results) if results else ""


def _youdao_cn_to_en(text: str) -> str:
    """Youdao CN→EN with comma normalization and comma-splitting fallback."""
    text = text.replace('\uff0c', ',').replace('\u3001', ',').replace('\uff1b', ';').replace('\uff1a', ':')
    try:
        from termprep.sources.youdao import YoudaoSource
        y = YoudaoSource()
        if not y.available: return ""

        # Try full text first
        sr = y.search(text, limit=1)
        for r in sr:
            if r.word_type == "translation" and r.word:
                return r.word
    except Exception as e:
        _log.debug(f"non-critical: {e}")

    # If full text fails and no commas to split, try shorter version
    text_no_comma = text.replace(',','').replace('\uff0c','')
    if len(text_no_comma) > 16 and ',' not in text:
        try:
            short = text
            while len(short) > 16:
                short = short[1:]
            from termprep.sources.youdao import YoudaoSource
            y3 = YoudaoSource()
            sr3 = y3.search(short.strip().lstrip('，'), limit=1)
            for rr in sr3:
                if rr.word_type == "translation" and rr.word:
                    return rr.word
        except Exception as e:
            _log.debug(f"non-critical: {e}")

    # If full text fails, split at commas (with delay between parts)
    parts = [p.strip() for p in text.split(',') if p.strip()]
    if len(parts) > 1:
        results = []
        for pi, p in enumerate(parts):
            if pi > 0:
                import time; time.sleep(1.0)
            try:
                from termprep.sources.youdao import YoudaoSource
                y2 = YoudaoSource()
                sr2 = y2.search(p, limit=1)
                for rr in sr2:
                    if rr.word_type == "translation" and rr.word:
                        results.append(rr.word.strip('.,; '))
                        break
                else:
                    results.append(p)
            except Exception:
                results.append(p)
        if results:
            return ", ".join(results)

    return ""


def _youdao_word_by_word_en(text: str) -> str:
    """Word-by-word EN→ZH fallback via Youdao."""
    from termprep.sources.youdao import YoudaoSource
    y = YoudaoSource()
    if not y.available: return f"[翻译不可用]"
    words = text.split()
    result = []
    for w in words:
        if len(w) < 2: result.append(w); continue
        try:
            sr = y.search(w, limit=1)
            for r in sr:
                if r.word_type == "translation" and r.word:
                    result.append(r.word)
                    break
            else:
                result.append(w)
        except Exception:
            result.append(w)
    return " ".join(result)


def _split_sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[。！？.!?：:])\s*", text) if p.strip()]


def _generate_highlights(src: str, tgt: str, domain: str) -> list[dict]:
    """Generate term highlights with translations for the source text.

    Works for both EN→ZH and ZH→EN. Falls back to local domain dictionary
    when online translation fails, so highlights are rarely empty.
    """
    highlights = []
    try:
        import jieba.analyse
        from termprep.termbase import DOMAIN_TERMS

        is_cn_source = bool(re.search(r'[\u4e00-\u9fff]', src))

        # Candidate terms: jieba for Chinese, simple noun chunks for English
        candidates: list[tuple[str, float]] = []
        if is_cn_source:
            kw = jieba.analyse.extract_tags(src, topK=8, withWeight=True)
            for word, weight in kw:
                if len(word) >= 2:
                    candidates.append((word, weight))
        else:
            # Extract capitalized phrases and 2-4 word chunks
            seen = set()
            for pat in (r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b",
                        r"\b([A-Za-z][a-z]+(?:\s+[a-z]+){1,2})\b"):
                for m in re.finditer(pat, src):
                    phrase = m.group(1)
                    if phrase.lower() not in seen and len(phrase) >= 3:
                        seen.add(phrase.lower())
                        candidates.append((phrase, 0.5))
            # Add single important words from jieba as fallback
            kw = jieba.analyse.extract_tags(src, topK=8, withWeight=True)
            for word, weight in kw:
                if len(word) >= 3 and word.lower() not in seen:
                    seen.add(word.lower())
                    candidates.append((word, weight))

        # Sort by position in source for stable rendering
        positioned: list[tuple[int, int, str, float]] = []
        for word, weight in candidates:
            pos = src.find(word)
            if pos < 0: continue
            # Skip overlapping candidates
            overlap = any(not (pos + len(word) <= s or pos >= e) for s, e, _, _ in positioned)
            if overlap: continue
            if len(positioned) >= 5: break
            positioned.append((pos, pos + len(word), word, weight))

        for pos, end, word, _ in positioned:
            trans = ""
            if is_cn_source:
                # Prefer Google for Chinese words/phrases; fallback to Youdao
                trans = _google_translate(word, "zh-CN", "en")
                if not trans:
                    trans = _youdao_cn_to_en(word)
            else:
                trans = _google_translate(word, "en", "zh-CN")
                if not trans:
                    trans = _youdao_word_by_word_en(word)

            # Fallback: local domain dictionary (look up CN/EN pairs)
            if not trans:
                low = word.lower()
                for d, terms in DOMAIN_TERMS.items():
                    for t in terms:
                        if t.lower() == low:
                            trans = _find_opposite_term(t, terms) or ""
                            break
                    if trans: break

            if not trans:
                continue

            reasons = {
                "legal": f'"{word}" 在法律语境中译为 "{trans}"，符合法律文本的正式表达。',
                "medical": f'"{word}" 在医学语境中译为 "{trans}"，使用标准医学术语。',
                "finance": f'"{word}" 在金融语境中译为 "{trans}"，遵循财经术语标准。',
                "it": f'"{word}" 在信息技术语境中译为 "{trans}"，保持技术术语一致性。',
                "academic": f'"{word}" 在学术语境中译为 "{trans}"，符合论文表达习惯。',
            }
            highlights.append({
                "start": pos, "end": end, "term": word,
                "translation": trans, "reasoning": reasons.get(domain, f'"{word}" → "{trans}"'),
            })
    except Exception as e:
        _log.debug(f"non-critical: {e}")
    return highlights


def _find_opposite_term(term: str, terms: list[str]) -> str | None:
    """Given a term in a domain list, try to find its opposite-language counterpart."""
    has_cn = bool(re.search(r'[\u4e00-\u9fff]', term))
    for t in terms:
        if t.lower() == term.lower():
            continue
        t_has_cn = bool(re.search(r'[\u4e00-\u9fff]', t))
        if has_cn != t_has_cn:
            return t
    return None


# ═══════════════════════════════════════════════════════════════
#  Mixed-Language Translation (Chinese ↔ English)
# ═══════════════════════════════════════════════════════════════

def _detect_block_lang(text: str) -> str:
    """Detect the dominant language of a text block.
    Returns: 'zh' | 'en' | 'mixed' | 'neutral'
    """
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(re.findall(r"[a-zA-Z]", text))
    total = cn + en
    if total == 0:
        return "neutral"
    cn_ratio = cn / total
    if cn_ratio > 0.7:
        return "zh"
    elif cn_ratio < 0.3:
        return "en"
    return "mixed"


def _split_blocks(text: str) -> list[dict]:
    """Split text into language-homogeneous blocks.

    Strategy:
    1. Try split by blank lines (\n\n) first — most common for well-formatted text
    2. If only 1 block and it's long (>100 chars), try line-by-line split
    3. Merge consecutive lines of the same language into blocks
    4. For mixed blocks, further split by sentence boundaries

    Each block: {text: str, lang: str}
    """
    # Step 1: try paragraph split (blank lines)
    raw_blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]

    # Step 2: if only 1 long block, try line-by-line split
    if len(raw_blocks) == 1 and len(raw_blocks[0]) > 100:
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if len(lines) > 1:
            # Merge consecutive lines of the same language
            merged: list[dict] = []
            current = {"text": lines[0], "lang": _detect_block_lang(lines[0])}
            for line in lines[1:]:
                line_lang = _detect_block_lang(line)
                # Merge if same language or current line is neutral
                if line_lang == current["lang"] or line_lang == "neutral" or current["lang"] == "neutral":
                    if current["lang"] == "neutral":
                        current["lang"] = line_lang
                    current["text"] += "\n" + line
                else:
                    merged.append(current)
                    current = {"text": line, "lang": line_lang}
            merged.append(current)
            # Further split any remaining mixed blocks
            blocks: list[dict] = []
            for m in merged:
                if m["lang"] == "mixed" and len(m["text"]) > 80:
                    sentences = _split_sentences(m["text"])
                    for s in sentences:
                        if not s.strip():
                            continue
                        s_lang = _detect_block_lang(s)
                        blocks.append({"text": s, "lang": s_lang})
                else:
                    blocks.append(m)
            return blocks

    # Fall back to paragraph split with sentence-level fallback for mixed blocks
    blocks: list[dict] = []
    for b in raw_blocks:
        lang = _detect_block_lang(b)
        if lang == "mixed" and len(b) > 80:
            sentences = _split_sentences(b)
            for s in sentences:
                if not s.strip():
                    continue
                s_lang = _detect_block_lang(s)
                blocks.append({"text": s, "lang": s_lang})
        else:
            blocks.append({"text": b, "lang": lang})
    return blocks


def translate_text_mixed(text: str, domain: str = "general", engine: str | None = None, settings: dict | None = None, use_rag: bool = False) -> TranslationResult:
    """Translate a mixed Chinese-English text paragraph by paragraph.
    Chinese paragraphs → English; English paragraphs → Chinese.
    Neutral blocks (numbers, symbols) are preserved.

    Args:
        text: Source text that may contain both Chinese and English.
        domain: Translation domain hint.
        engine: Preferred engine ("ai", "google", "youdao", or None for auto).
        settings: Optional settings dict to override default AI config.
        use_rag: Enable RAG retrieval for medical domain context.

    Returns:
        TranslationResult with bilingual segments.
    """
    from termprep.analyzer import analyze
    from termprep.config import settings as _default_settings

    analysis = analyze(text)
    detected_domain = analysis.domain if domain == "general" else domain
    style = STYLE_PROMPTS.get(detected_domain, "")

    result = TranslationResult(
        source_text=text,
        translated_text="",
        source_lang="mixed",
        target_lang="mixed",
        domain=detected_domain,
        style_used=style[:50] + "..." if style else "通用风格",
    )

    blocks = _split_blocks(text)
    if not blocks:
        result.translated_text = text
        return result

    active_settings = _default_settings.to_dict()
    if settings:
        active_settings.update({k: v for k, v in settings.items() if v is not None and v != ""})

    # Try AI first for mixed translation (if available and requested)
    ai_available = bool(engine == "ai" or engine is None)
    svc = None
    if ai_available:
        try:
            from termprep.services.translation_service import TranslationService
            svc = TranslationService(active_settings)
            svc.load_engines()
            # Only consider AI available if the chosen engine is actually available
            if engine == "ai" and not any(e.name == "ai" and e.available for e in svc._engines):
                ai_available = False
        except Exception:
            ai_available = False

    translated_blocks: list[str] = []
    segments: list[TranslatedSegment] = []
    rag_metadata = {"exact_terms": 0, "fuzzy_terms": 0, "corpus_chunks": 0}

    for idx, block in enumerate(blocks):
        src = block["text"]
        lang = block["lang"]
        tgt = ""

        if lang == "neutral":
            # Numbers, symbols, math — keep as is
            tgt = src
        elif lang == "zh":
            # CN → EN
            if ai_available and svc:
                r = svc.translate(src, source_lang="zh", target_lang="en", domain=detected_domain, engine=engine, use_rag=use_rag)
                if r.rag_metadata:
                    for key in rag_metadata:
                        rag_metadata[key] = max(rag_metadata[key], r.rag_metadata.get(key, 0))
                tgt = r.text or src
            else:
                tgt = _google_translate(src, "zh-CN", "en") or src
        elif lang == "en":
            # EN → ZH
            if ai_available and svc:
                r = svc.translate(src, source_lang="en", target_lang="zh", domain=detected_domain, engine=engine, use_rag=use_rag)
                if r.rag_metadata:
                    for key in rag_metadata:
                        rag_metadata[key] = max(rag_metadata[key], r.rag_metadata.get(key, 0))
                tgt = r.text or src
            else:
                tgt = _google_translate(src, "en", "zh-CN") or src
        else:
            # mixed or unknown — try AI full block, else keep
            if ai_available and svc:
                r = svc.translate(src, source_lang="auto", target_lang="auto", domain=detected_domain, engine=engine, use_rag=use_rag)
                if r.rag_metadata:
                    for key in rag_metadata:
                        rag_metadata[key] = max(rag_metadata[key], r.rag_metadata.get(key, 0))
                tgt = r.text or src
            else:
                tgt = src

        translated_blocks.append(tgt)
        segments.append(TranslatedSegment(
            index=idx, source=src, target=tgt,
            highlights=_generate_highlights(src, tgt, detected_domain)
        ))

    # Rejoin: preserve paragraph structure
    result.translated_text = "\n\n".join(translated_blocks)
    result.segments = segments
    result.rag_metadata = rag_metadata
    return result
