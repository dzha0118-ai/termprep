"""Multi-Agent Translation Demo — end-to-end walkthrough.

This script demonstrates the full TermPrep multi-agent workflow:
    Term Agent → Glossary JSON → Translation Agent → Structured Translation

Run:
    cd termprep
    python -m termprep.demo_multi_agent

Or programmatically:
    from termprep.demo_multi_agent import demo_chinese, demo_english
    demo_chinese()
"""

from __future__ import annotations

import json

from termprep.agents.orchestrator import AgentOrchestrator
from termprep.config import settings


# ── Sample texts ──

SAMPLE_EN = """Machine learning has revolutionized natural language processing.
Neural networks, particularly transformer architectures like BERT and GPT,
have enabled significant breakthroughs in machine translation, sentiment analysis,
and text generation. These deep learning models require massive computational
resources and large-scale training datasets to achieve state-of-the-art performance."""

SAMPLE_ZH = """机器学习彻底改变了自然语言处理领域。神经网络，尤其是像BERT和GPT这样的
Transformer架构，在机器翻译、情感分析和文本生成方面取得了重大突破。
这些深度学习模型需要大量的计算资源和大规模训练数据集才能达到最先进的性能。"""


# ── Demo: English → Chinese ──


def demo_english() -> None:
    """Run the full pipeline on English source text."""
    print("=" * 70)
    print("DEMO 1: English → Chinese (IT/Academic Domain)")
    print("=" * 70)

    orch = AgentOrchestrator(settings.to_dict())

    # Step 1: Term Agent
    print("\n[Step 1] Term Agent — extracting terminology...")
    glossary = orch.run_term_agent(
        text=SAMPLE_EN,
        project_name="ML-Paper-Demo",
        top_n=20,
        domain_hint="it",
    )
    print(f"  ✓ Extracted {len(glossary.terms)} terms")
    print(f"  ✓ High confidence: {len(glossary.by_confidence('high'))}")
    print(f"  ✓ Domain detected: {glossary.meta.domain}")

    # Print sample glossary entries
    print("\n  Sample Glossary Entries:")
    for t in glossary.terms[:5]:
        status_icon = "✓" if t.target_translation else "○"
        print(f"    {status_icon} {t.source_term:30s} → {t.target_translation or '(pending)'}")

    # Step 2: Translation Agent (with glossary injection)
    print("\n[Step 2] Translation Agent — full-text translation with glossary...")
    translation = orch.run_translation_agent(
        text=SAMPLE_EN,
        glossary=glossary,
        domain="it",
        engine="ai",  # or None for auto-fallback
    )
    print(f"  ✓ Engine: {translation.engine}")
    print(f"  ✓ Glossary used: {translation.glossary_used}")
    print(f"  ✓ Term consistency: {translation.term_consistency_score:.0%}")

    print("\n  --- Translation ---")
    print(translation.translated_text[:500] + "..." if len(translation.translated_text) > 500 else translation.translated_text)

    # Step 3: Full pipeline shortcut
    print("\n[Step 3] Full Pipeline (one-shot)...")
    result = orch.run_full_pipeline(
        text=SAMPLE_EN,
        project_name="ML-Paper-Demo",
        top_n=20,
        translation_engine="ai",
    )
    print(f"  ✓ Total duration: {result['duration']:.2f}s")
    print(f"  ✓ Term consistency: {result['term_consistency']:.0%}")

    return result


# ── Demo: Chinese → English ──


def demo_chinese() -> None:
    """Run the full pipeline on Chinese source text."""
    print("=" * 70)
    print("DEMO 2: Chinese → English (IT/Academic Domain)")
    print("=" * 70)

    orch = AgentOrchestrator(settings.to_dict())

    # Step 1: Term Agent
    print("\n[Step 1] Term Agent — extracting terminology...")
    glossary = orch.run_term_agent(
        text=SAMPLE_ZH,
        project_name="ML-Paper-ZH",
        top_n=20,
    )
    print(f"  ✓ Extracted {len(glossary.terms)} terms")
    print(f"  ✓ High confidence: {len(glossary.by_confidence('high'))}")
    print(f"  ✓ Domain detected: {glossary.meta.domain}")

    print("\n  Sample Glossary Entries:")
    for t in glossary.terms[:5]:
        status_icon = "✓" if t.target_translation else "○"
        print(f"    {status_icon} {t.source_term:20s} → {t.target_translation or '(pending)'}")

    # Step 2: Translation Agent
    print("\n[Step 2] Translation Agent — full-text translation with glossary...")
    translation = orch.run_translation_agent(
        text=SAMPLE_ZH,
        glossary=glossary,
        domain="it",
        engine="ai",
    )
    print(f"  ✓ Engine: {translation.engine}")
    print(f"  ✓ Glossary used: {translation.glossary_used}")
    print(f"  ✓ Term consistency: {translation.term_consistency_score:.0%}")

    print("\n  --- Translation ---")
    print(translation.translated_text[:500] + "..." if len(translation.translated_text) > 500 else translation.translated_text)

    return translation


# ── Demo: Glossary JSON export ──


def demo_glossary_json() -> None:
    """Show how the standardized Glossary JSON looks for inter-agent communication."""
    print("=" * 70)
    print("DEMO 3: Standardized Glossary JSON (Agent Contract)")
    print("=" * 70)

    orch = AgentOrchestrator(settings.to_dict())
    glossary = orch.run_term_agent(
        text=SAMPLE_EN,
        project_name="JSON-Demo",
        top_n=10,
    )

    # Compact version for prompt injection
    compact = glossary.to_compact()
    print("\n[Compact Format] (for prompt injection):")
    print(json.dumps(compact, ensure_ascii=False, indent=2))

    # Full version for persistence / sharing
    print("\n[Full Schema] (for storage / inter-service communication):")
    print(glossary.model_dump_json()[:1500] + "...")


# ── Demo: Async task flow ──


def demo_async_task() -> None:
    """Demonstrate async task submission and polling."""
    print("=" * 70)
    print("DEMO 4: Async Task Flow (Submit → Poll → Execute)")
    print("=" * 70)

    orch = AgentOrchestrator(settings.to_dict())

    # Submit task
    task_id = orch.submit_task("full_pipeline", {
        "text": SAMPLE_EN,
        "project_name": "Async-Demo",
        "top_n": 15,
        "domain": "it",
    })
    print(f"\n[Submit] Task ID: {task_id}")

    # Check status before execution
    task = orch.get_task(task_id)
    print(f"[Status] {task['status']}")

    # Execute
    print("[Execute] Running pipeline...")
    result = orch.execute_task(task_id)

    print(f"[Result] Status: {result['status']}")
    if result['status'] == 'completed':
        print(f"[Result] Glossary terms: {result['result']['glossary']['meta']['total_terms']}")
        print(f"[Result] Consistency: {result['result']['term_consistency']:.0%}")
    else:
        print(f"[Result] Errors: {result.get('errors', [])}")


# ── Demo: Decoupled multi-agent (simulate distributed) ──


def demo_decoupled() -> None:
    """Simulate a decoupled workflow where Term Agent and Translation Agent
    run on different services / at different times."""
    print("=" * 70)
    print("DEMO 5: Decoupled Multi-Agent (Service A → Service B)")
    print("=" * 70)

    # Service A: Term Agent produces Glossary JSON
    print("\n[Service A] Term Agent producing Glossary JSON...")
    orch_a = AgentOrchestrator(settings.to_dict())
    glossary = orch_a.run_term_agent(
        text=SAMPLE_EN,
        project_name="Decoupled-Demo",
        top_n=15,
    )

    # Serialize to JSON (sent over network, saved to S3, etc.)
    glossary_json = glossary.model_dump_json()
    print(f"  ✓ Glossary JSON size: {len(glossary_json)} bytes")
    print(f"  ✓ Ready to send to Translation Service")

    # Service B: Translation Agent consumes Glossary JSON
    print("\n[Service B] Translation Agent consuming Glossary JSON...")
    from termprep.agents.schemas import Glossary
    orch_b = AgentOrchestrator(settings.to_dict())

    # Reconstruct Glossary from JSON
    glossary_received = Glossary.model_validate_json(glossary_json)
    print(f"  ✓ Reconstructed glossary with {len(glossary_received.terms)} terms")

    translation = orch_b.run_translation_agent(
        text=SAMPLE_EN,
        glossary=glossary_received,
        domain="it",
        engine="ai",
    )
    print(f"  ✓ Translation complete")
    print(f"  ✓ Term consistency: {translation.term_consistency_score:.0%}")


# ── Main entry point ──


def main() -> None:
    """Run all demos."""
    print("\n")
    print("╔" + "═" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║" + "  TermPrep Multi-Agent Translation Workflow Demo".center(68) + "║")
    print("║" + " " * 68 + "║")
    print("╚" + "═" * 68 + "╝")

    # Check configuration
    print("\n[Configuration Check]")
    print(f"  AI Provider: {settings.ai_provider or 'Not configured'}")
    print(f"  AI Model: {settings.ai_model or 'Not configured'}")
    print(f"  AI API Key: {'✓ Configured' if settings.ai_api_key else '✗ Not configured'}")
    print(f"  Youdao Key: {'✓ Configured' if settings.youdao_key else '✗ Not configured'}")
    print(f"  Google Translate: Available (auto-detected)")

    print("\n" + "─" * 70)

    # Run demos
    try:
        demo_english()
    except Exception as e:
        print(f"\n  [Error in English demo: {e}]")

    print("\n" + "─" * 70)

    try:
        demo_chinese()
    except Exception as e:
        print(f"\n  [Error in Chinese demo: {e}]")

    print("\n" + "─" * 70)

    try:
        demo_glossary_json()
    except Exception as e:
        print(f"\n  [Error in JSON demo: {e}]")

    print("\n" + "─" * 70)

    try:
        demo_decoupled()
    except Exception as e:
        print(f"\n  [Error in decoupled demo: {e}]")

    print("\n" + "─" * 70)

    try:
        demo_async_task()
    except Exception as e:
        print(f"\n  [Error in async demo: {e}]")

    print("\n" + "=" * 70)
    print("All demos completed!")
    print("=" * 70)


if __name__ == "__main__":
    main()
