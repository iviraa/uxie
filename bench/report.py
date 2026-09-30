"""Regenerate bench/results/summary.md from result files (numbers are never hand-copied).

usage: python -m bench.report
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from bench.cases import load
from bench.score import HEADER, RESULTS, by_op, score, table

SETS = [
    (
        "Seeded bugs",
        Path("bench/cases.jsonl"),
        ["haiku-baseline", "haiku-context", "haiku-specialists"],
        "59 real commits from 6 repos, each with one injected bug plus a clean twin (118 PRs). "
        "Recall = flagged the injected line (within 2 lines); clean-PR columns measure noise.",
    ),
    (
        "Real bugs (SZZ)",
        Path("bench/cases_real.jsonl"),
        ["real-baseline", "real-context", "real-context16", "real-specialists"],
        "36 commits in httpx, click and rich that introduced lines a later bug fix changed "
        "(mined with SZZ). Recall = flagged one of those lines. SZZ is noisy: some 'bugs' "
        "are type-annotation or cosmetic fixes.",
    ),
    (
        "Live pipeline on seeded bugs (sample)",
        Path("bench/cases.jsonl"),
        [
            "haiku-specialists-sonnet-gate-subset",
            "haiku-specialists-sonnet-gate-verify-gated",
            "haiku-specialists-sonnet-gate-verify-gated-lenient",
        ],
        "Specialist reviewers, then findings under 0.9 confidence are held and proven by a "
        "Sonnet 4.6 prover in the sandbox. gated = post held findings only if proven (live "
        "setting); gated-lenient = post unless refuted. 14 sampled cases.",
    ),
    (
        "Live pipeline on real bugs (sample)",
        Path("bench/cases_real.jsonl"),
        [
            "real-specialists-sonnet-gate-subset",
            "real-specialists-sonnet-gate-verify-gated",
            "real-specialists-sonnet-gate-verify-gated-lenient",
        ],
        "Same pipeline on 12 sampled SZZ cases. Most losses are held findings the sandbox "
        "could not execute (old code on Python 3.12, OS-level code paths), not refutations.",
    ),
]


def main() -> None:
    out = [
        f"# uxie benchmark results\n\nGenerated {date.today():%Y-%m-%d} by "
        "`python -m bench.report`. "
        "Reviewer model: Claude Haiku 4.5 on Bedrock.\n"
    ]
    for title, path, variants, blurb in SETS:
        present = [v for v in variants if (RESULTS / f"{v}.jsonl").exists()]
        if not path.exists() or not present:
            continue
        cases = {c.id: c for c in load(path)}
        rows = [score(v, cases).row() for v in present]
        out += [f"## {title}\n", blurb + "\n", table(HEADER, rows) + "\n"]
        ops = sorted({op for v in present for op in by_op(v, cases)})
        if len(ops) > 1:
            per = [
                [v] + [f"{g}/{t}" for g, t in (by_op(v, cases).get(o, (0, 0)) for o in ops)]
                for v in present
            ]
            out += ["Recall by bug type:\n", table(["variant", *ops], per) + "\n"]
    (RESULTS / "summary.md").write_text("\n".join(out))
    print((RESULTS / "summary.md").read_text())


if __name__ == "__main__":
    main()
