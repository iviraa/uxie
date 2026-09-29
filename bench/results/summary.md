| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| haiku-baseline | 85% (50/59) | 43% | 1.80 | 68% | $0.0073 | 13s / 21s | 0.0 | 0 |
| haiku-context | 90% (53/59) | 60% | 1.42 | 75% | $0.0417 | 168s / 185s | 8.0 | 0 |

Recall by mutation type:

| variant | boolop | compare | drop-not | flip-return | off-by-one |
|---|---|---|---|---|---|
| haiku-baseline | 9/9 | 26/29 | 8/9 | 1/2 | 6/10 |
| haiku-context | 8/9 | 27/29 | 9/9 | 1/2 | 8/10 |
