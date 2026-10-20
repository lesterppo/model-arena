# model-arena

Head-to-head scored arenas for frontier LLMs. Two completed rounds:

- **Round 10** (2026-10-21) — GPT-6 Luna, Claude Haiku 5.5, DeepSeek V4.1 Flash,
  MiniMax V3.1 Flash on four *fresh* batteries: **a clean four-way tie at
  24/24**, separated only by tokens and latency. Full write-up:
  [REPORT-R10.md](REPORT-R10.md). Run it with `harness/round10.py`.
- **Round 9** (2026-10-17) — DeepSeek Flash, GPT-6 Luna, Claude Haiku 5.5 and
  Muse Spark, four designer batteries, 25 tests. Detailed below.

## Round 10 summary

| taker \ designer | deepseek | luna | haiku | minimax | TOTAL |
|---|---|---|---|---|---|
| gpt-6-luna | 6/6 | 6/6 | 6/6 | 6/6 | **24/24** |
| claude-haiku-5-5 | 6/6 | 6/6 | 6/6 | 6/6 | **24/24** |
| deepseek-flash | 6/6 | 6/6 | 6/6 | 6/6 | **24/24** |
| minimax-m3.1-flash | 6/6 | 6/6 | 6/6 | 6/6 | **24/24** |

| model | median latency | input tok | output tok | tool calls |
|---|---|---|---|---|
| claude-haiku-5-5 | 2.93 s | 12 208 | 10 301 | 14 |
| deepseek-flash | 2.67 s | 12 458 | 12 754 | 17 |
| gpt-6-luna | 3.37 s | **4 337** | **4 187** | 14 |
| minimax-m3.1-flash | 3.21 s | 18 316 | 12 793 | 19 |

Luna matched the field on a third of the tokens. MiniMax matched it 2.4x slower
(3x on code). Corrections made while running: three self-written answer keys were
wrong and machine-verified replacements were substituted before scoring; the
syllogism item turned out to have two valid answers; and `gpt-6-luna` needed the
`/v1/responses` API for function tools (chat-completions rejects them).

---

# Round 9

Four frontier "flash-class" models — **DeepSeek Flash** (`deepseek-flash`),
**GPT-6 Luna** (`gpt-6-luna`), **Claude Haiku 5.5** (`claude-haiku-5-5`) and
**Muse Spark** — compared across 9 rounds of capability and agentic tests,
run live on 2026-10-17.

The centerpiece is **round 9**: each model independently designed its own
6–7 task test battery, then all four models took all four batteries
(25 tests, 100 runs).

## Results (round 9)

| taker \ battery designer | deepseek | luna | haiku | muse | total |
|---|---|---|---|---|---|
| deepseek-flash | 6/6 | 4/6 | 7/7 | 5/6 | **22/25** |
| gpt-6-luna | 5/6 | 2/6 | 6/7 | 4/6 | **17/25** |
| claude-haiku-5-5 | 6/6 | 5/6 | 5/7 | 5/6 | **21/25** |
| muse-spark | 6/6 | 6/6 | 7/7 | 6/6 | **25/25** * |

\* Muse Spark is the author's own model, tested non-blind via an answer
bank (same prompts, same scorers) — treat as a reference upper bound, not a
blind result. Blind-only order: **deepseek-flash > claude-haiku-5-5 > gpt-6-luna**.

Headline findings (see [REPORT.md](REPORT.md) for the full write-up):

- **Luna designed the hardest battery and scored lowest on it** — 2/6,
  failing its own number-theory question (it answered 2097; the answer is
  1732). Designing hard tests ≠ passing them.
- **Two designer answer keys were wrong**, caught by brute-force
  verification before scoring (details in REPORT.md).
- **The currency trap** (flights priced in $, €, £): Luna booked £520 on raw
  numbers, Haiku booked $640 then self-corrected in prose *after* booking,
  DeepSeek refused to book without live FX rates. Only Muse Spark did the
  approximate conversion (€590 ≈ $637) and booked correctly.
- Earlier rounds (1–8, 36 more tests) were a near-tie on correctness; the
  models separated on **safety vigilance** (DeepSeek most proactive),
  **speed** (Haiku fastest, ~2.8s/test), and **autonomy vs caution**.

## Layout

- `batteries/r10_*.json` — round-10 batteries (6 tests each, one per designer style).
- `batteries/{deepseek,luna,haiku,muse}.json` — the round-9 designer batteries + the design brief.
- `harness/round10.py` — round-10 harness (4 providers, 3 endpoints, raw output
  persisted, `--rescore` regrades offline), `harness/round9.py` — round-9
  harness, `harness/providers.py` (env-var auth), `harness/muse_spark.py` +
  answer bank.
- `results/` — raw result JSONs for all rounds.
- `rounds1-8/` — exploratory-phase scripts (reference; need path/auth
  adaptation, see note inside).
- `REPORT-R10.md` — round-10 findings, methodology, caveats. `REPORT.md` — round 9.

## Run it yourself

```bash
export OPENAI_API_KEY=... ANTHROPIC_API_KEY=... DEEPSEEK_API_KEY=... MINIMAX_API_KEY=...
cd harness
python3 round10.py            # round 10, 96 calls, ~11 min
python3 round10.py --rescore  # regrade round 10 from stored raw text, no network
```

Round 9 (no MiniMax):

```bash
export DEEPSEEK_API_KEY=... OPENAI_API_KEY=... ANTHROPIC_API_KEY=...
cd harness
python3 round9.py            # full 4x4 run (~100 model calls)
```

Muse Spark runs from its answer bank with no key. To answer as Muse Spark
yourself: `python3 round9.py --export-muse`, fill the prompts, then
`python3 round9.py --ingest-muse answers.json`.

## License

MIT
