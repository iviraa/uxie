"""Build a seeded-bug benchmark from the history of local git repos.

usage: python -m bench.seed --repo PATH [--repo PATH ...] --per-repo 15 --out bench/cases.jsonl
"""

from __future__ import annotations

import argparse
import random
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bench import treemutate
from bench.cases import Case, changed_paths, git, show
from bench.mutate import BEHAVIOR, LOGIC, Mutation, mutate
from uxie.diff.patch import make_patch, parse_patch

MIN_ADDED, MAX_ADDED = 5, 400


SOURCES = {
    "python": (".py",),
    "go": (".go",),
    "typescript": (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"),
}
_SKIP_DIRS = {
    "vendor",
    "node_modules",
    "dist",
    "build",
    "testdata",
    "examples",
    "example",
    "docs",
    "benchmarks",
    "scripts",
    "__tests__",
    "test",
    "tests",
    "fixtures",
}


def _is_test(path: str) -> bool:
    p = Path(path)
    name = p.name
    return (
        bool(_SKIP_DIRS.intersection(p.parts[:-1]))
        or name.startswith("test_")
        or name.endswith(("_test.py", "_test.go", ".d.ts"))
        or ".test." in name
        or ".spec." in name
    )


def is_source(path: str, lang: str) -> bool:
    return path.endswith(SOURCES[lang]) and not _is_test(path)


def mutator(lang: str, ops: tuple[str, ...]) -> Callable[..., tuple[str, Mutation] | None]:
    if lang == "python":
        return lambda path, src, allowed, rng: mutate(path, src, allowed, rng, ops)
    return lambda path, src, allowed, rng: treemutate.mutate(path, src, allowed, rng, ops)


def seed_repo(
    repo: str,
    per_repo: int,
    rng: random.Random,
    max_scan: int,
    lang: str = "python",
    ops: tuple[str, ...] = LOGIC,
) -> list[Case]:
    inject = mutator(lang, ops)
    shas = git(repo, "log", "--no-merges", "--format=%H", f"-n{max_scan}").split()
    rng.shuffle(shas)
    cases: list[Case] = []
    for sha in shas:
        if len(cases) >= 2 * per_repo:
            break
        try:
            base = git(repo, "rev-parse", f"{sha}^").strip()
        except subprocess.CalledProcessError:
            continue  # root commit
        paths = changed_paths(repo, base, sha)
        py = [(p, s) for p, s in paths if s != "removed" and is_source(p, lang)]
        if not py or len(paths) > 20:
            continue
        added_total = 0
        mutation = None
        for path, _status in py:
            new = show(repo, sha, path)
            fp = parse_patch(path, make_patch(show(repo, base, path), new))
            added_total += fp.added
            if mutation is None:
                mutation = inject(path, new, fp.added_lines(), rng)
        if mutation is None or not MIN_ADDED <= added_total <= MAX_ADDED:
            continue

        msg = git(repo, "log", "-1", "--format=%s%n%n%b", sha).strip()
        title, _, body = msg.partition("\n")
        name = Path(repo).name
        common: dict[str, Any] = dict(
            repo=str(Path(repo).resolve()), base=base, head=sha, title=title, body=body.strip()
        )
        src, mut = mutation
        cases.append(
            Case(
                id=f"{name}-{sha[:10]}-seeded",
                kind="seeded",
                mutation=mut,
                mutated_source=src,
                **common,
            )
        )
        cases.append(Case(id=f"{name}-{sha[:10]}-clean", kind="clean", **common))
    return cases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", required=True)
    ap.add_argument("--per-repo", type=int, default=15)
    ap.add_argument("--max-scan", type=int, default=600)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=Path("bench/cases.jsonl"))
    ap.add_argument("--lang", choices=sorted(SOURCES), default="python")
    ap.add_argument("--ops", choices=["logic", "behavior", "all"], default="logic")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    cases: list[Case] = []
    chosen = {"logic": LOGIC, "behavior": BEHAVIOR, "all": LOGIC + BEHAVIOR}[args.ops]
    for repo in args.repo:
        got = seed_repo(repo, args.per_repo, rng, args.max_scan, args.lang, chosen)
        print(f"{Path(repo).name}: {len(got) // 2} commits")
        cases += got
    args.out.write_text("".join(c.to_json() + "\n" for c in cases))
    ops: dict[str, int] = {}
    for c in cases:
        if c.mutation:
            ops[c.mutation.op] = ops.get(c.mutation.op, 0) + 1
    print(f"wrote {len(cases)} cases to {args.out}; mutation ops: {ops}")


if __name__ == "__main__":
    main()
