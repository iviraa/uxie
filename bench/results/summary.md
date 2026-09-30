# uxie benchmark results

Generated 2026-09-30 by `python -m bench.report`. Reviewer model: Claude Haiku 4.5 on Bedrock.

## Seeded bugs

59 real commits from 6 repos, each with one injected bug plus a clean twin (118 PRs). Recall = flagged the injected line (within 2 lines); clean-PR columns measure noise.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| haiku-baseline | 85% (50/59) | 43% | 1.80 | 68% | $0.0073 | 13s / 21s | 0.0 | 0 |
| haiku-context | 90% (53/59) | 60% | 1.42 | 75% | $0.0417 | 168s / 185s | 8.0 | 0 |
| haiku-specialists | 93% (55/59) | 27% | 4.27 | 92% | $0.0235 | 40s / 45s | 0.0 | 0 |

Recall by bug type:

| variant | boolop | compare | drop-not | flip-return | off-by-one |
|---|---|---|---|---|---|
| haiku-baseline | 9/9 | 26/29 | 8/9 | 1/2 | 6/10 |
| haiku-context | 8/9 | 27/29 | 9/9 | 1/2 | 8/10 |
| haiku-specialists | 9/9 | 27/29 | 9/9 | 1/2 | 9/10 |

## Real bugs (SZZ)

36 commits in httpx, click and rich that introduced lines a later bug fix changed (mined with SZZ). Recall = flagged one of those lines. SZZ is noisy: some 'bugs' are type-annotation or cosmetic fixes.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| real-baseline | 44% (16/36) | 23% | - | - | $0.0085 | 18s / 28s | 0.0 | 0 |
| real-context | 25% (9/36) | 20% | - | - | $0.0474 | 176s / 183s | 9.6 | 0 |
| real-context16 | 25% (9/36) | 24% | - | - | $0.0703 | 232s / 342s | 13.5 | 0 |
| real-specialists | 67% (24/36) | 24% | - | - | $0.0266 | 39s / 48s | 0.0 | 0 |

## Live pipeline on seeded bugs (sample)

Specialist reviewers, then findings under 0.9 confidence are held and proven by a Sonnet 4.6 prover in the sandbox. gated = post held findings only if proven (live setting); gated-lenient = post unless refuted. 14 sampled cases.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| haiku-specialists-sonnet-gate-subset | 100% (7/7) | 31% | 4.29 | 100% | $0.0304 | 40s / 46s | 0.0 | 0 |
| haiku-specialists-sonnet-gate-verify-gated | 100% (7/7) | 31% | 1.86 | 86% | $0.0623 | 318s / 659s | 0.0 | 0 |
| haiku-specialists-sonnet-gate-verify-gated-lenient | 100% (7/7) | 34% | 3.43 | 100% | $0.0623 | 318s / 659s | 0.0 | 0 |

Recall by bug type:

| variant | compare | drop-not | off-by-one |
|---|---|---|---|
| haiku-specialists-sonnet-gate-subset | 4/4 | 1/1 | 2/2 |
| haiku-specialists-sonnet-gate-verify-gated | 4/4 | 1/1 | 2/2 |
| haiku-specialists-sonnet-gate-verify-gated-lenient | 4/4 | 1/1 | 2/2 |

## Live pipeline on real bugs (sample)

Same pipeline on 12 sampled SZZ cases. Most losses are held findings the sandbox could not execute (old code on Python 3.12, OS-level code paths), not refutations.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| real-specialists-sonnet-gate-subset | 67% (8/12) | 15% | - | - | $0.0325 | 40s / 46s | 0.0 | 0 |
| real-specialists-sonnet-gate-verify-gated | 42% (5/12) | 24% | - | - | $0.2470 | 372s / 866s | 0.0 | 0 |
| real-specialists-sonnet-gate-verify-gated-lenient | 50% (6/12) | 18% | - | - | $0.2470 | 372s / 866s | 0.0 | 0 |

## Go: seeded bugs

20 commits from samber/lo and go-chi/chi with one injected logic or behavioral bug each, plus clean twins (40 PRs). Mutations made with tree-sitter.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| go-baseline | 80% (16/20) | 64% | 0.55 | 30% | $0.0046 | 13s / 16s | 0.0 | 0 |
| go-specialists | 85% (17/20) | 52% | 1.50 | 55% | $0.0194 | 53s / 57s | 0.0 | 0 |

Recall by category:

| variant | behavioral | logic |
|---|---|---|
| go-baseline | 9/13 | 7/7 |
| go-specialists | 10/13 | 7/7 |

Recall by bug type:

| variant | boolop | compare | drop-guard | drop-update | off-by-one | swap-args | swap-stmts |
|---|---|---|---|---|---|---|---|
| go-baseline | 1/1 | 3/3 | 1/1 | 3/4 | 3/3 | 4/5 | 1/3 |
| go-specialists | 1/1 | 3/3 | 1/1 | 4/4 | 3/3 | 4/5 | 1/3 |

## Go: real bugs (SZZ)

24 bug-introducing commits in samber/lo and go-chi/chi.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| real-go-baseline | 21% (5/24) | 38% | - | - | $0.0057 | 13s / 16s | 0.0 | 0 |
| real-go-specialists | 62% (15/24) | 43% | - | - | $0.0252 | 53s / 60s | 0.0 | 0 |

## TypeScript: seeded bugs

20 commits from remeda and date-fns with one injected bug each, plus clean twins.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| ts-baseline | 60% (12/20) | 38% | 1.40 | 60% | $0.0149 | 13s / 20s | 0.0 | 0 |
| ts-specialists | 75% (15/20) | 29% | 2.35 | 65% | $0.0596 | 53s / 60s | 0.0 | 0 |

Recall by category:

| variant | behavioral | logic |
|---|---|---|
| ts-baseline | 2/10 | 10/10 |
| ts-specialists | 5/10 | 10/10 |

Recall by bug type:

| variant | boolop | compare | drop-not | drop-update | off-by-one | swap-args | swap-stmts |
|---|---|---|---|---|---|---|---|
| ts-baseline | 1/1 | 4/4 | 2/2 | 1/1 | 3/3 | 1/4 | 0/5 |
| ts-specialists | 1/1 | 4/4 | 2/2 | 1/1 | 3/3 | 2/4 | 2/5 |

## TypeScript: real bugs (SZZ)

24 bug-introducing commits in remeda and date-fns.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| real-ts-baseline | 4% (1/24) | 5% | - | - | $0.0081 | 13s / 18s | 0.0 | 0 |
| real-ts-specialists | 17% (4/24) | 10% | - | - | $0.0331 | 53s / 58s | 0.0 | 0 |

## Python: behavioral bugs

30 commits with one behavioral bug each (dropped state update, swapped statements, removed None guard, swapped arguments), plus clean twins. spec3 = logic, edge-case, and contract reviewers; spec4 adds the behavior reviewer.

| variant | bug recall | on-target precision | comments / clean PR | clean PRs flagged | cost / PR | latency p50 / p90 | tool calls / PR | errors |
|---|---|---|---|---|---|---|---|---|
| pybehav-spec3 | 80% (24/30) | 21% | 5.03 | 80% | $0.0310 | 40s / 45s | 0.0 | 0 |
| pybehav-spec4 | 80% (24/30) | 20% | 5.53 | 80% | $0.0413 | 53s / 59s | 0.0 | 0 |

Recall by bug type:

| variant | drop-guard | drop-update | swap-args | swap-stmts |
|---|---|---|---|---|
| pybehav-spec3 | 6/8 | 8/8 | 9/10 | 1/4 |
| pybehav-spec4 | 6/8 | 8/8 | 9/10 | 1/4 |
