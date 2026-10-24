"""P4 — AI-based terminology verification.

Why this exists
---------------
The rule-based extractor (P0/P1 in ``extractor.py``) now removes clause fragments
reliably, but two classes of error remain that rules cannot settle:

1. **Descriptive phrases vs. terms of art** — ``高校翻译硕士课程`` is a well-formed
   noun phrase yet not a term; telling it apart needs world knowledge.
2. **Verbose vs. precise form** — ``术语管理过程`` / ``术语管理`` are both valid
   candidates, and only meaning can say the shorter one is the term.

A single batched AI call settles both cheaply. This module is deliberately
conservative and self-contained:

* **optional** — with no AI key, or on any failure, candidates pass through
  untouched (graceful degradation; never a hard error, never a silently empty
  result);
* **conservative** — a candidate is dropped only when the model explicitly marks
  it as a non-term *and* the answer matches the candidate verbatim; anything
  ambiguous is kept (宁可漏删，不可错删);
* **one call** — the whole candidate list goes into a single prompt.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Upper bound of candidates sent in one prompt (keeps the reply inside max_tokens).
MAX_CANDIDATES = 40


def _norm(s: str) -> str:
    """Normalise a term for verbatim-ish matching (case / punctuation / spaces)."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .,;:!?、。，；：！？\"'“”‘’()（）[]【】")


# Public alias — callers need the same normalisation to match decisions back.
normalise_term = _norm


def is_available(settings: dict | None) -> bool:
    """True when an AI engine is configured well enough to attempt verification."""
    settings = settings or {}
    return bool(settings.get("ai_api_key"))


def _build_prompt(candidates: list[str], context: str) -> tuple[str, str]:
    listing = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(candidates))
    system = (
        "You are a terminology reviewer preparing a translation project. "
        "You receive a SOURCE text and a list of CANDIDATE terms produced by a "
        "rule-based extractor. Decide for each candidate whether it is a genuine "
        "TERM.\n"
        "Keep when: domain-specific term, technical compound, method / metric / "
        "standard / named entity, or a concreteness-bearing noun phrase.\n"
        "Drop when: verb-object or clause fragment, sentence starter, purely "
        "generic wording, or merely a longer descriptive form of a shorter "
        "candidate that is also in the list (then keep the shorter one).\n"
        "Return JSON only:\n"
        '{"decisions": [{"term": "<candidate copied verbatim>", '
        '"is_term": true, "type": "term|noun phrase|verb phrase|clause|generic", '
        '"reason": "<short>"}]}\n'
        "Include exactly one decision per candidate and copy `term` verbatim."
    )
    excerpt = " ".join((context or "").split())[:1200]
    user = f"SOURCE (excerpt):\n{excerpt}\n\nCANDIDATES:\n{listing}"
    return system, user


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
    return text.strip().strip("`").strip()


def _parse_decisions(raw: str) -> dict[str, dict[str, Any]]:
    """Parse a model reply into ``{normalised_term: decision}``.

    Tolerates the usual format drift: fenced JSON, a bare list, ``decisions``
    wrapper, or ``term || yes/no`` lines.
    """
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    text = _strip_fences(raw)

    payload: Any = None
    if text[:1] in ("{", "["):
        try:
            payload = json.loads(text)
        except Exception:
            payload = None

    items: list[Any] = []
    if isinstance(payload, dict):
        picked = payload.get("decisions")
        if picked is None:
            picked = payload.get("results")
        if picked is None:
            # ``{"术语管理": true, "高校翻译硕士课程": false}`` shape — treat the
            # whole object as a term -> verdict map.
            picked = [
                {"term": k, "is_term": v}
                for k, v in payload.items()
                if isinstance(v, (bool, str))
            ]
        elif isinstance(picked, dict):
            picked = [{"term": k, "is_term": v} for k, v in picked.items()]
        items = picked if isinstance(picked, list) else []
    elif isinstance(payload, list):
        items = payload

    for item in items:
        if not isinstance(item, dict):
            continue
        term = item.get("term") or item.get("candidate") or item.get("word")
        if not term:
            continue
        verdict = item.get("is_term")
        if verdict is None and "keep" in item:
            verdict = item.get("keep")
        if isinstance(verdict, str):
            verdict = verdict.strip().lower() in ("true", "yes", "1", "y", "term", "keep")
        out[_norm(str(term))] = {
            "is_term": bool(verdict) if verdict is not None else True,
            "type": str(item.get("type") or ""),
            "reason": str(item.get("reason") or ""),
        }

    if out:
        return out

    # line-based fallback: "term || yes || type"
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in ("||", "\t", " | ", ":", "\uff1a"):
            if sep in line:
                parts = [p.strip() for p in line.split(sep)]
                if len(parts) >= 2:
                    term, verdict = parts[0], parts[1]
                    keep = verdict.lower() in ("true", "yes", "1", "y", "term", "keep")
                    out[_norm(term)] = {
                        "is_term": keep,
                        "type": parts[2] if len(parts) > 2 else "",
                        "reason": "",
                    }
                break
    return out


def verify_terms(
    candidates: list[str],
    context: str = "",
    settings: dict | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Ask the configured AI engine to classify candidates as terms / non-terms.

    Args:
        candidates: Candidate term strings, in ranking order.
        context: Source text excerpt used as evidence.
        settings: Engine settings (needs ``ai_api_key``).

    Returns:
        ``(decisions, meta)``. ``decisions`` maps normalised term ->
        ``{"is_term", "type", "reason"}``. It is **empty** whenever verification
        did not run or could not be parsed, and callers must then keep every
        candidate unchanged. ``meta`` reports what happened (never contains keys).
    """
    meta: dict[str, Any] = {
        "used": False,
        "reason": "",
        "checked": 0,
        "dropped": 0,
    }

    if not candidates:
        meta["reason"] = "no candidates"
        return {}, meta
    if not is_available(settings):
        meta["reason"] = "AI not configured"
        return {}, meta

    batch = [c for c in dict.fromkeys(candidates) if c and c.strip()][:MAX_CANDIDATES]
    if not batch:
        meta["reason"] = "no candidates"
        return {}, meta

    try:
        from termprep.engines.ai_translator import AITranslator

        engine = AITranslator(settings or {})
        if not getattr(engine, "available", False):
            meta["reason"] = "AI engine unavailable"
            return {}, meta
        system, user = _build_prompt(batch, context)
        raw = engine._chat(user, system_prompt=system)  # type: ignore[attr-defined]
    except Exception as exc:  # never let verification break extraction
        meta["reason"] = f"AI call failed: {type(exc).__name__}"
        return {}, meta

    decisions = _parse_decisions(raw or "")
    # keep only answers that correspond to a candidate we actually asked about
    wanted = {_norm(c) for c in batch}
    decisions = {k: v for k, v in decisions.items() if k in wanted}

    if not decisions:
        meta["reason"] = "unparseable or empty reply"
        return {}, meta

    meta["used"] = True
    meta["reason"] = "ok"
    meta["checked"] = len(decisions)
    meta["dropped"] = sum(1 for v in decisions.values() if not v.get("is_term", True))
    return decisions, meta
