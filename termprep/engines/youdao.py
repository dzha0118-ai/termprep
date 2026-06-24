"""Youdao Dictionary engine implementing the Translator interface."""

import hashlib
import time
import uuid
from typing import Any

import requests

from termprep.engines.base import register_engine
from termprep.interfaces.translator import TranslationRequest, TranslationResponse, Translator


@register_engine
class YoudaoTranslator(Translator):
    """Youdao Open API (v3) for translation."""

    name = "youdao"
    priority = 20

    def __init__(self, settings: dict | None = None) -> None:
        self.settings = settings or {}
        self.api_key = self.settings.get("youdao_key") or self._env_key()
        self.api_secret = self.settings.get("youdao_secret") or self._env_secret()
        self.timeout = self.settings.get("youdao_timeout", 10)

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def priority(self) -> int:
        return self.settings.get("youdao_priority", 20)

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        resp = TranslationResponse(engine=self.name)
        if not self.available:
            resp.errors.append("Youdao not configured")
            return resp

        text = request.text.replace("\uff0c", ",").replace("\u3001", ",")
        data = self._call_api(text)
        if data and data.get("errorCode") == "0":
            translations = data.get("translation", [])
            if translations:
                resp.text = translations[0]
                resp.source_lang = request.source_lang
                resp.target_lang = request.target_lang
                resp.confidence = 0.75
                return resp

        # Fallback: comma-split for long sentences
        resp.text = self._split_and_translate(text)
        if resp.text:
            resp.confidence = 0.60
        else:
            resp.errors.append("Youdao returned empty translation")
        return resp

    def _call_api(self, text: str) -> dict[str, Any] | None:
        try:
            salt = str(uuid.uuid4())
            curtime = str(int(time.time()))
            sign_str = self.api_key + text + salt + curtime + self.api_secret
            sign = hashlib.sha256(sign_str.encode()).hexdigest()
            params = {
                "q": text,
                "from": "auto",
                "to": "auto",
                "appKey": self.api_key,
                "salt": salt,
                "sign": sign,
                "signType": "v3",
                "curtime": curtime,
            }
            r = requests.get(
                "https://openapi.youdao.com/api",
                params=params,
                timeout=self.timeout,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def _split_and_translate(self, text: str) -> str:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) <= 1:
            return ""
        results = []
        for i, p in enumerate(parts):
            if i > 0:
                time.sleep(1.0)
            data = self._call_api(p)
            if data and data.get("errorCode") == "0":
                t = data.get("translation", [])
                results.append(t[0] if t else p)
            else:
                results.append(p)
        return ", ".join(results) if results else ""

    def _env_key(self) -> str:
        import os
        return os.environ.get("TERMPREP_YOUDAO_KEY", "")

    def _env_secret(self) -> str:
        import os
        return os.environ.get("TERMPREP_YOUDAO_SECRET", "")
