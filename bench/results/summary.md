# uxie benchmark results

Generated 2026-09-29 by `python -m bench.report`. Reviewer model: Claude Haiku 4.5 on Bedrock.

## Seeded bugs

59 real commits from 6 repos, each with one injected bug plus a clean twin (118 PRs). Recall = flagged the injected line (within 2 lines); clean-PR columns measure noise.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| haiku-baseline | 85% (50/59) | 43% | 1.80 | 68% | $0.0073 | 13s / 21s | 0.0 | 0 |
| haiku-context | 90% (53/59) | 60% | 1.42 | 75% | $0.0417 | 168s / 185s | 8.0 | 0 |
| haiku-specialists | 100% (1/1) | 50% | 1.00 | 100% | $0.0177 | 19s / 19s | 0.0 | 0 |

Recall by bug type:

| variant | boolop | compare | drop-not | flip-return | off-by-one |
|---|---|---|---|---|---|
| haiku-baseline | 9/9 | 26/29 | 8/9 | 1/2 | 6/10 |
| haiku-context | 8/9 | 27/29 | 9/9 | 1/2 | 8/10 |
| haiku-specialists | 0/0 | 0/0 | 1/1 | 0/0 | 0/0 |

## Real bugs (SZZ)

36 commits in httpx, click and rich that introduced lines a later bug fix changed (mined with SZZ). Recall = flagged one of those lines. SZZ is noisy: some 'bugs' are type-annotation or cosmetic fixes.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| real-baseline | 44% (16/36) | 23% | - | - | $0.0085 | 18s / 28s | 0.0 | 0 |
| real-context | 25% (9/36) | 20% | - | - | $0.0474 | 176s / 183s | 9.6 | 0 |
| real-context16 | 25% (9/36) | 24% | - | - | $0.0703 | 232s / 342s | 13.5 | 0 |
