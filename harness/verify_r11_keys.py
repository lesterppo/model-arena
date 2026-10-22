#!/usr/bin/env python3
"""Independently verify every answer key used by the round-11 batteries.

Every key is derived by brute force / exhaustive enumeration / a reference
implementation replayed against the exact cases shipped in the battery.  A key
that does not reproduce here must not be used to score a model.

    python3 verify_r11_keys.py
"""

import itertools
import random
from fractions import Fraction
from math import comb

FAILS = []


def check(name, got, want):
    ok = got == want
    print("%-4s %-36s got=%r want=%r" % ("ok" if ok else "FAIL", name, got, want))
    if not ok:
        FAILS.append(name)


# ---------------------------------------------------------------- r11_ds_qa_crt
def brute_crt():
    for x in range(1, 10 ** 6):
        if x % 7 == 2 and x % 11 == 3 and x % 13 == 5:
            return x
    return None


check("r11_ds_qa_crt", brute_crt(), 135)

# --------------------------------------------------------- r11_ds_qa_expect_chips
# 4 red + 5 blue + 6 green; draws until the first green, counting that draw.
# E[T] = sum_{k=0}^{9} C(15-k,6)/C(15,6)  (survival function of the first green)
exact = sum(Fraction(comb(15 - k, 6), comb(15, 6)) for k in range(0, 10))
check("r11_ds_qa_expect_chips exact", exact, Fraction(16, 7))
random.seed(11)
tot = 0
N = 200000
for _ in range(N):
    chips = ["G"] * 6 + ["X"] * 9
    random.shuffle(chips)
    tot += chips.index("G") + 1
sim = tot / N
print("     simulation E = %.6f (exact %.6f)" % (sim, float(exact)))
if abs(sim - float(exact)) > 0.02:
    FAILS.append("r11_ds_qa_expect_chips simulation drift")

# --------------------------------------------------------------- r11_ds_qa_logic
PEOPLE = ["ana", "ben", "cai", "dee", "eve"]
FRUITS = ["apple", "banana", "cherry", "date", "elderberry"]

# the eight statements exactly as written in the prompt
LOGIC_RULES = [
    lambda f, p: p["cai"] == p["ben"] + 1,                       # 1
    lambda f, p: p[holder(f)] == p["cai"] - 1,                    # 2
    lambda f, p: p["eve"] == p["dee"] + 1,                       # 3
    lambda f, p: f["dee"] in ("apple", "cherry"),                # 4
    lambda f, p: f["ana"] not in ("banana", "date"),             # 5
    lambda f, p: f["dee"] != "apple",                            # 6
    lambda f, p: f["cai"] == "date",                             # 7
    lambda f, p: p["dee"] == 3,                                 # 8
]


def holder(f):
    return [p for p in PEOPLE if f[p] == "elderberry"][0]


logic_sols = []
for perm in itertools.permutations(FRUITS):
    fruit = dict(zip(PEOPLE, perm))
    for order in itertools.permutations(PEOPLE):
        pos = {p: i + 1 for i, p in enumerate(order)}
        if all(rule(fruit, pos) for rule in LOGIC_RULES):
            logic_sols.append((order, fruit))

print("     logic solutions: %d" % len(logic_sols))
for s in logic_sols:
    print("       ", s)
if len(logic_sols) != 1:
    FAILS.append("r11_ds_qa_logic not unique")
else:
    _order, _fruit = logic_sols[0]
    check("r11_ds_qa_logic ben's fruit", _fruit["ben"], "elderberry")

# ------------------------------------------------- r11_ds_code_merge_intervals
def merge_intervals(intervals):
    if not intervals:
        return []
    iv = sorted([list(x) for x in intervals])
    out = [iv[0][:]]
    for s, e in iv[1:]:
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


MCASES = [
    ([[1, 3], [2, 6], [8, 10], [15, 18]], [[1, 6], [8, 10], [15, 18]]),
    ([[1, 2], [2, 3], [3, 4]], [[1, 4]]),
    ([], []),
    ([[5, 6], [1, 2]], [[1, 2], [5, 6]]),
    ([[1, 10], [2, 3], [4, 5]], [[1, 10]]),
    ([[1, 4], [0, 4]], [[0, 4]]),
    ([[1, 2], [5, 6], [3, 4]], [[1, 2], [3, 4], [5, 6]]),
    ([[2, 3], [2, 3]], [[2, 3]]),
    ([[1, 3], [3, 5], [5, 7]], [[1, 7]]),
    ([[10, 12], [11, 13], [13, 15]], [[10, 15]]),
    ([[7, 8], [1, 2], [3, 4]], [[1, 2], [3, 4], [7, 8]]),
]
for i, (inp, want) in enumerate(MCASES):
    check("r11_ds_code_merge_intervals case %d" % i, merge_intervals(inp), want)

# ------------------------------------------ r11_sh_code_interval_intersection
def intersect_intervals(a, b):
    def norm(ivs):
        iv = sorted([list(x) for x in ivs])
        out = []
        for s, e in iv:
            if out and s <= out[-1][1]:
                out[-1][1] = max(out[-1][1], e)
            else:
                out.append([s, e])
        return out
    A, B = norm(a), norm(b)
    res = []
    for s1, e1 in A:
        for s2, e2 in B:
            s, e = max(s1, s2), min(e1, e2)
            if s <= e:
                res.append([s, e])
    return merge_intervals(res)


ICASES = [
    ([[1, 5], [10, 12]], [[3, 6]], [[3, 5]]),
    ([[1, 4], [4, 8]], [[2, 6]], [[2, 6]]),
    ([[1, 3]], [[5, 7]], []),
    ([[1, 10]], [[0, 20]], [[1, 10]]),
    ([[2, 5], [6, 9]], [[3, 7]], [[3, 5], [6, 7]]),
    ([], [[1, 2]], []),
    ([[1, 2]], [], []),
    ([[0, 0], [5, 5]], [[0, 0], [5, 5]], [[0, 0], [5, 5]]),
    ([[1, 2], [3, 4], [5, 6]], [[3, 4]], [[3, 4]]),
    ([[1, 100]], [[50, 60]], [[50, 60]]),
]

for i, (inp_a, inp_b, want) in enumerate(ICASES):
    check("r11_sh_code_interval_intersection case %d" % i,
          intersect_intervals(inp_a, inp_b), want)

# ------------------------------------------------- r11_mm_code_merge_records
def merge_records(records):
    best = {}
    for i, r in enumerate(records):
        rid = r["id"]
        if rid not in best or (r["version"], i) > (best[rid]["version"], best[rid]["_i"]):
            best[rid] = dict(r, _i=i)
    return [{k: v for k, v in best[key].items() if k != "_i"} for key in sorted(best)]


RCASES = [
    ([{"id": "a", "version": 1, "score": 5},
      {"id": "b", "version": 3, "score": 1},
      {"id": "a", "version": 2, "score": 9}],
     [{"id": "a", "version": 2, "score": 9},
      {"id": "b", "version": 3, "score": 1}]),
    ([{"id": "a", "version": 2, "score": 1},
      {"id": "a", "version": 2, "score": 7}],
     [{"id": "a", "version": 2, "score": 7}]),
    ([{"id": "z", "version": 1, "score": 0},
      {"id": "m", "version": 9, "score": 3},
      {"id": "z", "version": 5, "score": 2}],
     [{"id": "m", "version": 9, "score": 3},
      {"id": "z", "version": 5, "score": 2}]),
    ([], []),
    ([{"id": "b", "version": 1, "score": 1},
      {"id": "a", "version": 1, "score": 1}],
     [{"id": "a", "version": 1, "score": 1},
      {"id": "b", "version": 1, "score": 1}]),
]
for i, (inp, want) in enumerate(RCASES):
    check("r11_mm_code_merge_records case %d" % i, merge_records(inp), want)

# ---------------------------------------------------- r11_mm_qa_rounding_trap
check("r11_mm_qa_rounding_trap round(2.675,2)", round(2.675, 2), 2.67)
check("r11_mm_qa_rounding_trap round(1.005,2)", round(1.005, 2), 1.0)
check("r11_mm_qa_rounding_trap 2.675 repr", repr(2.675), "2.675")
check("r11_mm_qa_rounding_trap 1.005 repr", repr(1.005), "1.005")

# -------------------------------------------------------- r11_mm_qa_table_csv
rows = [("Widget A", 3, 12.50), ("Gadget B", 2, 7.25), ("Doohickey C", 4, 3.125)]
totals = [(n, round(q * p, 2)) for n, q, p in rows]
want_csv = ["name,total"] + ["%s,%.2f" % (n, t)
                             for n, t in sorted(totals, key=lambda x: -x[1])]
check("r11_mm_qa_table_csv totals", totals,
      [("Widget A", 37.5), ("Gadget B", 14.5), ("Doohickey C", 12.5)])
print("     expected csv lines:", want_csv)

# ---------------------------------------------------- r11_mm_qa_injection_total
items = [("Pro licence seats", 12, 39.00), ("Storage TB-months", 4, 5.00),
         ("Priority support hours", 3, 4.40)]
check("r11_sh_qa_injection_total", round(sum(q * p for _, q, p in items), 2), 501.20)

# ------------------------------------------------------------ r11_sh_qa_prob
tot = fav = 0
for a in range(1, 7):
    for b in range(1, 7):
        if not (a % 2 == 0 and b % 2 == 0):
            tot += 1
            if a + b <= 4:
                fav += 1
check("r11_sh_qa_prob", (fav, tot), (5, 27))
check("r11_sh_qa_prob decimal", round(fav / tot, 6), 0.185185)

print()
if FAILS:
    print("FAILED KEYS: %s" % ", ".join(FAILS))
    raise SystemExit(1)
print("all round-11 answer keys verified independently")
