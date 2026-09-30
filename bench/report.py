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
    (
        "Go: seeded bugs",
        Path("bench/cases_go.jsonl"),
        ["go-baseline", "go-specialists"],
        "20 commits from samber/lo and go-chi/chi with one injected logic or behavioral bug "
        "each, plus clean twins (40 PRs). Mutations made with tree-sitter.",
    ),
    (
        "Go: real bugs (SZZ)",
        Path("bench/cases_real_go.jsonl"),
        ["real-go-baseline", "real-go-specialists"],
        "24 bug-introducing commits in samber/lo and go-chi/chi.",
    ),
    (
        "TypeScript: seeded bugs",
        Path("bench/cases_ts.jsonl"),
        ["ts-baseline", "ts-specialists"],
        "20 commits from remeda and date-fns with one injected bug each, plus clean twins.",
    ),
    (
        "TypeScript: real bugs (SZZ)",
        Path("bench/cases_real_ts.jsonl"),
        ["real-ts-baseline", "real-ts-specialists"],
        "24 bug-introducing commits in remeda and date-fns.",
    ),
    (
        "Python: behavioral bugs",
        Path("bench/cases_behavior_py.jsonl"),
        ["pybehav-spec3", "pybehav-spec4"],
        "30 commits with one behavioral bug each (dropped state update, swapped statements, "
        "removed None guard, swapped arguments), plus clean twins. spec3 = logic, edge-case, "
        "and contract reviewers; spec4 adds the behavior reviewer.",
    ),
]

BEHAVIOR_OPS = {"drop-update", "swap-stmts", "swallow-error", "drop-guard", "swap-args"}


def by_category(variant: str, cases: dict) -> dict[str, tuple[int, int]]:
    """Recall grouped as logic vs behavioral mutations."""
    out: dict[str, tuple[int, int]] = {}
    for op, (got, tot) in by_op(variant, cases).items():
        if op == "real":
            continue
        cat = "behavioral" if op in BEHAVIOR_OPS else "logic"
        g, t = out.get(cat, (0, 0))
        out[cat] = (g + got, t + tot)
    return out


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
        cats = sorted({c for v in present for c in by_category(v, cases)})
        if len(cats) > 1:
            rows = [
                [v] + [f"{g}/{t}" for g, t in (by_category(v, cases).get(c, (0, 0)) for c in cats)]
                for v in present
            ]
            out += ["Recall by category:\n", table(["variant", *cats], rows) + "\n"]
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
