"""Term-extraction benchmark: precision/recall against a small gold set.

Usage:
    python eval_term_extraction.py            # score the current extractor
    python eval_term_extraction.py --show     # also print every extracted term

Metrics per text (k = top_n):
  strict P   : extracted term is exactly a gold term (normalised)
  lenient P  : extracted term contains a gold term (usable but over-long)
  junk       : neither -> likely a fragment / non-term
  recall     : share of gold terms recovered (strict)
  must_not   : blacklisted verb/clause fragments that leaked into the output

Purpose: make "更准了" measurable before and after the P0/P1 fixes.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from termprep.extractor import extract

TOP_N = 20
HERE = os.path.dirname(os.path.abspath(__file__))
GOLD_PATH = os.path.join(HERE, "eval_termset.json")


def norm(s: str) -> str:
    """Normalise for comparison: case, punctuation, whitespace."""
    s = (s or "").lower().strip()
    s = re.sub(r"[\s\u3000]+", " ", s)
    s = s.strip(" .,;:!?、。，；：！？\"'“”‘’()（）[]【】")
    return s


def norm_cjk(s: str) -> str:
    """CJK-safe normalisation: also drop spaces so 术语 管理 == 术语管理."""
    return re.sub(r"\s+", "", norm(s))


def load_cases():
    with open(GOLD_PATH, encoding="utf-8") as fh:
        return json.load(fh)["texts"]


def score_case(case, show=False):
    gold = [norm(g) for g in case["gold"]]
    gold_cjk = [norm_cjk(g) for g in case["gold"]]
    ok_extra_cjk = [norm_cjk(g) for g in case.get("ok_extra", [])]
    must_not = [norm(m) for m in case["must_not"]]

    terms = extract(case["text"], top_n=TOP_N)
    got = [t.term for t in terms]
    got_n = [norm(g) for g in got]
    got_cjk = [norm_cjk(g) for g in got]

    def _contains(needles, hay):
        return any(n and (n in hay or hay in n) for n in needles)

    strict = []
    lenient = []
    ok_extra = []
    junk = []
    for raw, n, nc in zip(got, got_n, got_cjk):
        if n in gold or nc in gold_cjk:
            strict.append(raw)
        elif _contains(gold_cjk, nc):
            lenient.append(raw)
        elif _contains(ok_extra_cjk, nc):
            # valid phrase, just not a terms-of-art target: neither junk nor recall
            ok_extra.append(raw)
        else:
            junk.append(raw)

    recalled = sum(1 for g in gold_cjk if g in got_cjk)
    leaked = [m for m in must_not if any(m == n or norm_cjk(m) == nc for n, nc in zip(got_n, got_cjk))]

    if show:
        print(f"\n  extracted ({len(got)}):")
        for raw, n, nc in zip(got, got_n, got_cjk):
            if n in gold or nc in gold_cjk:
                tag = "gold    "
            elif _contains(gold_cjk, nc):
                tag = "lenient "
            elif _contains(ok_extra_cjk, nc):
                tag = "ok-extra"
            else:
                tag = "JUNK    "
            print(f"    [{tag}] {raw}")

    return {
        "id": case["id"],
        "n": len(got),
        "strict": len(strict),
        "lenient": len(lenient),
        "ok_extra": len(ok_extra),
        "junk": len(junk),
        "recall_hit": recalled,
        "gold_n": len(case["gold"]),
        "leaked": leaked,
        "junk_terms": junk,
    }


def main():
    show = "--show" in sys.argv
    check = "--check" in sys.argv
    cases = load_cases()

    tot_n = tot_strict = tot_lenient = tot_junk = tot_hit = tot_gold = 0
    tot_leak = 0
    tot_extra = 0

    print("=" * 92)
    print(f"TermPrep 术语提取评测  (top_n={TOP_N})")
    print("=" * 92)
    print(f"{'case':<26} {'n':>3} {'gold':>5} {'lenient':>8} {'ok+':>4} {'junk':>5} {'recall':>8} {'must_not':>9}")
    print("-" * 92)

    rows = []
    for case in cases:
        r = score_case(case, show=show)
        rows.append(r)
        pct = lambda a, b: (100.0 * a / b) if b else 0.0
        print(f"{r['id']:<26} {r['n']:>3} {pct(r['strict'], r['n']):>4.0f}% "
              f"{pct(r['lenient'], r['n']):>7.0f}% {r['ok_extra']:>4} {r['junk']:>5} "
              f"{pct(r['recall_hit'], r['gold_n']):>7.0f}% {len(r['leaked']):>9}")
        tot_n += r["n"]; tot_strict += r["strict"]; tot_lenient += r["lenient"]
        tot_junk += r["junk"]; tot_hit += r["recall_hit"]; tot_gold += r["gold_n"]
        tot_leak += len(r["leaked"]); tot_extra += r["ok_extra"]

    pct = lambda a, b: (100.0 * a / b) if b else 0.0
    print("-" * 92)
    print(f"{'TOTAL':<26} {tot_n:>3} {pct(tot_strict, tot_n):>4.0f}% "
          f"{pct(tot_lenient, tot_n):>7.0f}% {tot_extra:>4} {tot_junk:>5} "
          f"{pct(tot_hit, tot_gold):>7.0f}% {tot_leak:>9}")
    print()
    print(f"  严格精确率 strict P@{TOP_N} : {pct(tot_strict, tot_n):.1f}%   (提取项 == 金标准术语)")
    print(f"  宽松精确率 lenient P@{TOP_N}: {pct(tot_strict + tot_lenient, tot_n):.1f}%   (+ 含金标准的偏长形式)")
    print(f"  召回率 R@{TOP_N}          : {pct(tot_hit, tot_gold):.1f}%  ({tot_hit}/{tot_gold})")
    print(f"  噪声 junk                 : {tot_junk}")
    print(f"  动宾/子句片段泄漏          : {tot_leak}")

    leaks = [(r["id"], r["leaked"]) for r in rows if r["leaked"]]
    if leaks:
        print("\n  泄漏的动宾/子句片段:")
        for cid, items in leaks:
            print(f"    {cid}: {items}")
    junks = [(r["id"], r["junk_terms"]) for r in rows if r["junk_terms"]]
    if junks:
        print("\n  被判为噪声的提取项:")
        for cid, items in junks:
            print(f"    {cid}: {items}")
    print("=" * 92)

    if check:
        # Regression gate: fail if extraction quality drops below the P0/P1 level.
        # Baseline before the fix was strict 35.2% / junk 27 / leaks 23.
        MIN_STRICT = 55.0
        MIN_RECALL = 58.0
        MAX_JUNK = 2
        MAX_LEAK = 0
        sp = pct(tot_strict, tot_n)
        rc = pct(tot_hit, tot_gold)
        problems = []
        if sp < MIN_STRICT:
            problems.append(f"strict P {sp:.1f}% < {MIN_STRICT}%")
        if rc < MIN_RECALL:
            problems.append(f"recall {rc:.1f}% < {MIN_RECALL}%")
        if tot_junk > MAX_JUNK:
            problems.append(f"junk {tot_junk} > {MAX_JUNK}")
        if tot_leak > MAX_LEAK:
            problems.append(f"fragment leaks {tot_leak} > {MAX_LEAK}")
        if problems:
            print("REGRESSION FAILED: " + "; ".join(problems))
            sys.exit(1)
        print(f"REGRESSION OK  (strict {sp:.1f}% >= {MIN_STRICT}%, "
              f"recall {rc:.1f}% >= {MIN_RECALL}%, junk {tot_junk} <= {MAX_JUNK}, leaks {tot_leak})")


if __name__ == "__main__":
    main()
