#!/usr/bin/env python3
"""Round 3: sharp differentiators. Rounds 1+2 were 15/15 across the board,
so these three tests target instruction traps, sequential-constraint
generation, and real number theory.

Usage: python3 compare3.py
"""

import json
import re
import sys

sys.path.insert(0, "/home/hatch/workspace/model-compare")
from compare import PROVIDERS  # noqa: E402

TESTS = [
    ("trap_instruction",
     "Describe the water cycle in exactly 25 words. Do not use the word "
     "'water' anywhere in your answer. Output only the description, no extra text."),
    ("alphabet_sentence",
     "Write a single English sentence in which the first word starts with A, "
     "the second word starts with B, the third with C, and so on, continuing "
     "through the alphabet in order for as many letters as you can. Each "
     "letter may be used at most once, in sequence. Output only the sentence."),
    ("number_theory",
     "Find the smallest positive integer n such that n^3 - n is a positive "
     "multiple of 1000. Show your reasoning, then state n clearly."),
]


def score_alphabet(t):
    words = re.findall(r"[A-Za-z']+", t)
    run = 0
    for i, w in enumerate(words):
        if i >= 26:
            break
        if w[0].upper() == chr(ord("A") + i):
            run += 1
        else:
            break
    return run


def score(test_id, text):
    if test_id == "trap_instruction":
        words = text.strip().split()
        n = len(words)
        no_water = "water" not in text.lower()
        return {"pass": n == 25 and no_water,
                "detail": f"{n} words (want 25), no 'water': {no_water}"}
    if test_id == "alphabet_sentence":
        run = score_alphabet(text)
        return {"pass": run >= 10, "detail": f"correct prefix run: {run} letters",
                "run": run}
    if test_id == "number_theory":
        ok = bool(re.search(r"\bn\s*=\s*125\b", text) or
                  re.search(r"(?i)(answer|smallest)[^.\n]{0,40}\b125\b", text))
        return {"pass": ok, "detail": "expected n=125"}


def main():
    results = {}
    for label, (model, caller) in PROVIDERS.items():
        print(f"\n===== {label} =====", flush=True)
        results[label] = {}
        for test_id, prompt in TESTS:
            try:
                res = caller(model, [{"role": "user", "content": prompt}], None)
                res["score"] = score(test_id, res["text"])
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e)[:300], "text": ""}
            results[label][test_id] = res
            s = res.get("score", {})
            print(f"  {test_id}: {s.get('pass')} ({str(s.get('detail', res.get('error','')))[:70]}) "
                  f"[{res.get('latency',0):.1f}s]", flush=True)
    with open("/home/hatch/workspace/model-compare/results3.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("\nsaved results3.json")
    print("\n--- alphabet sentences ---")
    for label in results:
        print(f"[{label}]", results[label]["alphabet_sentence"]["text"][:220])


if __name__ == "__main__":
    main()
