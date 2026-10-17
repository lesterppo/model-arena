#!/usr/bin/env python3
"""Capability comparison: deepseek-flash vs gpt-6-luna vs claude-haiku-5-5.

Same 8-test battery against each model. Records latency, token usage and raw
outputs; auto-scores the objective tests. Saves results.json next to this file.

Usage: python3 compare.py
"""

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request  # noqa: E402

sys.path.insert(0, "/home/hatch/workspace/model-compare")
from muse_spark import call_musespark  # noqa: E402

ANTHROPIC_VERSION = "2023-06-01"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")


def post_json(url, headers, body, provider, hosts):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    add_surrogate_to_request(req, provider, allowed_hosts=hosts)
    t0 = time.perf_counter()
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:600]}")
    return json.loads(raw), time.perf_counter() - t0


# ---------------- provider call wrappers ----------------

def call_deepseek(model, messages, tools=None):
    body = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
    data, lat = post_json("https://api.deepseek.com/chat/completions",
                          {"Content-Type": "application/json"}, body,
                          "custom.deepseek", ["api.deepseek.com"])
    msg = data["choices"][0]["message"]
    return {"text": msg.get("content") or "",
            "tool_calls": msg.get("tool_calls") or [],
            "usage": {"in": data.get("usage", {}).get("prompt_tokens"),
                      "out": data.get("usage", {}).get("completion_tokens")},
            "latency": lat}


def call_openai(model, messages, tools=None):
    body = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
        body["reasoning_effort"] = "none"  # required for function tools on luna
    data, lat = post_json("https://api.openai.com/v1/chat/completions",
                          {"Content-Type": "application/json"}, body,
                          "custom.openai", ["api.openai.com"])
    msg = data["choices"][0]["message"]
    return {"text": msg.get("content") or "",
            "tool_calls": msg.get("tool_calls") or [],
            "usage": {"in": data.get("usage", {}).get("prompt_tokens"),
                      "out": data.get("usage", {}).get("completion_tokens")},
            "latency": lat}


def call_anthropic(model, messages, tools=None):
    body = {"model": model, "max_tokens": 1500, "messages": messages}
    if tools:
        body["tools"] = [{
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        } for t in tools]
        body["tool_choice"] = {"type": "auto"}
    data, lat = post_json("https://api.anthropic.com/v1/messages",
                          {"Content-Type": "application/json",
                           "anthropic-version": ANTHROPIC_VERSION}, body,
                          "custom.anthropic", ["api.anthropic.com"])
    text, tcalls = "", []
    for b in data.get("content", []):
        if b.get("type") == "text":
            text += b.get("text", "")
        elif b.get("type") == "tool_use":
            tcalls.append({"name": b["name"], "arguments": b["input"]})
    return {"text": text, "tool_calls": tcalls,
            "usage": {"in": data.get("usage", {}).get("input_tokens"),
                      "out": data.get("usage", {}).get("output_tokens")},
            "latency": lat}


PROVIDERS = {
    "deepseek-flash": ("deepseek-flash", call_deepseek),
    "gpt-6-luna": ("gpt-6-luna", call_openai),
    "claude-haiku-5-5": ("claude-haiku-5-5", call_anthropic),
    # self-test: answer-bank backed, see muse_spark.py (no live API yet)
    "muse-spark": ("muse-spark-1.3", call_musespark),
}

WEATHER_TOOL = [{"type": "function",
                 "function": {"name": "get_weather",
                              "description": "Get the current weather in a city.",
                              "parameters": {"type": "object",
                                             "properties": {"location": {"type": "string"}},
                                             "required": ["location"]}}}]


def needle_context():
    filler = ("The quick brown fox jumps over the lazy dog. "
              "Pack my box with five dozen liquor jugs. "
              "How vexingly quick daft zebras jump. ")
    paras = []
    for i in range(60):
        paras.append(f"Paragraph {i + 1}: " + filler * 8)
    paras.insert(37, "The secret password is BLUEBIRD-42. Remember it for later.")
    return "\n\n".join(paras)


TESTS = [
    ("json_adherence",
     [{"role": "user",
       "content": 'Return ONLY this JSON object, no other text: '
                  '{"name": "Ada", "age": 36, "skills": ["math", "music", "chess"]}'}],
     None),
    ("tool_call",
     [{"role": "user", "content": "What is the current weather in Tokyo?"}],
     WEATHER_TOOL),
    ("math_avg_speed",
     [{"role": "user",
       "content": "A train travels 120 km in 1.5 hours, then another 180 km in 2.5 hours. "
                  "What is its average speed in km/h for the whole trip? "
                  "Show your working, then give the final number."}],
     None),
    ("math_eggs",
     [{"role": "user",
       "content": "Janet's ducks lay 16 eggs per day. She eats 3 for breakfast and uses 4 "
                  "to bake muffins. She sells the rest at $2 per egg. "
                  "How much money does she make per day? Show your working."}],
     None),
    ("code_is_prime",
     [{"role": "user",
       "content": "Write a Python function is_prime(n) that returns True if n is a prime "
                  "number and False otherwise. Put the code in a python code block."}],
     None),
    ("needle_retrieval",
     [{"role": "user",
       "content": "Read the following document and answer: what is the secret password?\n\n"
                  + needle_context()}],
     None),
    ("chinese_writing",
     [{"role": "user",
       "content": "用繁體中文寫一段約100字嘅短文，介紹量子運算嘅基本概念。"}],
     None),
    ("format_follow",
     [{"role": "user",
       "content": "List the capitals of France, Japan and Brazil. "
                  "Output exactly 3 lines, numbered \"1. \", \"2. \", \"3. \". "
                  "No other text before or after."}],
     None),
]


def extract_code(text):
    m = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    return m[0] if m else text


def run_code_tests(code):
    cases = {2: True, 3: True, 4: False, 17: True, 1: False, 0: False, 97: True, 100: False}
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code + "\n")
        path = f.name
    harness = ("import json,sys; sys.path.insert(0,'" + path.rsplit("/", 1)[0] + "'); "
               "import importlib.util; s=importlib.util.spec_from_file_location('m','" + path + "'); "
               "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
               "print(json.dumps({str(k): bool(m.is_prime(k))==v for k,v in " + repr(cases) + ".items()}))")
    try:
        out = subprocess.run([sys.executable, "-c", harness], capture_output=True,
                             text=True, timeout=15)
        if out.returncode != 0:
            return {"pass": False, "detail": "exec failed: " + (out.stderr or out.stdout)[:300]}
        got = json.loads(out.stdout.strip())
        failed = [k for k, v in got.items() if not v]
        return {"pass": not failed, "detail": f"{len(got)-len(failed)}/{len(got)} cases pass"
                + (f"; failed: {failed}" if failed else "")}
    except subprocess.TimeoutExpired:
        return {"pass": False, "detail": "timeout"}


def score(test_id, res):
    t = res["text"]
    if test_id == "json_adherence":
        try:
            o = json.loads(t.strip())
            ok = (o.get("name") == "Ada" and o.get("age") == 36
                  and o.get("skills") == ["math", "music", "chess"])
            return {"pass": ok, "detail": "parsed, schema ok" if ok else "parsed, wrong values"}
        except Exception as e:
            return {"pass": False, "detail": f"not valid JSON: {e}"}
    if test_id == "tool_call":
        for c in res["tool_calls"]:
            if isinstance(c, dict) and "function" in c:  # OpenAI/DeepSeek format
                name, args = c["function"].get("name"), c["function"].get("arguments")
            else:  # Anthropic format
                name, args = c.get("name"), c.get("arguments")
            if name == "get_weather":
                argstr = json.dumps(args) if not isinstance(args, str) else args
                return {"pass": "tokyo" in argstr.lower(),
                        "detail": f"called get_weather({argstr[:80]})"}
        return {"pass": False, "detail": "no tool call; answered in text"}
    if test_id == "math_avg_speed":
        return {"pass": bool(re.search(r"\b75\b", t)), "detail": "expected 75"}
    if test_id == "math_eggs":
        return {"pass": bool(re.search(r"\$?18\b", t)), "detail": "expected 18"}
    if test_id == "code_is_prime":
        return run_code_tests(extract_code(t))
    if test_id == "needle_retrieval":
        return {"pass": "bluebird-42" in t.lower(), "detail": "expected BLUEBIRD-42"}
    if test_id == "format_follow":
        lines = [l for l in t.strip().splitlines() if l.strip()]
        ok = (len(lines) == 3 and all(re.match(r"^[123]\.\s+\S", l) for l in lines))
        return {"pass": ok, "detail": f"{len(lines)} lines"}
    return {"pass": None, "detail": "qualitative"}


def main():
    results = {}
    for label, (model, caller) in PROVIDERS.items():
        print(f"\n===== {label} =====", flush=True)
        results[label] = {}
        for test_id, messages, tools in TESTS:
            try:
                res = caller(model, messages, tools)
                res["score"] = score(test_id, res)
            except Exception as e:  # noqa: BLE001
                res = {"error": str(e)[:400]}
            results[label][test_id] = res
            s = res.get("score", {})
            print(f"  {test_id}: {s.get('pass')} ({s.get('detail', res.get('error', ''))[:90]}) "
                  f"[{res.get('latency', 0):.1f}s in={res.get('usage', {}).get('in')} "
                  f"out={res.get('usage', {}).get('out')}]", flush=True)
    with open("/home/hatch/workspace/model-compare/results.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("\nsaved to ~/workspace/model-compare/results.json")


if __name__ == "__main__":
    main()
