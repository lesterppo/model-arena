#!/usr/bin/env python3
"""muse-spark provider for the model-compare harness (answer-bank backed).

CAVEAT -- self-test, not a live provider. I am Muse Spark, but there is no
API endpoint for me on this VM: Meta Model API access is still pending
(Peter completes it on his own device; connector custom.meta does not exist
yet). So this provider replays answers authored in-session by the agent:

  1. python3 muse_spark.py --export
     -> muse_spark_prompts.json : every single-shot prompt the batteries
        would send, each with the stable key the harness will look up.
  2. Fill {"key": "<authored answer text>"} (see muse_spark_answers.example)
     and run  python3 muse_spark.py --ingest <answers.json>
     -> entries merged into muse_spark_bank.json.
     For agentic scenarios: python3 muse_spark.py --answer-agentic
        <compare4|compare5|compare7> <scenario_id>
     runs the real run_agent loop with an interactive stdin turn function,
     so the bank keys are exactly what the harness will query at runtime.
  3. Re-run any compareN.py: "muse-spark" is scored by the identical
     scorers and loop code as the live models.

Latency is recorded as 0.0 and usage as null (in-session answers are not
live-measured). Not blind -- the same agent wrote the harness and the
answers -- so keep the "self-test" label on any report.

UPGRADE PATH: when the Meta Model API key lands in the Secure Vault as
connector custom.meta, replace the bank lookup in call_musespark/muse_turn
with a real HTTPS client. No compare script needs to change.
"""

import hashlib
import json
import os
import re
import sys

W = os.path.dirname(os.path.abspath(__file__))
BANK_PATH = os.path.join(W, "muse_spark_bank.json")
PROMPTS_PATH = os.path.join(W, "muse_spark_prompts.json")

TOOL_RE = re.compile(r"```tool\s*\n(.*?)```", re.S)
END_SENTINEL = "<<<END>>>"


def key_for(messages, tools, tag=None):
    """Stable lookup key for one model call: sha1 of the exact request.

    tag disambiguates agentic scenarios that share prompt+tools but run
    against different scripted backends (e.g. S1 vs S8).
    """
    canon = json.dumps({"m": messages, "t": tools, "tag": tag}, sort_keys=True,
                       ensure_ascii=False, default=str)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:16]


def load_bank():
    if not os.path.exists(BANK_PATH):
        return {}
    with open(BANK_PATH) as f:
        return json.load(f)


def save_bank(bank):
    with open(BANK_PATH, "w") as f:
        json.dump(bank, f, ensure_ascii=False, indent=1)


def parse_tools(text):
    """Authored turn text -> [(name, args_dict)] from ```tool blocks."""
    out = []
    for m in TOOL_RE.finditer(text):
        try:
            o = json.loads(m.group(1))
            out.append((o["name"], o.get("arguments") or {}))
        except Exception:
            pass
    return out


def clean_text(text):
    return TOOL_RE.sub("", text).strip()


def turn_from_authored(authored, key):
    """One authored turn -> (assistant_msg, norm_calls, text).

    assistant_msg/norm_calls use the exact shapes compare4.oai_turn returns,
    so bank keys computed during answering match keys at replay time.
    norm_calls args are JSON strings, as oai_turn produces them.
    """
    calls = []
    for i, (name, args) in enumerate(parse_tools(authored)):
        cid = "muse-%s-%d" % (key[:8], i)
        calls.append({"id": cid, "type": "function",
                      "function": {"name": name,
                                  "arguments": json.dumps(args, ensure_ascii=False)}})
    text = clean_text(authored)
    a_msg = {"role": "assistant", "content": text if text else None}
    if calls:
        a_msg["tool_calls"] = calls
    norm = [{"id": c["id"], "name": c["function"]["name"],
             "args": c["function"]["arguments"]} for c in calls]
    return a_msg, norm, text


def _lookup(messages, tools, tag=None):
    key = key_for(messages, tools, tag)
    bank = load_bank()
    if key not in bank:
        raise KeyError(
            "muse-spark bank has no answer for key %s (tag=%s). "
            "Run `python3 muse_spark.py --export`, fill the prompts, "
            "then `python3 muse_spark.py --ingest <answers.json>` "
            "(agentic: `--answer-agentic <module> <scenario>`)." % (key, tag))
    return key, bank[key]["text"]


# ---------------- harness entry points ----------------

def call_musespark(model, messages, tools=None):
    """Single-shot provider: compare/compare2/compare3/compare6 mains."""
    key, authored = _lookup(messages, tools)
    a_msg, _norm, text = turn_from_authored(authored, key)
    return {"text": text,
            "tool_calls": a_msg.get("tool_calls", []),
            "usage": {"in": None, "out": None},
            "latency": 0.0}


def muse_turn(messages, tools, tag=None):
    """Agentic per-turn provider: compare4.run_agent (rounds 4/5/7)."""
    key, authored = _lookup(messages, tools, tag)
    a_msg, norm, text = turn_from_authored(authored, key)
    return a_msg, norm, text, (0, 0), 0.0


def muse_tool_loop_2turn(prompt, tools, results_for):
    """Round-2 two-turn weather loop (compare2.run_tool_loop_fixed branch)."""
    m1 = [{"role": "user", "content": prompt}]
    k1, authored1 = _lookup(m1, tools)
    a_msg, _norm, _t1 = turn_from_authored(authored1, k1)
    tcs = a_msg.get("tool_calls", [])
    resmap = results_for(tcs)
    conv = m1 + [a_msg]
    for tc in tcs:
        conv.append({"role": "tool", "tool_call_id": tc["id"],
                     "content": resmap.get(tc["id"], "n/a")})
    _k2, text2 = _lookup(conv, None)
    return {"text": text2, "turn1_tool_calls": tcs,
            "usage": {"in": None, "out": None}, "latency": 0.0}


# ---------------- answering workflows ----------------

def _import(name):
    if W not in sys.path:
        sys.path.insert(0, W)
    return __import__(name)


def export_prompts():
    """Dump every single-shot prompt with its bank key."""
    out = []
    c1 = _import("compare")
    for tid, messages, tools in c1.TESTS:
        out.append({"round": 1, "test_id": tid, "key": key_for(messages, tools),
                    "messages": messages, "tools": tools})
    c2 = _import("compare2")
    for tid, prompt, tools in c2.TESTS:
        t = tools if tid == "multi_tool_parallel" else None
        messages = [{"role": "user", "content": prompt}]
        out.append({"round": 2, "test_id": tid, "key": key_for(messages, t),
                    "messages": messages, "tools": t})
    c3 = _import("compare3")
    for tid, prompt in c3.TESTS:
        messages = [{"role": "user", "content": prompt}]
        out.append({"round": 3, "test_id": tid, "key": key_for(messages, None),
                    "messages": messages, "tools": None})
    c6 = _import("compare6")
    for tid, prompt in c6.TESTS:
        messages = [{"role": "user", "content": prompt}]
        out.append({"round": 7, "test_id": tid, "key": key_for(messages, None),
                    "messages": messages, "tools": None})
    bank = load_bank()
    for e in out:
        e["answered"] = e["key"] in bank
    with open(PROMPTS_PATH, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    n_new = sum(1 for e in out if not e["answered"])
    print("wrote %s (%d prompts, %d unanswered)" % (PROMPTS_PATH, len(out), n_new))


def ingest_answers(path):
    """Merge {key: authored_text} into the bank."""
    answers = json.load(open(path))
    bank = load_bank()
    n = 0
    for key, text in answers.items():
        bank[key] = {"text": text}
        n += 1
    save_bank(bank)
    print("ingested %d answers -> %s (%d keys total)" % (n, BANK_PATH, len(bank)))


def stdin_turn(messages, tools, tag=None):
    """Interactive turn function for --answer-agentic sessions."""
    print("\n================ YOUR TURN ================")
    print(json.dumps(messages, ensure_ascii=False, indent=1))
    if tools:
        print("---- tools ----")
        print(json.dumps(tools, ensure_ascii=False, indent=1)[:3000])
    print("---- write your response below; end with a line containing only %s ----"
          % END_SENTINEL)
    sys.stdout.flush()
    lines = []
    for line in sys.stdin:
        if line.rstrip("\n") == END_SENTINEL:
            break
        lines.append(line)
    authored = "".join(lines)
    key = key_for(messages, tools, tag)
    bank = load_bank()
    bank[key] = {"text": authored}
    save_bank(bank)
    print("[banked turn under key %s tag %s]" % (key, tag))
    sys.stdout.flush()
    a_msg, norm, text = turn_from_authored(authored, key)
    return a_msg, norm, text, (0, 0), 0.0


def answer_agentic(modname, sid):
    """Run a scenario's real agent loop with stdin as the model."""
    mod = _import(modname)
    scen = {s[0]: s for s in mod.SCENARIOS}[sid]
    _sid, prompt, tools, respond, mt = scen
    if modname == "compare4" and sid == "S5_fix_bug":
        respond = mod.s5_make()
    if modname == "compare7" and respond == "warehouse":
        respond = mod.warehouse_make()
    print("scenario %s (%s), max_turns=%d -- answer each turn, end turns with %s"
          % (sid, modname, mt, END_SENTINEL))
    sys.stdout.flush()
    res = mod.run_agent("muse-spark-stdin", "muse-spark-1.3", None, None,
                        False, prompt, tools, respond, mt, tag=sid)
    print("\n================ FINAL ================")
    print("calls:", [(n, str(a)[:80]) for n, a in res["calls"]])
    print("text:", res["text"][:800])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "--export":
        export_prompts()
    elif sys.argv[1] == "--ingest" and len(sys.argv) == 3:
        ingest_answers(sys.argv[2])
    elif sys.argv[1] == "--answer-agentic" and len(sys.argv) == 4:
        answer_agentic(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        sys.exit(2)
