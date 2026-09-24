"""Score result files against ground truth and print the ablation table.

usage: python -m bench.score --cases bench/cases.jsonl haiku-baseline haiku-context
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.cases import Case, load

RESULTS = Path(__file__).parent / "results"
LINE_TOLERANCE = 2


@dataclass
class Score:
    variant: str
    seeded: int
    clean: int
    caught: int
    findings_seeded: int
    on_target: int
    findings_clean: int
    clean_flagged: int
    errors: int
    cost: float
    latencies: list[float]
    tool_calls: int

    @property
    def recall(self) -> float:
        return self.caught / self.seeded if self.seeded else 0.0

    @property
    def precision(self) -> float:
        return self.on_target / self.findings_seeded if self.findings_seeded else 0.0

    def row(self) -> list[str]:
        n = self.seeded + self.clean
        lat = sorted(self.latencies)
        p50 = statistics.median(lat) if lat else 0.0
        p90 = lat[int(0.9 * (len(lat) - 1))] if lat else 0.0
        return [
            self.variant,
            f"{self.recall:.0%} ({self.caught}/{self.seeded})",
            f"{self.precision:.0%}",
            f"{self.findings_clean / self.clean:.2f}" if self.clean else "-",
            f"{self.clean_flagged / self.clean:.0%}" if self.clean else "-",
            f"${self.cost / n:.4f}" if n else "-",
            f"{p50:.0f}s / {p90:.0f}s",
            f"{self.tool_calls / n:.1f}" if n else "-",
            str(self.errors),
        ]


HEADER = [
    "variant",
    "bug recall",
    "on-target precision",
    "comments / clean PR",
    "clean PRs flagged",
    "cost / PR",
    "latency p50 / p90",
    "tool calls / PR",
    "errors",
]


def hits(case: Case, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    m = case.mutation
    if m is None:
        return []
    return [
        f
        for f in findings
        if f["path"] == m.path and abs(int(f["line"]) - m.line) <= LINE_TOLERANCE
    ]


def latest(variant: str) -> list[dict[str, Any]]:
    """One row per case: the last success, else the last error (runs are append-only)."""
    best: dict[str, dict[str, Any]] = {}
    for ln in (RESULTS / f"{variant}.jsonl").read_text().splitlines():
        r = json.loads(ln)
        prev = best.get(r["case_id"])
        if prev is None or prev.get("error") or not r.get("error"):
            best[r["case_id"]] = r
    return list(best.values())


def score(variant: str, cases: dict[str, Case]) -> Score:
    rows = latest(variant)
    s = Score(variant, 0, 0, 0, 0, 0, 0, 0, 0, 0.0, [], 0)
    for r in rows:
        case = cases.get(r["case_id"])
        if case is None:
            continue
        if r.get("error"):
            s.errors += 1
            continue
        found = r["findings"]
        s.cost += r["cost_usd"]
        s.latencies.append(r["latency_s"])
        s.tool_calls += len(r["tool_calls"])
        if case.kind == "seeded":
            s.seeded += 1
            h = hits(case, found)
            s.caught += bool(h)
            s.findings_seeded += len(found)
            s.on_target += len(h)
        else:
            s.clean += 1
            s.findings_clean += len(found)
            s.clean_flagged += bool(found)
    return s


def by_op(variant: str, cases: dict[str, Case]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for r in latest(variant):
        case = cases.get(r["case_id"])
        if not case or not case.mutation or r.get("error"):
            continue
        got, tot = out.get(case.mutation.op, (0, 0))
        out[case.mutation.op] = (got + bool(hits(case, r["findings"])), tot + 1)
    return out


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, default=Path("bench/cases.jsonl"))
    ap.add_argument("--out", type=Path)
    ap.add_argument("variants", nargs="+")
    args = ap.parse_args()

    cases = {c.id: c for c in load(args.cases)}
    scores = [score(v, cases) for v in args.variants]
    ops = sorted({op for v in args.variants for op in by_op(v, cases)})
    per_op = [
        [v] + [f"{g}/{t}" for g, t in (by_op(v, cases).get(op, (0, 0)) for op in ops)]
        for v in args.variants
    ]
    md = "\n\n".join(
        [
            table(HEADER, [s.row() for s in scores]),
            "Recall by mutation type:",
            table(["variant", *ops], per_op),
        ]
    )
    print(md)
    if args.out:
        args.out.write_text(md + "\n")


if __name__ == "__main__":
    main()
