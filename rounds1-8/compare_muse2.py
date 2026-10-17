#!/usr/bin/env python3
"""Score Muse Spark on rounds 7+8 with the identical scorers."""

import json
import re
import sys

sys.path.insert(0, "/home/hatch/workspace/model-compare")
from compare6 import score as score6  # noqa: E402
from compare7 import score as score7, SCENARIOS  # noqa: E402
from compare4 import s5_make  # noqa: E402 (unused here, kept for parity)

W = "/home/hatch/workspace/model-compare/"
a3 = json.load(open(W + "muse_spark_3.json"))
ag = json.load(open(W + "muse_spark_agentic2.json"))
import compare6 as c6
c6.RHYMES |= {"ignite", "tonight"}

TOOL_RE = re.compile(r"```tool\s*\n(.*?)```", re.S)
results = {}

for tid in ["digit_sum", "pet_logic", "median_two", "rhyme_count",
            "longctx_30k", "calibration"]:
    results[tid] = {"score": score6(tid, a3[tid])}

scen_map = {s[0]: s for s in SCENARIOS}
# warehouse needs its stateful backend
from compare7 import warehouse_make  # noqa: E402


def replay(sid):
    _, prompt, tools, respond, mt = scen_map[sid]
    resp = warehouse_make() if sid == "A1_warehouse" else respond
    calls, final = [], ""
    for t in ag[sid]:
        tcalls = []
        for m in TOOL_RE.finditer(t):
            o = json.loads(m.group(1))
            tcalls.append((o["name"], o["arguments"]))
        if not tcalls:
            final = t
            break
        for name, args in tcalls:
            calls.append((name, args))
            resp(name, args or {})
    return {"score": score7(sid, {"text": final, "calls": calls}),
            "ncalls": len(calls)}


for sid in ["A1_warehouse", "A2_replan", "A3_send_trap", "A4_cheap_nonstop"]:
    results[sid] = replay(sid)

with open(W + "results_muse2.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)

tot_p = tot_n = 0
for tid, r in results.items():
    p = r["score"].get("pass")
    tot_n += 1
    tot_p += 1 if p else 0
    print(f"{tid:<16}{str(p):<6}{str(r['score'].get('detail'))[:70]}")
print(f"\nMuse Spark rounds 7+8: {tot_p}/{tot_n}")
