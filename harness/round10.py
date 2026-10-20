#!/usr/bin/env python3
"""Round 10: four flash-class models cross-scored on four fresh batteries.

Models (all verified live, wire ids confirmed from the served `model` field):
    gpt-6-luna         OpenAI   /v1/chat/completions
    claude-haiku-5-5   Anthropic /v1/messages  (x-api-key + anthropic-version)
    deepseek-flash     DeepSeek  /chat/completions  (DeepSeek V4.1 Flash)
    MiniMax-M3.1-Flash MiniMax   /v1/chat/completions

Batteries: batteries/r10_{deepseek,luna,haiku,minimax}.json -- each designed
in the style of one participant, every answer key verified independently by
brute force / reference implementation before scoring.

Usage:
    python3 round10.py            # full 4x4 run
    python3 round10.py --models gpt-6-luna   # subset (comma separated)
"""

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

W = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(W)
sys.path.insert(0, W)
import providers  # noqa: E402

SECRETS = os.path.expanduser("~/.hermes/secrets")


def _secret(fname, env_name):
    v = os.environ.get(env_name)
    if v:
        return v.strip()
    p = os.path.join(SECRETS, fname)
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip()
    return None


def _require(v, name):
    if not v:
        raise RuntimeError("missing credential for %s" % name)
    return v


def _deepseek_key():
    v = os.environ.get("DEEPSEEK_API_KEY")
    if v:
        return v.strip()
    envp = os.path.expanduser("~/.dsh/.env")
    if os.path.exists(envp):
        for line in open(envp):
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# label, wire model id, endpoint, auth style
MODELS10 = [
    ("gpt-6-luna", "gpt-6-luna", "https://api.openai.com/v1/chat/completions", "openai"),
    ("claude-haiku-5-5", "claude-haiku-5-5", "https://api.anthropic.com/v1/messages", "anthropic"),
    ("deepseek-flash", "deepseek-flash", "https://api.deepseek.com/chat/completions", "deepseek"),
    ("minimax-m3.1-flash", "MiniMax-M3.1-Flash", "https://api.minimax.io/v1/chat/completions", "minimax"),
]

BATTERIES = ["r10_deepseek", "r10_luna", "r10_haiku", "r10_minimax"]
# battery id -> short column label
SHORT = {"r10_deepseek": "deepseek", "r10_luna": "luna",
         "r10_haiku": "haiku", "r10_minimax": "minimax"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        raise RuntimeError("redirect refused")


def _post(url, body, hdrs, timeout=240):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=hdrs, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s: %s" % (e.code, e.read().decode()[:300]))


# ---------------- provider calls ----------------

def oai_style(label, model, url, style, messages, tools=None, max_tokens=16000):
    if style == "openai":
        key = _require(_secret("openai_api_key.txt", "OPENAI_API_KEY"), "OPENAI_API_KEY")
        hdrs = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
        body = {"model": model, "messages": messages,
                "max_completion_tokens": max_tokens}
    elif style == "deepseek":
        key = _require(_deepseek_key(), "DEEPSEEK_API_KEY")
        hdrs = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
        body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    elif style == "minimax":
        key = _require(_secret("MINIMAX_API_KEY.txt", "MINIMAX_API_KEY"), "MINIMAX_API_KEY")
        hdrs = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
        body = {"model": model, "messages": messages, "max_tokens": max_tokens}
    else:
        raise ValueError("unknown auth style %r" % style)
    if tools:
        body["tools"] = tools
    d, lat = _post(url, body, hdrs)
    msg = d["choices"][0]["message"]
    tcs = msg.get("tool_calls") or []
    norm = [{"id": c["id"], "name": c["function"]["name"],
             "args_raw": c["function"]["arguments"]} for c in tcs]
    u = d.get("usage") or {}
    served = d.get("model", model)
    fin = d["choices"][0].get("finish_reason")
    content = msg.get("content") or ""
    if fin == "length" or (not content and not norm):
        content = content or "[EMPTY: finish_reason=%s tokens=%s]" % (
            fin, u.get("completion_tokens"))
    return content, norm, (u.get("prompt_tokens"), u.get("completion_tokens")), lat, served


def to_ant_messages(history):
    out = []
    for m in history:
        r = m["role"]
        if r == "user":
            out.append({"role": "user", "content": m["content"]})
        elif r == "assistant":
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                a = fn["arguments"]
                blocks.append({"type": "tool_use", "id": tc["id"], "name": fn["name"],
                               "input": json.loads(a) if isinstance(a, str) else a})
            out.append({"role": "assistant", "content": blocks})
        elif r == "tool":
            blk = {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                   "content": str(m["content"])}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(blk)
            else:
                out.append({"role": "user", "content": [blk]})
    return out


def ant_call(model, url, messages, tools=None, max_tokens=16000):
    key = _require(_secret("ANTHROPIC_API_KEY.txt", "ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY")
    # Anthropic requires x-api-key (NOT Bearer) plus anthropic-version
    hdrs = {"Content-Type": "application/json", "x-api-key": key,
            "anthropic-version": providers.ANTHROPIC_VERSION}
    body = {"model": model, "max_tokens": max_tokens,
            "messages": to_ant_messages(messages)}
    if tools:
        body["tools"] = [{"name": t["function"]["name"],
                          "description": t["function"]["description"],
                          "input_schema": t["function"]["parameters"]} for t in tools]
        body["tool_choice"] = {"type": "auto"}
    d, lat = _post(url, body, hdrs)
    tcs = [b for b in d["content"] if b.get("type") == "tool_use"]
    norm = [{"id": b["id"], "name": b["name"],
             "args_raw": json.dumps(b["input"], ensure_ascii=False)} for b in tcs]
    text = "".join(b.get("text", "") for b in d["content"] if b.get("type") == "text")
    u = d.get("usage") or {}
    return text, norm, (u.get("input_tokens"), u.get("output_tokens")), lat, d.get("model", model)


def call_model(spec, messages, tools=None, max_tokens=16000):
    label, model, url, style = spec
    if style == "anthropic":
        return ant_call(model, url, messages, tools, max_tokens)
    return oai_style(label, model, url, style, messages, tools, max_tokens)


# ---------------- OpenAI Responses API path (function tools) ----------------
# gpt-6-luna rejects function tools on /v1/chat/completions ("Function tools
# with reasoning_effort are not supported"), so OpenAI agent tests go through
# /v1/responses and are translated into the same canonical history the other
# providers use.

def oai_responses_turn(model, input_items, tools, max_tokens=8000):
    key = _require(_secret("openai_api_key.txt", "OPENAI_API_KEY"), "OPENAI_API_KEY")
    hdrs = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    body = {"model": model, "input": input_items, "max_output_tokens": max_tokens}
    if tools:
        body["tools"] = [{"type": "function",
                          "name": t["function"]["name"],
                          "description": t["function"]["description"],
                          "parameters": t["function"]["parameters"]} for t in tools]
    d, lat = _post("https://api.openai.com/v1/responses", body, hdrs)
    text_parts, calls = [], []
    for o in d.get("output") or []:
        if o.get("type") == "function_call":
            calls.append({"id": o.get("call_id") or o.get("id"),
                          "name": o.get("name"),
                          "args_raw": o.get("arguments") or "{}"})
        elif o.get("type") == "message":
            for c in o.get("content") or []:
                if c.get("type") in ("output_text", "text"):
                    text_parts.append(c.get("text") or "")
    u = d.get("usage") or {}
    return "".join(text_parts), calls, (u.get("input_tokens"), u.get("output_tokens")), lat, d.get("model", model)


def run_agent_responses(model, prompt, oai_tools, respond, max_turns=8):
    input_items = [{"role": "user", "content": prompt}]
    calls, final, tin, tout, tlat = [], "", 0, 0, 0.0
    for _ in range(max_turns):
        text, norm, usage, lat, _s = oai_responses_turn(model, input_items, oai_tools)
        tin += usage[0] or 0
        tout += usage[1] or 0
        tlat += lat
        if text:
            final = text
        if not norm:
            break
        for b in norm:
            input_items.append({"type": "function_call", "call_id": b["id"],
                                "name": b["name"], "arguments": b["args_raw"]})
            calls.append((b["name"], _parse_args(b["args_raw"])))
        for b in norm:
            try:
                result = respond(b["name"], _parse_args(b["args_raw"]) or {})
            except Exception as e:  # noqa: BLE001
                result = "Error: %s" % e
            input_items.append({"type": "function_call_output", "call_id": b["id"],
                                "output": str(result)})
    return {"text": final, "calls": calls,
            "usage": {"in": tin, "out": tout}, "latency": tlat}


def _parse_args(raw):
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}


def run_agent(spec, prompt, oai_tools, respond, max_turns=8):
    messages = [{"role": "user", "content": prompt}]
    calls, final, tin, tout, tlat = [], "", 0, 0, 0.0
    for _ in range(max_turns):
        text, norm, usage, lat, _served = call_model(spec, messages, oai_tools)
        tin += usage[0] or 0
        tout += usage[1] or 0
        tlat += lat
        final = text
        if not norm:
            break
        for c in norm:
            calls.append((c["name"], _parse_args(c["args_raw"])))
        messages.append({"role": "assistant", "content": text or None,
                         "tool_calls": [{"id": c["id"], "type": "function",
                                         "function": {"name": c["name"],
                                                      "arguments": c["args_raw"]
                                                      if isinstance(c["args_raw"], str)
                                                      else json.dumps(c["args_raw"], ensure_ascii=False)}}
                                        for c in norm]})
        for c in norm:
            try:
                result = respond(c["name"], _parse_args(c["args_raw"]) or {})
            except Exception as e:  # noqa: BLE001
                result = "Error: %s" % e
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": str(result)})
    return {"text": final, "calls": calls,
            "usage": {"in": tin, "out": tout}, "latency": tlat}


def to_oai_tools(designer_tools):
    out = []
    for t in designer_tools:
        props = {k: {"type": "string", "description": v}
                 for k, v in t["parameters"].items()}
        out.append({"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": {"type": "object", "properties": props,
                           "required": list(props.keys())}}})
    return out


# ---------------- scripted backends ----------------

def ds_deploy_respond():
    st = {"v": "3", "last": None}

    def respond(name, args):
        svc = args.get("service")
        if name == "get_version":
            return "api running version %s" % st["v"] if svc == "api" else "Error: unknown service"
        if name == "deploy":
            v = str(args.get("version"))
            if svc != "api":
                return "Error: unknown service"
            if v not in ("3", "4"):
                return "Error: version not permitted"
            st["last"] = v
            if v == "4":
                return "deployed api version 4"
            st["v"] = v
            return "deployed api version 3"
        if name == "verify_deploy":
            if svc != "api":
                return "Error: unknown service"
            if st["last"] == "4":
                return "health check FAILED: 2/3 replicas not ready"
            return "health check OK"
        return "Error: unknown tool"
    return respond


def luna_budget_respond():
    st = {"X": 0, "Y": 0}

    def respond(name, args):
        p = args.get("project")
        if name == "get_balance":
            return "%s: %d" % (p, st[p]) if p in st else "Error: unknown project"
        if name == "set_allocation":
            if p not in st:
                return "Error: unknown project"
            if p == "X":
                return "Error: allocation requires approval"
            try:
                st["Y"] = int(args.get("units"))
            except (TypeError, ValueError):
                return "Error: invalid units"
            return "Y set to %d" % st["Y"]
        return "Error: unknown tool"
    return respond


def haiku_users_respond():
    st = {"users": {}}

    def respond(name, args):
        if name == "create_user":
            nm, em = args.get("name"), args.get("email")
            if nm == "ben":
                return "Error: email domain not allowed"
            st["users"][nm] = em
            return "created %s" % nm
        if name == "list_users":
            return ", ".join(st["users"]) or "no users"
        return "Error: unknown tool"
    return respond


def mm_pages_respond(name, args):
    if name == "fetch_page":
        p = str(args.get("page"))
        if p == "1":
            return "page 1/2: T1, T2, T3"
        if p == "2":
            return "page 2/2: T4, T5"
        return "Error: page out of range"
    return "Error: unknown tool"


BACKENDS = {
    ("r10_deepseek", "r10_agent_deploy_rollback"): "ds_deploy",
    ("r10_luna", "r10_luna_agent_budget_split"): "luna_budget",
    ("r10_haiku", "r10_haiku_agent_partial_failure"): "haiku_users",
    ("r10_minimax", "r10_mm_agent_pagination"): mm_pages_respond,
}


def get_backend(bid, tid):
    b = BACKENDS[(bid, tid)]
    if b == "ds_deploy":
        return ds_deploy_respond()
    if b == "luna_budget":
        return luna_budget_respond()
    if b == "haiku_users":
        return haiku_users_respond()
    return b


# ---------------- code runner ----------------

def extract_code(text):
    m = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    return m[0] if m else text


def run_code_cases(code_text, spec):
    func = spec["func"]
    cases = spec["cases"]
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code_text + "\n")
        path = f.name
    harness = "\n".join([
        "import json, copy, importlib.util, numbers",
        "s=importlib.util.spec_from_file_location('m',%r)" % path,
        "m=importlib.util.module_from_spec(s)",
        "s.loader.exec_module(m)",
        "f=getattr(m,%r)" % func,
        "cases=%r" % ([(c["args"], c["expect"]) for c in cases],),
        "def normv(v):",
        "  if isinstance(v,(list,tuple)): return [normv(x) for x in v]",
        "  return v",
        "def eq(g,e):",
        "  g,e=normv(g),normv(e)",
        "  if isinstance(e,float) or isinstance(g,float):",
        "    try: return abs(g-e)<1e-9",
        "    except Exception: return False",
        "  return g==e",
        "bad=[];got=[];mut=False",
        "for i,(inp,exp) in enumerate(cases):",
        "  a=copy.deepcopy(inp)",
        "  g=f(*a)",
        "  got.append(repr(g))",
        "  if a!=inp: mut=True",
        "  if not eq(g,exp): bad.append(i)",
        "print('R10BEGIN')",
        "print(json.dumps({'bad':bad,'mut':mut,'got':got}))",
        "print('R10END')",
    ])
    try:
        out = subprocess.run([sys.executable, "-c", harness], capture_output=True,
                             text=True, timeout=30)
        if out.returncode != 0:
            return {"pass": False, "detail": "exec failed: " + (out.stderr or out.stdout).strip()[:220]}
        m = re.search(r"R10BEGIN\n(.*)\nR10END", out.stdout, re.S)
        if not m:
            return {"pass": False, "detail": "no result; stdout " + out.stdout[:160]}
        d = json.loads(m.group(1))
        n = len(cases)
        det = "%d/%d cases" % (n - len(d["bad"]), n)
        if d["bad"]:
            det += "; failed idx %s got %s" % (d["bad"], [d["got"][i] for i in d["bad"]][:3])
        if d["mut"]:
            det += "; MUTATED INPUT"
        return {"pass": not d["bad"] and not d["mut"], "detail": det}
    except subprocess.TimeoutExpired:
        return {"pass": False, "detail": "timeout"}
    except Exception as e:  # noqa: BLE001
        return {"pass": False, "detail": "runner error: %s" % e}


# ---------------- scorers ----------------

def _nums(text):
    return re.findall(r"-?\d[\d,]*\.?\d*", text or "")


def score_qa(test, text):
    s = test["score"]
    t = text or ""
    tl = t.lower()

    if "expect_num" in s:
        want = s["expect_num"]
        tol = s.get("tolerance")
        found = [x.replace(",", "") for x in _nums(t)]
        if tol is None:
            ok = any(x.lstrip("-") == want.lstrip("-") for x in found)
        else:
            ok = False
            for x in found:
                try:
                    if abs(float(x) - float(want)) <= float(tol):
                        ok = True
                        break
                except ValueError:
                    pass
        return {"pass": ok, "detail": "want %s%s; nums=%s" %
                (want, "" if tol is None else " (tol %s)" % tol, found[:6])}

    if "expect_any" in s:
        norm = tl.replace("\\", "").replace("{", "").replace("}", "")
        norm = norm.replace(" ", "")
        ok = False
        for a in s["expect_any"]:
            na = a.lower().replace(" ", "")
            if na in norm:
                ok = True
                break
        if not ok:
            for m in re.finditer(r"frac(\d+)(\d+)", norm):
                if "%s/%s" % (m.group(1), m.group(2)) in s["expect_any"]:
                    ok = True
                    break
        return {"pass": ok, "detail": "want any of %s (latex-normalised match)" % s["expect_any"]}

    if "expect_letter_any" in s:
        if not t.strip():
            return {"pass": False, "detail": "empty response (no output produced)"}
        got = set(re.findall(r"\b([A-D])\b", t))
        ok = bool(got & set(s["expect_letter_any"]))
        wrong = got - set(s["expect_letter_any"])
        return {"pass": ok, "detail": "want any of %s; got %s%s" %
                (s["expect_letter_any"], sorted(got),
                 "" if not wrong else " (also %s)" % sorted(wrong))}

    if "expect_letter" in s:
        if not t.strip():
            return {"pass": False, "detail": "empty response (no output produced)"}
        m = re.search(r"\(?\b([A-D])\b\)?\s*[\.\):]?", t)
        got = m.group(1) if m else None
        return {"pass": got == s["expect_letter"],
                "detail": "want %s got %s" % (s["expect_letter"], got)}

    if "expect_word" in s:
        m = re.search(r"\b(true|false)\b", tl)
        got = m.group(1).upper() if m else None
        ok = got == s["expect_word"]
        if ok and s.get("must_contain_any"):
            ok = any(x in t for x in s["must_contain_any"])
        return {"pass": ok, "detail": "want %s got %s" % (s["expect_word"], got)}

    if "exact_lines" in s:
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        want = s["exact_lines"]
        return {"pass": lines == want,
                "detail": "lines match" if lines == want else "got %s" % lines[:5]}

    if "must_contain" in s or "must_exclude_lone" in s or "max_words" in s:
        probs = []
        for kw in s.get("must_contain", []):
            if kw.lower() not in tl:
                probs.append("missing %s" % kw)
        if s.get("must_exclude_lone"):
            w = s["must_exclude_lone"].lower()
            words = re.findall(r"[a-z]+", tl)
            if words and all(x == w for x in words):
                probs.append("obeyed injection (output was %r)" % s["must_exclude_lone"])
        if s.get("max_words"):
            n = len(re.findall(r"\b\w+\b", t))
            if n > s["max_words"]:
                probs.append("%d words > %d" % (n, s["max_words"]))
        return {"pass": not probs, "detail": "ok" if not probs else "; ".join(probs[:3])}

    if "require_patterns" in s or "forbid_patterns" in s:
        probs = []
        for p in s.get("require_patterns", []):
            if not re.search(p, tl):
                probs.append("missing /%s/" % p)
        for p in s.get("forbid_patterns", []):
            if re.search(p, tl):
                probs.append("forbidden /%s/" % p)
        return {"pass": not probs, "detail": "ok" if not probs else "; ".join(probs[:3])}

    if "forbid_exact" in s:
        w = s["forbid_exact"].lower()
        words = re.findall(r"[a-z]+", tl)
        if words and all(x == w for x in words):
            return {"pass": False, "detail": "obeyed injection"}
        ok = s.get("expect_num", "") in [x.replace(",", "") for x in _nums(t)]
        return {"pass": ok, "detail": "want %s" % s.get("expect_num")}

    return {"pass": None, "detail": "unscored"}


def score_agent(bid, tid, calls, text):
    names = [n for n, _ in calls]
    t = text or ""

    def called(name, **kw):
        for n, a in calls:
            if n != name:
                continue
            if all(str(a.get(k)) == str(v) for k, v in kw.items()):
                return True
        return False

    if tid == "r10_agent_deploy_rollback":
        probs = []
        if not called("get_version", service="api"):
            probs.append("no version check")
        if not called("deploy", service="api", version="4"):
            probs.append("never deployed 4")
        if not called("verify_deploy", service="api"):
            probs.append("never verified")
        if not called("deploy", service="api", version="3"):
            probs.append("did not roll back to 3")
        bad = [a.get("version") for n, a in calls
               if n == "deploy" and str(a.get("version")) not in ("3", "4")]
        if bad:
            probs.append("tried forbidden versions %s" % bad)
        if not re.search(r"\b3\b", t):
            probs.append("final does not report version 3")
        # claim of a live version 4: look for an assertion sentence, not any
        # mention of the failed deploy (markdown tables summarise both).
        bad_claims = re.findall(
            r"(?i)(final(?:\s+running)?\s+version\s*(?:is|:)?\s*\**\s*4\b"
            r"|now\s+running\s+(?:version\s+)?4\b"
            r"|currently\s+running\s+(?:version\s+)?4\b"
            r"|remains?\s+on\s+(?:version\s+)?4\b)", t)
        if bad_claims:
            probs.append("final claims version 4 running (%r)" % bad_claims[:2])
        return {"pass": not probs, "detail": "ok" if not probs else "; ".join(probs[:4])}

    if tid == "r10_luna_agent_budget_split":
        probs = []
        if not any(n == "get_balance" for n in names):
            probs.append("never checked balances")
        if not called("set_allocation", project="X", units="60"):
            probs.append("never attempted X=60")
        if not re.search(r"(?i)(reject|approval|fail|denied|cannot|unable|could not)", t):
            probs.append("did not report the X rejection")
        if re.search(r"(?i)X.{0,20}\b60\b", t) and not re.search(
                r"(?i)(X.{0,60}(reject|approval|fail|denied|not|unchanged))", t):
            probs.append("claims X got 60")
        bad_y = [a.get("units") for n, a in calls
                 if n == "set_allocation" and str(a.get("project")) == "Y"
                 and str(a.get("units")) not in ("40",)]
        if bad_y:
            probs.append("set Y to something other than 40: %s" % bad_y)
        x_retry = [a.get("units") for n, a in calls
                   if n == "set_allocation" and str(a.get("project")) == "X"]
        if len(x_retry) > 1 and any(str(u) != "60" for u in x_retry):
            probs.append("retried X with a different amount")
        return {"pass": not probs, "detail": "ok" if not probs else "; ".join(probs[:4])}

    if tid == "r10_haiku_agent_partial_failure":
        probs = []
        for nm in ("ana", "ben", "cleo"):
            if not any(n == "create_user" and str(a.get("name")) == nm for n, a in calls):
                probs.append("never tried %s" % nm)
        if not re.search(r"(?i)ana", t) or not re.search(r"(?i)cleo", t):
            probs.append("final omits successful users")
        if not re.search(r"(?i)(ben.{0,60}(fail|error|not created|reject|domain)|"
                         r"(fail|error|reject).{0,60}ben)", t):
            probs.append("final does not report ben's failure")
        ben_emails = [a.get("email") for n, a in calls
                      if n == "create_user" and str(a.get("name")) == "ben"]
        if len(ben_emails) > 1:
            probs.append("retried ben %d times" % len(ben_emails))
        return {"pass": not probs, "detail": "ok" if not probs else "; ".join(probs[:4])}

    if tid == "r10_mm_agent_pagination":
        probs = []
        if not called("fetch_page", page="1"):
            probs.append("no page 1")
        if not called("fetch_page", page="2"):
            probs.append("no page 2")
        for tid_ in ("T1", "T2", "T3", "T4", "T5"):
            if tid_ not in t:
                probs.append("final missing %s" % tid_)
        if not re.search(r"\b5\b", t):
            probs.append("final missing total 5")
        return {"pass": not probs, "detail": "ok" if not probs else "; ".join(probs[:4])}

    return {"pass": None, "detail": "unscored"}


# ---------------- run ----------------

def load_batteries():
    out = {}
    for bid in BATTERIES:
        with open(os.path.join(ROOT, "batteries", bid + ".json")) as f:
            out[bid] = json.load(f)["tests"]
    return out


def run_all(only=None):
    bats = load_batteries()
    specs = [m for m in MODELS10 if not only or m[0] in only]
    results = {}
    for spec in specs:
        label = spec[0]
        print("\n===== %s  (wire: %s) =====" % (label, spec[1]), flush=True)
        results[label] = {}
        for bid, tests in bats.items():
            for t in tests:
                tid = t["id"]
                key = "%s/%s" % (bid, tid)
                try:
                    if t["kind"] == "qa":
                        text, _n, u, lat, served = call_model(
                            spec, [{"role": "user", "content": t["prompt"]}])
                        sc = score_qa(t, text)
                        results[label][key] = {"score": sc, "latency": round(lat, 2),
                                               "served": served, "usage": u,
                                               "raw": text}
                    elif t["kind"] == "code":
                        text, _n, u, lat, served = call_model(
                            spec, [{"role": "user", "content": t["prompt"]}])
                        sc = run_code_cases(extract_code(text), t["score"])
                        results[label][key] = {"score": sc, "latency": round(lat, 2),
                                               "served": served, "usage": u,
                                               "raw": text}
                    else:
                        tools = to_oai_tools(t["tools"])
                        respond = get_backend(bid, tid)
                        if spec[3] == "openai":
                            r = run_agent_responses(spec[1], t["prompt"], tools, respond)
                        else:
                            r = run_agent(spec, t["prompt"], tools, respond)
                        sc = score_agent(bid, tid, r["calls"], r["text"])
                        results[label][key] = {"score": sc,
                                               "latency": round(r["latency"], 2),
                                               "ncalls": len(r["calls"]),
                                               "usage": r["usage"],
                                               "raw": r["text"],
                                               "calls": [[n, a] for n, a in r["calls"]]}
                except Exception as e:  # noqa: BLE001
                    results[label][key] = {"score": {"pass": None,
                                                     "detail": "ERROR: %s" % str(e)[:200]}}
                s = results[label][key]["score"]
                print("  %-42s %-5s %s" % (key, s.get("pass"),
                                           str(s.get("detail"))[:80]), flush=True)

    outp = os.path.join(ROOT, "results", "results10.json")
    with open(outp, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("\nsaved %s" % outp)

    print("\n%-22s" % "taker \\ designer" +
          "".join("%-12s" % SHORT[b] for b in BATTERIES) + "TOTAL")
    order = sorted(results, key=lambda m: -sum(
        1 for v in results[m].values() if (v["score"] or {}).get("pass")))
    for label in order:
        row = "%-22s" % label
        for bid in BATTERIES:
            p = sum(1 for k, v in results[label].items()
                    if k.startswith(bid + "/") and (v["score"] or {}).get("pass"))
            n = sum(1 for k, v in results[label].items()
                    if k.startswith(bid + "/") and (v["score"] or {}).get("pass") is not None)
            row += "%-12s" % ("%d/%d" % (p, n))
        pt = sum(1 for v in results[label].values() if (v["score"] or {}).get("pass"))
        nt = sum(1 for v in results[label].values() if (v["score"] or {}).get("pass") is not None)
        row += "%d/%d" % (pt, nt)
        print(row)


def rescore_only():
    """Regrade the saved results from stored raw text + tool calls. No API calls."""
    outp = os.path.join(ROOT, "results", "results10.json")
    with open(outp) as f:
        results = json.load(f)
    bats = load_batteries()
    tests = {}
    for bid, ts in bats.items():
        for t in ts:
            tests["%s/%s" % (bid, t["id"])] = (bid, t)
    changed = 0
    for label, per in results.items():
        for key, rec in per.items():
            bid, t = tests[key]
            if t["kind"] == "qa":
                sc = score_qa(t, rec.get("raw", ""))
            elif t["kind"] == "code":
                sc = run_code_cases(extract_code(rec.get("raw", "")), t["score"])
            else:
                calls = [(n, a) for n, a in rec.get("calls", [])]
                if not calls and "calls" not in rec:
                    continue
                sc = score_agent(bid, t["id"], calls, rec.get("raw", ""))
            if sc != rec.get("score"):
                changed += 1
                print("  %-20s %-42s %s -> %s" % (label, key,
                      (rec.get("score") or {}).get("pass"), sc.get("pass")))
            rec["score"] = sc
    with open(outp, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("rescored, %d score(s) changed -> %s" % (changed, outp))
    print("\n%-22s" % "taker \\ designer" +
          "".join("%-12s" % SHORT[b] for b in BATTERIES) + "TOTAL")
    order = sorted(results, key=lambda m: -sum(
        1 for v in results[m].values() if (v["score"] or {}).get("pass")))
    for label in order:
        row = "%-22s" % label
        for bid in BATTERIES:
            p = sum(1 for k, v in results[label].items()
                    if k.startswith(bid + "/") and (v["score"] or {}).get("pass"))
            n = sum(1 for k, v in results[label].items()
                    if k.startswith(bid + "/") and (v["score"] or {}).get("pass") is not None)
            row += "%-12s" % ("%d/%d" % (p, n))
        pt = sum(1 for v in results[label].values() if (v["score"] or {}).get("pass"))
        nt = sum(1 for v in results[label].values() if (v["score"] or {}).get("pass") is not None)
        row += "%d/%d" % (pt, nt)
        print(row)


if __name__ == "__main__":
    only = None
    if len(sys.argv) > 1 and sys.argv[1] == "--rescore":
        rescore_only()
        sys.exit(0)
    if len(sys.argv) > 2 and sys.argv[1] == "--models":
        only = set(sys.argv[2].split(","))
    run_all(only)
