# Threads post draft — model-arena findings (3-post thread)

## Post 1
I made 4 AI models — DeepSeek Flash, GPT-6 Luna, Claude Haiku 5.5, and Muse Spark — each design their own 6-question test battery. Then I made them all take each other's tests: 25 questions, 100 live runs. The results surprised me. 🧵

## Post 2 (reply to 1)
Scores: Muse Spark 25/25* (non-blind — I wrote the harness, grain of salt), DeepSeek Flash 22/25, Claude Haiku 5.5 21/25, GPT-6 Luna 17/25. The twist: Luna designed the HARDEST test set… and scored lowest on it — 2/6, failing its own math question.

## Post 3 (reply to 2)
Favorite moment: my currency-trap test priced flights at $640 / €590 / £520 and said "book the cheapest." Luna booked £520 (520 < 640 🤦). Haiku booked $640, then realized €590 was cheaper — in prose, after booking. Only Muse did the FX math first. Full harness + results: https://github.com/lesterppo/model-arena
