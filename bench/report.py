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
