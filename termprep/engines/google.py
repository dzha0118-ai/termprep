"""Google Translate engine implementing the Translator interface."""

import re
import time
import urllib.parse
from typing import Any

import requests

from termprep.engines.base import register_engine
from termprep.interfaces.translator import TranslationRequest, TranslationResponse, Translator


@register_engine
class GoogleTranslator(Translator):
    """Google Translate via the free gtx endpoint."""

    name = "google"
    priority = 10  # lower than AI, higher than Youdao
    _ENDPOINT = "https://translate.googleapis.com/translate_a/single"

    def __init__(self, settings: dict | None = None) -> None:
        self.settings = settings or {}
        self._available: bool | None = None
        self.timeout = self.settings.get("google_timeout", 3)

    @property
    def available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            url = f"{self._ENDPOINT}?client=gtx&sl=en&tl=zh-CN&dt=t&q=test"
            r = requests.get(url, timeout=2, headers={"User-Agent": "Mozilla/5.0"})
            self._available = r.status_code == 200
        except Exception:
            self._available = False
        return self._available

    @property
    def priority(self) -> int:
        return self.settings.get("google_priority", 10)

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        resp = TranslationResponse(engine=self.name)

        if not self.available:
            resp.errors.append("Google Translate unavailable")
            return resp

        src = request.source_lang if request.source_lang != "auto" else ("zh-CN" if self._is_chinese(request.text) else "en")
        tgt = request.target_lang if request.target_lang != "auto" else ("en" if src.startswith("zh") else "zh-CN")

        result_text = self._translate_chunks(request.text, src, tgt)
        if result_text:
            resp.text = result_text
            resp.source_lang = src
            resp.target_lang = tgt
            resp.confidence = 0.85
        else:
            resp.errors.append("Google Translate returned empty")

        return resp

    def _translate_chunks(self, text: str, src: str, tgt: str) -> str:
        chunks = self._split_text(text, chunk_size=1500)
        results = []
        for c in chunks:
            tr = self._call(c, src, tgt)
            if tr:
                results.append(tr)
            time.sleep(0.05)
        return "\n\n".join(results) if results else ""

    def _call(self, text: str, src: str, tgt: str) -> str:
        try:
            encoded = urllib.parse.quote(text[:1000])
            url = f"{self._ENDPOINT}?client=gtx&sl={src}&tl={tgt}&dt=t&q={encoded}"
            r = requests.get(url, timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data and isinstance(data[0], list):
                    parts = [s[0] for s in data[0] if isinstance(s, (list, tuple)) and s and s[0]]
                    result = " ".join(parts) if parts else ""
                    if result:
                        if tgt.startswith("zh"):
                            if re.search(r"[\u4e00-\u9fff]", result):
                                return result
                        else:
                            return result
        except Exception:
            pass
        return ""

    @staticmethod
    def _is_chinese(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    @staticmethod
    def _split_text(text: str, chunk_size: int = 1500) -> list[str]:
        chunks = []
        buf = ""
        for s in re.split(r"(?<=[.!?。！？])\s+", text):
            if not s.strip():
                continue
            if len(buf) + len(s) < chunk_size:
                buf += s
            else:
                if buf.strip():
                    chunks.append(buf.strip())
                buf = s
        if buf.strip():
            chunks.append(buf.strip())
        return chunks
