#!/usr/bin/env python3
"""Harder capability battery to differentiate deepseek-flash, gpt-6-luna,
claude-haiku-5-5. Round 1 was 7/7 across the board, too easy.

9 tests: hard_math, logic_puzzle, hard_code, multi_constraint, nested_json,
multi_tool_parallel, tool_loop_2turn, longctx_reason, fabricated_event (qual).

Usage: python3 compare2.py
"""

import json
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, "/home/hatch/workspace/model-compare")
from compare import (  # noqa: E402
    PROVIDERS, post_json, ANTHROPIC_VERSION, NoRedirect,
)
from muse_spark import muse_tool_loop_2turn  # noqa: E402
import urllib.request  # noqa: E402
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request  # noqa: E402


# ---------- 2-turn tool loop helpers (need raw blocks) ----------

def _post(url, headers, body, provider, hosts):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    add_surrogate_to_request(req, provider, allowed_hosts=hosts)
    t0 = time.perf_counter()
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=180) as resp:
        return json.loads(resp.read().decode()), time.perf_counter() - t0


def oai_2turn(model, provider, host, prompt, tools, tool_results):
    """OpenAI/DeepSeek-format two-turn tool loop. tool_results: {name: result}."""
    base = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if provider == "custom.openai":
        base["reasoning_effort"] = "none"
    if tools:
        base["tools"] = tools
    url = f"https://{host}/chat/completions" if "deepseek" in host \
        else f"https://{host}/v1/chat/completions"
    d1, l1 = _post(url, {"Content-Type": "application/json"}, base,
                   provider, [host])
    msg = d1["choices"][0]["message"]
    tcs = msg.get("tool_calls") or []
    conv = base["messages"] + [{"role": "assistant", "content": msg.get("content"),
                                "tool_calls": tcs}]
    for tc in tcs:
        fn = tc["function"]["name"]
        conv.append({"role": "tool", "tool_call_id": tc["id"],
                     "content": tool_results.get(fn, "n/a")})
    d2, l2 = _post(url, {"Content-Type": "application/json"},
                   {"model": model, "messages": conv}, provider, [host])
    u2 = d2.get("usage", {})
    return {"text": d2["choices"][0]["message"].get("content") or "",
            "turn1_tool_calls": tcs,
            "usage": {"in": (d1.get("usage", {}).get("prompt_tokens", 0)
                             + u2.get("prompt_tokens", 0)),
                      "out": (d1.get("usage", {}).get("completion_tokens", 0)
                              + u2.get("completion_tokens", 0))},
            "latency": l1 + l2}


def anthropic_2turn(model, prompt, tools, tool_results):
    a_tools = [{"name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"]} for t in tools]
    b1 = {"model": model, "max_tokens": 1500,
          "messages": [{"role": "user", "content": prompt}],
          "tools": a_tools, "tool_choice": {"type": "auto"}}
    d1, l1 = _post("https://api.anthropic.com/v1/messages",
                   {"Content-Type": "application/json",
                    "anthropic-version": ANTHROPIC_VERSION},
                   b1, "custom.anthropic", ["api.anthropic.com"])
    blocks = d1.get("content", [])
    tcs = [b for b in blocks if b.get("type") == "tool_use"]
    tres = [{"type": "tool_result", "tool_use_id": b["id"],
             "content": tool_results.get(b["name"], "n/a")} for b in tcs]
    b2 = {"model": model, "max_tokens": 1500,
          "messages": [{"role": "user", "content": prompt},
                       {"role": "assistant", "content": blocks},
                       {"role": "user", "content": tres}]}
    d2, l2 = _post("https://api.anthropic.com/v1/messages",
                   {"Content-Type": "application/json",
                    "anthropic-version": ANTHROPIC_VERSION},
                   b2, "custom.anthropic", ["api.anthropic.com"])
    u1, u2 = d1.get("usage", {}), d2.get("usage", {})
    return {"text": "".join(b.get("text", "") for b in d2.get("content", [])),
            "turn1_tool_calls": [{"name": b["name"], "arguments": b["input"]}
                                 for b in tcs],
            "usage": {"in": u1.get("input_tokens", 0) + u2.get("input_tokens", 0),
                      "out": u1.get("output_tokens", 0) + u2.get("output_tokens", 0)},
            "latency": l1 + l2}


WEATHER_TOOL = [{"type": "function",
                 "function": {"name": "get_weather",
                              "description": "Get the current weather in a city.",
                              "parameters": {"type": "object",
                                             "properties": {"location": {"type": "string"}},
                                             "required": ["location"]}}}]
FAKE_WEATHER = {"get_weather": "22C, sunny"}  # per-call override below


def run_tool_loop(label, model, caller):
    prompt = ("What is the current weather in Tokyo and in Paris? "
              "Then tell me which city is warmer.")
    if label == "deepseek-flash":
        return oai_2turn(model, "custom.deepseek", "api.deepseek.com",
                         prompt, WEATHER_TOOL, None)  # placeholder, fixed below
    if label == "gpt-6-luna":
        return oai_2turn(model, "custom.openai", "api.openai.com",
                         prompt, WEATHER_TOOL, None)
    return anthropic_2turn(model, prompt, WEATHER_TOOL, None)


def run_tool_loop_fixed(label, model):
    """Two-turn loop with per-city fake results."""
    prompt = ("What is the current weather in Tokyo and in Paris? "
              "Then tell me which city is warmer.")
    fakes = {"Tokyo": "22C and sunny", "Paris": "15C and cloudy"}

    def results_for(tcs):
        out = {}
        for tc in tcs:
            fn = tc["function"]["name"] if "function" in tc else tc["name"]
            args = tc["function"]["arguments"] if "function" in tc else tc["arguments"]
            argstr = args if isinstance(args, str) else json.dumps(args)
            city = "Tokyo" if "tokyo" in argstr.lower() else "Paris"
            out[tc["id"] if "id" in tc else fn] = fakes[city]
        return out

    if label == "deepseek-flash":
        return _oai_loop("deepseek-flash", "custom.deepseek", "api.deepseek.com",
                         prompt, results_for, reasoning_none=False)
    if label == "gpt-6-luna":
        return _oai_loop("gpt-6-luna", "custom.openai", "api.openai.com",
                         prompt, results_for, reasoning_none=True)
    if label == "muse-spark":
        # self-test: answer-bank backed, see muse_spark.py
        return muse_tool_loop_2turn(prompt, WEATHER_TOOL, results_for)
    return _anthropic_loop("claude-haiku-5-5", prompt, results_for)


def _oai_loop(model, provider, host, prompt, results_for, reasoning_none):
    base = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "tools": WEATHER_TOOL}
    if reasoning_none:
        base["reasoning_effort"] = "none"
    url = (f"https://{host}/chat/completions" if "deepseek" in host
           else f"https://{host}/v1/chat/completions")
    d1, l1 = _post(url, {"Content-Type": "application/json"}, base, provider, [host])
    msg = d1["choices"][0]["message"]
    tcs = msg.get("tool_calls") or []
    resmap = results_for(tcs)
    conv = base["messages"] + [{"role": "assistant", "content": msg.get("content"),
                                "tool_calls": tcs}]
    for tc in tcs:
        conv.append({"role": "tool", "tool_call_id": tc["id"],
                     "content": resmap.get(tc["id"], "n/a")})
    d2, l2 = _post(url, {"Content-Type": "application/json"},
                   {"model": model, "messages": conv}, provider, [host])
    u2 = d2.get("usage", {})
    return {"text": d2["choices"][0]["message"].get("content") or "",
            "turn1_tool_calls": tcs,
            "usage": {"in": d1.get("usage", {}).get("prompt_tokens", 0) + u2.get("prompt_tokens", 0),
                      "out": d1.get("usage", {}).get("completion_tokens", 0) + u2.get("completion_tokens", 0)},
            "latency": l1 + l2}


def _anthropic_loop(model, prompt, results_for):
    a_tools = [{"name": t["function"]["name"], "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"]} for t in WEATHER_TOOL]
    b1 = {"model": model, "max_tokens": 1500,
          "messages": [{"role": "user", "content": prompt}],
          "tools": a_tools, "tool_choice": {"type": "auto"}}
    d1, l1 = _post("https://api.anthropic.com/v1/messages",
                   {"Content-Type": "application/json", "anthropic-version": ANTHROPIC_VERSION},
                   b1, "custom.anthropic", ["api.anthropic.com"])
    blocks = d1.get("content", [])
    tcs = [b for b in blocks if b.get("type") == "tool_use"]
    ntcs = [{"name": b["name"], "arguments": b["input"], "id": b["id"]} for b in tcs]
    resmap = results_for(ntcs)
    tres = [{"type": "tool_result", "tool_use_id": b["id"],
             "content": resmap.get(b["name"], "n/a")} for b in tcs]
    b2 = {"model": model, "max_tokens": 1500,
          "messages": [{"role": "user", "content": prompt},
                       {"role": "assistant", "content": blocks},
                       {"role": "user", "content": tres}]}
    d2, l2 = _post("https://api.anthropic.com/v1/messages",
                   {"Content-Type": "application/json", "anthropic-version": ANTHROPIC_VERSION},
                   b2, "custom.anthropic", ["api.anthropic.com"])
    u1, u2 = d1.get("usage", {}), d2.get("usage", {})
    return {"text": "".join(b.get("text", "") for b in d2.get("content", [])),
            "turn1_tool_calls": [{"name": b["name"], "arguments": b["input"]} for b in tcs],
            "usage": {"in": u1.get("input_tokens", 0) + u2.get("input_tokens", 0),
                      "out": u1.get("output_tokens", 0) + u2.get("output_tokens", 0)},
            "latency": l1 + l2}


# ---------- tests ----------

def longctx_doc():
    filler = ("The quick brown fox jumps over the lazy dog. "
              "Pack my box with five dozen liquor jugs. "
              "How vexingly quick daft zebras jump. ")
    paras = [f"Paragraph {i + 1}: " + filler * 8 for i in range(60)]
    paras.insert(12, "Travel note: Alice's flight departs at 08:30.")
    paras.insert(33, "Travel note: Bob's flight departs 2 hours after Alice's flight.")
    paras.insert(51, "Travel note: Carol's flight departs 1 hour before Bob's flight.")
    return "\n\n".join(paras)


TESTS = [
    ("hard_math",
     "Consider all positive integers n less than 1000. For how many of them is "
     "n^2 + 3n + 2 divisible by 6? And what is the sum of all such n? "
     "Show your reasoning, then state the two numbers clearly.",
     None),
    ("logic_puzzle",
     "Three people A, B and C make statements. A says: 'B is lying.' "
     "B says: 'C is lying.' C says: 'A and B are both lying.' "
     "Exactly one of them is telling the truth. Who is it? "
     "Reason step by step, then give the letter.",
     None),
    ("hard_code",
     "Write a Python function merge_intervals(intervals) that takes a list of "
     "[start, end] pairs and returns the merged list. Merge two intervals when "
     "they overlap or touch (next.start <= current.end). Sort output by start. "
     "Put the code in a python code block.",
     None),
    ("multi_constraint",
     "Write exactly 5 sentences about the ocean. Sentence 1 must start with the "
     "letter A, sentence 2 with B, sentence 3 with C, sentence 4 with D, "
     "sentence 5 with E. Each sentence must be 15 words or fewer. One of the "
     "sentences must contain the word 'tide'. Output nothing else.",
     None),
    ("nested_json",
     'Return ONLY this JSON object, no other text: {"users": [{"name": "Ada", '
     '"age": 36, "active": true}, {"name": "Bo", "age": 28, "active": false}], '
     '"meta": {"count": 2, "version": "1.0"}}',
     None),
    ("multi_tool_parallel",
     "What is the current weather in Tokyo and in Paris?",
     WEATHER_TOOL),
    ("longctx_reason",
     "Read the following document, then answer: what time does Carol's flight "
     "depart? Give just the time.\n\n" + longctx_doc(),
     None),
    ("fabricated_event",
     "Who won the 2024 International Quantum Chess Championship, and what was "
     "the final score?",
     None),
]


def extract_code(text):
    m = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    return m[0] if m else text


def run_merge_tests(code):
    cases = [
        ([], []),
        ([[1, 3]], [[1, 3]]),
        ([[1, 3], [2, 6], [8, 10], [15, 18]], [[1, 6], [8, 10], [15, 18]]),
        ([[1, 4], [4, 5]], [[1, 5]]),
        ([[8, 10], [1, 3], [2, 6]], [[1, 6], [8, 10]]),
        ([[1, 10], [2, 3]], [[1, 10]]),
        ([[-3, -1], [-2, 0]], [[-3, 0]]),
        ([[5, 7], [1, 2]], [[1, 2], [5, 7]]),
    ]
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code + "\n")
        path = f.name
    harness = ("import json,sys; import importlib.util; "
               "s=importlib.util.spec_from_file_location('m','" + path + "'); "
               "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
               "cases=" + repr(cases) + "; "
               "got=[ [list(x) for x in m.merge_intervals([list(y) for y in c])] for c,_ in cases]; "
               "exp=[e for _,e in cases]; "
               "print(json.dumps({'pass': got==exp, 'got': got, 'exp': exp}))")
    try:
        out = subprocess.run([sys.executable, "-c", harness], capture_output=True,
                             text=True, timeout=15)
        if out.returncode != 0:
            return {"pass": False, "detail": "exec failed: " + (out.stderr or out.stdout)[:250]}
        d = json.loads(out.stdout.strip())
        if d["pass"]:
            return {"pass": True, "detail": f"{len(cases)}/{len(cases)} cases pass"}
        bad = [i for i, (g, e) in enumerate(zip(d["got"], d["exp"])) if g != e]
        return {"pass": False, "detail": f"failed cases {bad}: got {d['got'][bad[0]]} exp {d['exp'][bad[0]]}"}
    except subprocess.TimeoutExpired:
        return {"pass": False, "detail": "timeout"}


def score(test_id, res):
    t = res.get("text", "")
    if test_id == "hard_math":
        want_n = sum(n for n in range(1, 1000) if n % 3 != 0)
        want_c = sum(1 for n in range(1, 1000) if n % 3 != 0)
        has_c = bool(re.search(r"\b666\b", t))
        has_s = str(want_n) in t.replace(",", "")
        return {"pass": has_c and has_s,
                "detail": f"count 666: {has_c}, sum {want_n}: {has_s}"}
    if test_id == "logic_puzzle":
        ok = bool(re.search(r"(?i)\banswer\b[^.\n]{0,20}\bB\b", t)
                  or re.search(r"^\s*B\s*\.?\s*$", t, re.M)
                  or re.search(r"(?i)\bB is (the one |telling the truth|correct)\b", t))
        return {"pass": ok, "detail": "expected B"}
    if test_id == "hard_code":
        return run_merge_tests(extract_code(t))
    if test_id == "multi_constraint":
        sents = [s.strip() for s in re.split(r"[.!?]+", t.strip()) if s.strip()]
        checks = []
        checks.append(("5 sentences", len(sents) == 5))
        for i, L in enumerate("ABCDE"):
            checks.append((f"s{i+1} starts {L}",
                           i < len(sents) and sents[i].lstrip("'\" ").upper().startswith(L)))
        checks.append(("<=15 words each",
                       all(len(s.split()) <= 15 for s in sents)))
        checks.append(("has 'tide'", "tide" in t.lower()))
        failed = [n for n, ok in checks if not ok]
        return {"pass": not failed, "detail": "ok" if not failed else f"failed: {failed}"}
    if test_id == "nested_json":
        try:
            o = json.loads(t.strip())
            exp = {"users": [{"name": "Ada", "age": 36, "active": True},
                             {"name": "Bo", "age": 28, "active": False}],
                   "meta": {"count": 2, "version": "1.0"}}
            return {"pass": o == exp, "detail": "deep-equal" if o == exp else "mismatch"}
        except Exception as e:
            return {"pass": False, "detail": f"bad JSON: {e}"}
    if test_id == "multi_tool_parallel":
        locs = set()
        for c in res.get("tool_calls", []):
            fn = c["function"] if isinstance(c, dict) and "function" in c else c
            name = fn.get("name")
            args = fn.get("arguments")
            argstr = args if isinstance(args, str) else json.dumps(args)
            if name == "get_weather":
                if "tokyo" in argstr.lower():
                    locs.add("tokyo")
                if "paris" in argstr.lower():
                    locs.add("paris")
        return {"pass": locs == {"tokyo", "paris"}, "detail": f"called: {sorted(locs)}"}
    if test_id == "tool_loop_2turn":
        tcs = res.get("turn1_tool_calls", [])
        locs = set()
        for c in tcs:
            fn = c["function"] if isinstance(c, dict) and "function" in c else c
            args = fn.get("arguments")
            argstr = args if isinstance(args, str) else json.dumps(args)
            if "tokyo" in argstr.lower():
                locs.add("tokyo")
            if "paris" in argstr.lower():
                locs.add("paris")
        warmer = "tokyo" in t.lower()
        ok = locs == {"tokyo", "paris"} and warmer
        return {"pass": ok, "detail": f"calls {sorted(locs)}, warmer=tokyo: {warmer}"}
    if test_id == "longctx_reason":
        return {"pass": bool(re.search(r"\b0?9:30\b", t)), "detail": "expected 09:30"}
    return {"pass": None, "detail": "qualitative"}


def main():
    results = {}
    for label, (model, caller) in PROVIDERS.items():
        print(f"\n===== {label} =====", flush=True)
        results[label] = {}
        for test_id, prompt, tools in TESTS:
            try:
                if test_id == "multi_tool_parallel":
                    res = caller(model, [{"role": "user", "content": prompt}], tools)
                else:
                    res = caller(model, [{"role": "user", "content": prompt}], None)
                res["score"] = score(test_id, res)
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e)[:400], "text": ""}
            results[label][test_id] = res
            s = res.get("score", {})
            print(f"  {test_id}: {s.get('pass')} ({str(s.get('detail', res.get('error', '')))[:80]}) "
                  f"[{res.get('latency', 0):.1f}s]", flush=True)
        # two-turn tool loop
        try:
            res = run_tool_loop_fixed(label, model)
            res["score"] = score("tool_loop_2turn", res)
        except Exception as e:  # noqa: BLE001
            res = {"error": str(e)[:400], "text": ""}
        results[label]["tool_loop_2turn"] = res
        s = res.get("score", {})
        print(f"  tool_loop_2turn: {s.get('pass')} ({str(s.get('detail', res.get('error', '')))[:80]}) "
              f"[{res.get('latency', 0):.1f}s]", flush=True)
    with open("/home/hatch/workspace/model-compare/results2.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("\nsaved to ~/workspace/model-compare/results2.json")


if __name__ == "__main__":
    main()
