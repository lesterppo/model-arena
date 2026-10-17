# model-arena — full report

*Live runs: 2026-10-17. Models: `deepseek-flash` (DeepSeek), `gpt-6-luna`
(OpenAI), `claude-haiku-5-5` (Anthropic), Muse Spark 1.3 (Meta, answer-bank
replay — non-blind, see caveats).*

## What was tested

**Rounds 1–3 (capability, 18 tests):** JSON adherence, tool calling, math,
code generation (8-case suites), 15k-token needle retrieval, format
following, harder number theory / logic / multi-constraint generation /
nested JSON / parallel tool loops / 15k-token multi-hop reasoning /
fabricated-event honesty / alphabet sentences. **All four models: 18/18.**
Correctness alone did not separate them.

**Rounds 4–5 (agentic, 8 scenarios):** flight-booking chains, divide-by-zero
recovery, prompt injection in tool results, deprecated-tool discrimination,
read→fix→write→test bug loops, clarification vs hallucination, destructive
email injection, sponsored-option distraction. All three API models: 8/8 on
actions. Separation came from *behavior*: DeepSeek proactively flagged
injections/phishing twice; Haiku warned once; Luna acted correctly but
stayed silent.

**Rounds 7–8 (deeper, 10 tests):** combinatorics casework, self-reference
logic, median-of-two-sorted-arrays, rhyme+word-count poetry, 30k-token
2-hop reasoning, sycophancy probes, 7-turn warehouse exploration,
sellout-replan, email→SEND trap, cheapest+nonstop conjunction.
Totals: Luna 36/36, Muse Spark 36/36\*, DeepSeek 35/36, Haiku 35/36.
(\*non-blind.) Notable nuance: DeepSeek/Haiku "failed" the replan test by
*asking before spending more money* — arguably the safer behavior.

**Round 9 (the main event, 25 tests):** each model independently designed a
6–7 task battery from a shared brief (2 hard reasoning/coding, 2 agentic
tool-use, 1 multi-constraint instruction-following, 1 robustness), then all
four took all four batteries — 100 runs, scripted deterministic backends,
per-designer scorers.

## Round 9 scoreboard

| taker \ designer | deepseek | luna | haiku | muse | total |
|---|---|---|---|---|---|
| deepseek-flash | 6/6 | 4/6 | 7/7 | 5/6 | 22/25 |
| gpt-6-luna | 5/6 | 2/6 | 6/7 | 4/6 | 17/25 |
| claude-haiku-5-5 | 6/6 | 5/6 | 5/7 | 5/6 | 21/25 |
| muse-spark | 6/6 | 6/6 | 7/7 | 6/6 | 25/25 \* |

## Findings

1. **The hardest test designer scored the lowest.** Luna's battery was the
   toughest for everyone (even Luna: 2/6). Luna failed its own
   number-theory question, answering 2097 for a CRT system whose answer is
   1732 — and its own route-planning agent test.
2. **Designers make mistakes; verification caught two.** Luna's CRT key
   (1229) fails its own congruence (1229 mod 5 = 4, not 2); brute force
   gives 1732. Luna's interval-merge key ([[1,5]]) is wrong ([1,2]∪[2,4] =
   [1,4], disjoint from [5,5] → [[1,4],[5,5]]). Both were corrected before
   scoring; without this, correct models would have been marked wrong.
3. **The currency trap** (my battery: identical flights priced $640 / €590 /
   £520, "book the cheapest"): Luna booked £520 on raw numeric comparison;
   Haiku booked $640, then computed €590 ≈ $637 in its *prose* — after the
   irreversible action; DeepSeek refused to book at all without live FX
   rates (safe, but task-incomplete). Only Muse Spark did the approximate
   conversion first and booked correctly.
4. **Haiku failed two of its own seven tests** — its config-conflict final
   falsely implied a locked change went through, and its product blurb
   contained a forbidden letter z.
5. **Strict grading stung everyone on Luna's summary test** (≤12 words,
   both labels, must mention "18 volunteers" and non-randomization):
   all three API models failed; all three also missed the "equal priors"
   keyword on the Bayes question despite correct 90% answers.
6. **Dimensional ranking** (across all 9 rounds): DeepSeek Flash — best
   safety vigilance, wobbly latency; GPT-6 Luna — best reasoning
   transparency and autonomy, silent on threats, slowest; Claude Haiku
   5.5 — fastest (~2.8s/test), efficient, safe; Muse Spark — measured
   top-tier on correctness and vigilance (non-blind caveat applies).

## Caveats

- Single-sample scoring per test; temperature sampling means close calls
  can flip on re-run.
- Muse Spark has no public API here; it ran from an answer bank authored by
  the same person who wrote the harness — non-blind by construction.
  Treat 25/25 as a reference, not a blind victory.
- Two designer keys and three scorer bugs were fixed during the run; all
  fixes are documented in the harness comments.
- Latency/price vary by provider and time; correctness results are from
  2026-10-17 snapshots of each model.
