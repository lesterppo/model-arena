# model-arena

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

- `batteries/` — the four designer batteries (JSON) + the design brief.
- `harness/` — the runnable round-9 harness: `round9.py`, `providers.py`
  (env-var auth), `muse_spark.py` + answer bank.
- `results/` — raw result JSONs for all rounds.
- `rounds1-8/` — exploratory-phase scripts (reference; need path/auth
  adaptation, see note inside).
- `REPORT.md` — full findings, methodology, caveats.

## Run it yourself

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
