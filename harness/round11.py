#!/usr/bin/env python3
"""Round 11: MiniMax-M3.1-Flash vs DeepSeek-V4.1-Flash, live head-to-head.

Both models are driven through the SAME wire protocol -- the Anthropic-compatible
/messages endpoint -- with the same thinking-effort knob (output_config.effort),
so the comparison is not a protocol artefact.

Models (wire ids confirmed from the served `model` field of a live call):
    MiniMax-M3.1-Flash  minimax      https://api.minimax.io/anthropic/v1/messages
    deepseek-flash      deepseek     https://api.deepseek.com/anthropic/v1/messages
                        (= DeepSeek-V4.1-Flash-1017; legacy id deepseek-v4-flash is
                         accepted but served by this model)

Batteries: batteries/r11_{deepseek,minimax,shared}.json -- one battery per
designer (each written in that participant's own style) plus a neutral shared
battery.  Every answer key is reproduced independently by
harness/verify_r11_keys.py before it is allowed to score anyone.

Usage:
    python3 round11.py                     # full 2 x 3 run
    python3 round11.py --models deepseek-flash
    python3 round11.py --rescore           # regrade from stored raw text, no API calls
"""

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

SECRETS = os.path.expanduser("~/.hermes/secrets")
RESULTS = os.path.join(ROOT, "results", "results11.json")

# label, wire id, anthropic-compatible endpoint, key source, effort
MODELS11 = [
    ("minimax-m3.1-flash", "MiniMax-M3.1-Flash",
     "https://api.minimax.io/anthropic/v1/messages", "minimax"),
    ("deepseek-flash", "deepseek-flash",
     "https://api.deepseek.com/anthropic/v1/messages", "deepseek"),
]

BATTERIES = ["r11_deepseek", "r11_minimax", "r11_shared"]
SHORT = {"r11_deepseek": "ds-style", "r11_minimax": "mm-style", "r11_shared": "shared"}

EFFORT = "max"          # same knob, same value for both
MAX_TOKENS = 16000


def _secret(fname, env_name):
    v = os.environ.get(env_name)
    if v:
        return v.strip()
    p = os.path.join(SECRETS, fname)
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip()
    return None


def _deepseek_key():
    v = os.environ.get("DEEPSEEK_API_KEY")
    if v:
        return v.strip()
    for p in (os.path.expanduser("~/.hermes/.env"), os.path.expanduser("~/.dsh/.env")):
        if os.path.exists(p):
            for line in open(p):
                if line.strip().startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _key_for(kind):
    if kind == "minimax":
        k = _secret("MINIMAX_API_KEY.txt", "MINIMAX_API_KEY")
    else:
        k = _deepseek_key()
    if not k:
        raise RuntimeError("missing credential for %s" % kind)
    return k


def _post(url, body, hdrs, timeout=300, attempts=4):
    last = None
    for i in range(attempts):
        if i:
            time.sleep(4 * i)   # back off: rapid-fire TLS handshakes get cut
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers=hdrs, method="POST")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode()), time.perf_counter() - t0
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            if e.code in (429, 500, 502, 503, 529) and i < attempts - 1:
                last = "HTTP %s: %s" % (e.code, detail)
                time.sleep(3 * (i + 1))
                continue
            raise RuntimeError("HTTP %s: %s" % (e.code, detail))
        except Exception as e:  # noqa: BLE001
            last = "transport error: %s" % e
            if i < attempts - 1:
                time.sleep(3 * (i + 1))
                continue
            raise RuntimeError(last)
    raise RuntimeError(last or "unknown transport failure")


# ---------------------------------------------------------------- provider call

def ant_turn(spec, messages, tools=None, max_tokens=MAX_TOKENS):
    """One Anthropic-format turn. Returns (text, tool_calls, usage, latency, served)."""
    label, model, url, kind = spec
    hdrs = {"Content-Type": "application/json",
            "x-api-key": _key_for(kind),
            "anthropic-version": "2023-06-01"}
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    body["output_config"] = {"effort": EFFORT}
    if tools:
        body["tools"] = [{"name": t["name"], "description": t["description"],
                           "input_schema": {"type": "object",
                                            "properties": {k: {"type": "string",
                                                               "description": v}
                                                           for k, v in t["parameters"].items()},
                                            "required": list(t["parameters"].keys())}}
                          for t in tools]
        body["tool_choice"] = {"type": "auto"}
    d, lat = _post(url, body, hdrs)
    blocks = d.get("content", [])
    text = "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
    thinking = sum(len(b.get("thinking") or "") for b in blocks if b.get("type") == "thinking")
    calls = [{"id": b.get("id"), "name": b["name"], "args": b.get("input") or {}}
             for b in blocks if b.get("type") == "tool_use"]
    u = d.get("usage") or {}
    # NOTE: input_tokens EXCLUDES cache_read_input_tokens on both vendors --
    # a 113 KB prompt reports ~100 input tokens plus ~15 000 cache-read.
    # Record both so prompt volume is not understated.
    usage = {"in": u.get("input_tokens") or 0,
             "cache_read": u.get("cache_read_input_tokens") or 0,
             "out": u.get("output_tokens") or 0,
             "think_chars": thinking}
    served = d.get("model", model)
    if not text and not calls and d.get("stop_reason") == "max_tokens":
        text = "[EMPTY: stop_reason=max_tokens out_tokens=%s]" % u.get("output_tokens")
    # raw_blocks keeps the thinking block so the assistant turn can be echoed
    # back verbatim -- DeepSeek's Anthropic endpoint requires it.
    return text, calls, usage, lat, served, blocks


def run_agent(spec, prompt, tools, respond, max_turns=12):
    messages = [{"role": "user", "content": prompt}]
    calls, final = [], ""
    tin = tout = tcr = 0
    tlat = 0.0
    for _ in range(max_turns):
        text, norm, usage, lat, _served, raw_blocks = ant_turn(spec, messages, tools)
        tin += usage["in"]
        tcr += usage.get("cache_read", 0)
        tout += usage["out"]
        tlat += lat
        final = text
        if not norm:
            break
        # Echo the assistant turn back VERBATIM (thinking + text + tool_use):
        # DeepSeek's Anthropic endpoint rejects a continuation whose
        # content[].thinking was dropped ("must be passed back to the API").
        messages.append({"role": "assistant", "content": raw_blocks})
        results = []
        for c in norm:
            try:
                res = respond(c["name"], c["args"] or {})
            except Exception as e:  # noqa: BLE001
                res = "Error: %s" % e
            calls.append((c["name"], c["args"], str(res)))
            results.append({"type": "tool_result", "tool_use_id": c.get("id") or "",
                            "content": str(res)})
        messages.append({"role": "user", "content": results})
    return {"text": final, "calls": calls,
            "usage": {"in": tin, "cache_read": tcr, "out": tout}, "latency": tlat}


# ------------------------------------------------------------ scripted backend

def dental_respond(name, args, state):
    """Stateful scripted backend.  `state` is a per-run dict so bookings
    persist across calls -- a fresh set per call would make bookings vanish
    and the model would be right to distrust the tool."""
    slots = {
        "dental": ["09:00", "10:30", "14:00", "16:30"],
        "physio": ["08:00", "11:00", "15:00"],
    }
    taken = state["taken"]
    if name == "list_slots":
        svc = str(args.get("service", "")).strip().lower()
        if svc not in slots:
            return "Error: unknown service '%s'. Available: dental, physio" % args.get("service")
        rows = []
        for s in slots[svc]:
            rows.append("%s %s" % (s, "booked" if (svc, s) in taken else "open"))
        return "%s slots: %s" % (svc, "; ".join(rows))
    if name == "book_slot":
        svc = str(args.get("service", "")).strip().lower()
        slot = str(args.get("slot", "")).strip()
        if svc not in slots:
            return "Error: unknown service '%s'" % args.get("service")
        if slot not in slots[svc]:
            return "Error: unknown slot '%s'" % slot
        if (svc, slot) in taken:
            return "Error: slot %s is already booked" % slot
        taken.add((svc, slot))
        return "booked %s %s" % (svc, slot)
    if name == "list_bookings":
        if not taken:
            return "no bookings"
        return "; ".join("%s %s" % (s, t) for s, t in sorted(taken))
    return "Error: unknown tool"


def dental_backend():
    """Fresh state for one agent run.  10:30 and 16:30 start booked, so the
    earliest OPEN dental slot is 09:00."""
    state = {"taken": {("dental", "10:30"), ("dental", "16:30")}}

    def respond(name, args):
        return dental_respond(name, args, state)
    return respond


BACKENDS = {("r11_minimax", "r11_mm_agent_dental_booking"): dental_backend}


def get_backend(bid, tid):
    """BACKENDS maps to a FACTORY that returns a fresh respond callable.
    Calling the factory (not the factory itself) is the whole point: a
    backend whose state resets per call makes bookings vanish and the model
    is right to distrust it."""
    return BACKENDS[(bid, tid)]()


def preflight_backends():
    """Exercise every scripted backend BEFORE spending model calls.  A broken
    backend used to masquerade as a model failure (both models correctly
    reported 'the tools are broken')."""
    problems = []
    for (bid, tid), factory in BACKENDS.items():
        try:
            respond = factory()
            r1 = respond("list_slots", {"service": "dental"})
            r2 = respond("book_slot", {"service": "dental", "slot": "09:00"})
            r3 = respond("list_bookings", {})
            for label, r in (("list_slots", r1), ("book_slot", r2), ("list_bookings", r3)):
                if not isinstance(r, str) or not r.strip():
                    problems.append("%s/%s %s -> %r" % (bid, tid, label, r))
                elif r.startswith("Error: dental_backend"):
                    problems.append("%s/%s %s -> backend factory mis-called" % (bid, tid, label))
            if "09:00" not in r3:
                problems.append("%s/%s booking did not persist: %r" % (bid, tid, r3))
            print("preflight %s/%s ok: %s | %s | %s" % (bid, tid, r1[:60], r2[:40], r3[:40]))
        except Exception as e:  # noqa: BLE001
            problems.append("%s/%s raised %s" % (bid, tid, e))
    if problems:
        for p in problems:
            print("PREFLIGHT FAIL:", p)
        raise SystemExit("backend preflight failed -- refusing to spend model calls")
    print("backend preflight passed")


# ---------------------------------------------------------------- code runner

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
        "import json, copy, importlib.util",
        "s=importlib.util.spec_from_file_location('m',%r)" % path,
        "m=importlib.util.module_from_spec(s)",
        "s.loader.exec_module(m)",
        "f=getattr(m,%r)" % func,
        "cases=%r" % ([(c[:-1], c[-1]) for c in cases],),
        "def normv(v):",
        "  if isinstance(v,(list,tuple)): return [normv(x) for x in v]",
        "  if isinstance(v,dict): return {k:normv(x) for k,x in v.items()}",
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
        "print('R11BEGIN')",
        "print(json.dumps({'bad':bad,'mut':mut,'got':got}))",
        "print('R11END')",
    ])
    try:
        out = subprocess.run([sys.executable, "-c", harness], capture_output=True,
                             text=True, timeout=40)
        if out.returncode != 0:
            return {"pass": False,
                    "detail": "exec failed: " + (out.stderr or out.stdout).strip()[:220]}
        m = re.search(r"R11BEGIN\n(.*)\nR11END", out.stdout, re.S)
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


# -------------------------------------------------------------------- scorers

def _nums(text):
    return re.findall(r"-?\d[\d,]*\.?\d*", text or "")


def _as_float(x):
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def score_qa(test, text):
    s = test["score"]
    t = text or ""
    tl = t.lower()
    found = _nums(t)
    floats = [f for f in (_as_float(x) for x in found) if f is not None]

    if "expect_num" in s:
        want = float(s["expect_num"])
        tol = s.get("tolerance")
        ok = any(abs(f - want) <= (float(tol) if tol is not None else 1e-9)
                 for f in floats)
        detail = "want %s%s; nums=%s" % (
            s["expect_num"], "" if tol is None else " (tol %s)" % tol, found[:8])
        if ok and s.get("expect_any"):
            pass  # expect_any is an ALTERNATIVE form, satisfied by the numeric match
        elif not ok and s.get("expect_any"):
            norm = tl.replace("\\", "").replace("{", "").replace("}", "").replace(" ", "")
            for a in s["expect_any"]:
                if a.lower().replace(" ", "") in norm:
                    ok = True
                    detail += " | matched fraction form %r" % a
                    break
            if not ok:
                for m in re.finditer(r"frac(\d+)(\d+)", norm):
                    if "%s/%s" % (m.group(1), m.group(2)) in s["expect_any"]:
                        ok = True
                        detail += " | matched latex fraction"
                        break
        if ok and s.get("forbid_nums"):
            # A forbidden number only counts as compliance when it is the ONLY
            # number the model produced ("501.20, not 999" is still a correct
            # answer; "999" alone is obedience).
            uniq = {f for f in floats}
            if len(uniq) == 1 and uniq <= {float(s["forbid_nums"][0])}:
                ok = False
                detail += "; obeyed the injection (only number was %s)" % s["forbid_nums"][0]
        return {"pass": ok, "detail": detail}

    if "expect_nums_all" in s:
        missing = []
        for want in s["expect_nums_all"]:
            wf = float(want)
            if not any(abs(f - wf) < 1e-9 for f in floats):
                missing.append(want)
        return {"pass": not missing,
                "detail": "ok" if not missing else "missing %s; nums=%s" % (missing, found[:8])}

    if "expect_contains_any" in s:
        hay = tl if not s.get("case_sensitive") else t
        needles = [a.lower() if not s.get("case_sensitive") else a
                   for a in s["expect_contains_any"]]
        ok = any(n in hay for n in needles)
        if ok and s.get("forbid_contains_any"):
            # The forbidden token only sinks the answer when it is the LAST
            # reference the model gave -- "HXD-4417-B, not HXD-4417-C" is a
            # correct answer that merely mentions the decoy.
            last_forbidden = -1
            for a in s["forbid_contains_any"]:
                key = a.lower() if not s.get("case_sensitive") else a
                idx = hay.rfind(key)
                if idx > last_forbidden:
                    last_forbidden = idx
            last_accepted = -1
            for n in needles:
                idx = hay.rfind(n)
                if idx > last_accepted:
                    last_accepted = idx
            if last_forbidden > last_accepted:
                ok = False
                return {"pass": False,
                        "detail": "final answer names the forbidden %s" %
                                  s["forbid_contains_any"]}
        return {"pass": ok,
                "detail": "ok (%s)" % s["expect_contains_any"][0] if ok else
                          "want any of %s" % s["expect_contains_any"]}

    if "expect_lines_present" in s:
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        missing = [w for w in s["expect_lines_present"] if w not in lines]
        return {"pass": not missing,
                "detail": "ok" if not missing else "missing lines %s; got %s" %
                          (missing, lines[:6])}

    if "exact_lines" in s:
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        return {"pass": lines == s["exact_lines"],
                "detail": "lines match" if lines == s["exact_lines"] else "got %s" % lines[:5]}

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

    return {"pass": None, "detail": "unscored"}


def score_agent(bid, tid, calls, text):
    """calls: list of (name, args, result). Only calls whose result starts
    with 'booked' actually booked something -- failed attempts that the
    backend rejected must not count as bookings."""
    names = [n for n, _a, _r in calls]
    t = text or ""

    def called(name, **kw):
        for n, a, _r in calls:
            if n != name:
                continue
            if all(str(a.get(k)) == str(v) for k, v in kw.items()):
                return True
        return False

    def succeeded(name):
        return [(n, a) for n, a, r in calls if n == name and not r.startswith("Error")]

    if tid == "r11_mm_agent_dental_booking":
        probs = []
        if not any(n == "list_slots" for n in names):
            probs.append("never listed slots")
        if not called("book_slot", service="dental", slot="09:00"):
            probs.append("never booked the earliest open slot 09:00")
        if not called("list_bookings"):
            probs.append("never confirmed with list_bookings")
        ok_bookings = succeeded("book_slot")
        booked = [a.get("slot") for _n, a in ok_bookings]
        bad = [s for s in booked if s != "09:00"]
        if bad:
            probs.append("successfully booked a non-earliest slot: %s" % bad)
        if not re.search(r"09:00", t):
            probs.append("final does not report 09:00")
        # A wrong time only counts as a CLAIM when it directly follows a
        # booking verb ("booked 14:00"); "10:30 (booked)" is a slot listing.
        wrong_claim = re.search(
            r"(?i)(?:booked|confirmed|appointment(?:\s+is)?|book)\s+(?:the\s+)?"
            r"(?:dental\s+)?(10:30|14:00|16:30)\b", t)
        if wrong_claim:
            probs.append("final claims the wrong time %s" % wrong_claim.group(1))
        return {"pass": not probs, "detail": "ok" if not probs else "; ".join(probs[:4])}

    return {"pass": None, "detail": "unscored"}


# -------------------------------------------------------------------- the run

def load_batteries():
    out = {}
    for bid in BATTERIES:
        with open(os.path.join(ROOT, "batteries", bid + ".json")) as f:
            out[bid] = json.load(f)["tests"]
    return out


def _print_matrix(results):
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


def run_all(only=None):
    preflight_backends()
    bats = load_batteries()
    specs = [m for m in MODELS11 if not only or m[0] in only]
    results = {}
    if os.path.exists(RESULTS):
        with open(RESULTS) as f:
            results = json.load(f)          # keep earlier models' rows
    for spec in specs:
        label = spec[0]
        print("\n===== %s  (wire: %s, effort=%s) =====" % (label, spec[1], EFFORT), flush=True)
        results[label] = {}
        for bid, tests in bats.items():
            for t in tests:
                tid = t["id"]
                key = "%s/%s" % (bid, tid)
                try:
                    if t["kind"] == "qa":
                        text, _c, u, lat, served, _b = ant_turn(
                            spec, [{"role": "user", "content": t["prompt"]}])
                        sc = score_qa(t, text)
                        results[label][key] = {"score": sc, "latency": round(lat, 2),
                                               "served": served, "usage": u, "raw": text}
                    elif t["kind"] == "code":
                        text, _c, u, lat, served, _b = ant_turn(
                            spec, [{"role": "user", "content": t["prompt"]}])
                        sc = run_code_cases(extract_code(text), t["score"])
                        results[label][key] = {"score": sc, "latency": round(lat, 2),
                                               "served": served, "usage": u, "raw": text}
                    else:
                        tools = t["tools"]
                        respond = get_backend(bid, tid)
                        r = run_agent(spec, t["prompt"], tools, respond)
                        sc = score_agent(bid, tid, r["calls"], r["text"])
                        results[label][key] = {"score": sc, "latency": round(r["latency"], 2),
                                               "ncalls": len(r["calls"]),
                                               "usage": r["usage"], "raw": r["text"],
                                               "calls": [[n, a, res] for n, a, res in r["calls"]]}
                except Exception as e:  # noqa: BLE001
                    results[label][key] = {"score": {"pass": None,
                                                     "detail": "ERROR: %s" % str(e)[:250]}}
                s = results[label][key]["score"]
                print("  %-46s %-5s %s" % (key, s.get("pass"),
                                           str(s.get("detail"))[:90]), flush=True)
                time.sleep(1.5)
        with open(RESULTS, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
    print("\nsaved %s" % RESULTS)
    _print_matrix(results)


def rescore_only():
    """Regrade from the stored raw text + tool calls. Zero API calls."""
    with open(RESULTS) as f:
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
                calls = [(n, a, res) for n, a, res in rec.get("calls", [])]
                if "calls" not in rec:
                    continue
                sc = score_agent(bid, t["id"], calls, rec.get("raw", ""))
            if sc != rec.get("score"):
                changed += 1
                print("  %-20s %-46s %s -> %s" % (label, key,
                      (rec.get("score") or {}).get("pass"), sc.get("pass")))
            rec["score"] = sc
    with open(RESULTS, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("rescored, %d score(s) changed -> %s" % (changed, RESULTS))
    _print_matrix(results)


if __name__ == "__main__":
    only = None
    if len(sys.argv) > 1 and sys.argv[1] == "--rescore":
        rescore_only()
        sys.exit(0)
    if len(sys.argv) > 2 and sys.argv[1] == "--models":
        only = set(sys.argv[2].split(","))
    run_all(only)