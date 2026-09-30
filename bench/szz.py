"""Mine real bug-introducing commits with SZZ, as benchmark cases.

For each small bug-fix commit, the lines it removed or changed are blamed (in the fix's
parent) to find the commit that introduced them. That commit, replayed as a PR, should
have drawn a review comment on those lines.

usage: python -m bench.szz --repo PATH [--repo PATH ...] --per-repo 15 --out bench/cases_real.jsonl
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from bench.cases import Case, changed_paths, git, show
from bench.seed import SOURCES, is_source
from uxie.diff.patch import make_patch, parse_patch

FIX_RE = re.compile(r"\b(fix(es|ed)?|bug|regression|crash(es)?|incorrect|wrong|broken)\b", re.I)
NOISE_RE = re.compile(
    r"\b(typos?|docs?|docstrings?|readme|format(ting)?|style|lint|refactor|rename|bump|"
    r"release|version|changelog|ci|tests?|typing|mypy|pre-commit)\b",
    re.I,
)
MAX_FIX_REMOVED = 15  # surgical fixes only; big ones are rewrites, not bug fixes
INTRO_LINES = (3, 400)
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def removed_lines(repo: str, parent: str, fix: str, path: str) -> list[int]:
    """Old-side line numbers the fix removed or changed in path."""
    out = git(repo, "diff", "-U0", "-w", parent, fix, "--", path)
    lines: list[int] = []
    for ln in out.splitlines():
        m = _HUNK.match(ln)
        if m:
            start, count = int(m.group(1)), int(m.group(2) or "1")
            lines += range(start, start + count)
    return lines


def blame(repo: str, rev: str, path: str, line: int) -> tuple[str, str, int] | None:
    """(introducing sha, path at that commit, line number there) for one line."""
    try:
        out = git(repo, "blame", "-w", "--porcelain", "-L", f"{line},{line}", rev, "--", path)
    except subprocess.CalledProcessError:
        return None
    rows = out.splitlines()
    sha, orig, _ = rows[0].split()[:3]
    name = next((r.split(" ", 1)[1] for r in rows if r.startswith("filename ")), path)
    return sha, name, int(orig)


def _size(repo: str, sha: str) -> int:
    total = 0
    for ln in git(repo, "show", "--numstat", "--format=", sha).splitlines():
        a, d, _ = ln.split("\t", 2)
        total += int(a) + int(d) if a.isdigit() and d.isdigit() else 0
    return total


def mine(repo: str, per_repo: int, max_scan: int, lang: str = "python") -> list[Case]:
    truth: dict[str, set[tuple[str, int, str]]] = defaultdict(set)
    log = git(repo, "log", "--no-merges", "--format=%H%x00%s", f"-n{max_scan}")
    for row in log.splitlines():
        fix, subject = row.split("\x00", 1)
        if not FIX_RE.search(subject) or NOISE_RE.search(subject):
            continue
        try:
            parent = git(repo, "rev-parse", f"{fix}^").strip()
        except subprocess.CalledProcessError:
            continue
        paths = [
            p for p, s in changed_paths(repo, parent, fix) if s == "modified" and is_source(p, lang)
        ]
        removed = {p: removed_lines(repo, parent, fix, p) for p in paths}
        if not paths or not 0 < sum(map(len, removed.values())) <= MAX_FIX_REMOVED:
            continue
        for path, lines in removed.items():
            text = show(repo, parent, path).splitlines()
            for n in lines:
                src = text[n - 1].strip() if n <= len(text) else ""
                if not src or src.startswith(("#", "//", "/*", "*")):  # comments carry no bug
                    continue  # blank/comment lines carry no bug
                hit = blame(repo, parent, path, n)
                if hit and not hit[0].startswith("^"):  # "^" marks a boundary/root commit
                    truth[hit[0]].add((hit[1], hit[2], fix))
        if len(truth) >= per_repo * 3:
            break

    cases: list[Case] = []
    name = Path(repo).name
    for intro, blamed in truth.items():
        if len(cases) >= per_repo:
            break
        parents = git(repo, "rev-list", "--parents", "-n1", intro).split()
        if len(parents) != 2 or not INTRO_LINES[0] <= _size(repo, intro) <= INTRO_LINES[1]:
            continue  # skip merges, root commits, and huge drops
        msg = git(repo, "log", "-1", "--format=%s%n%n%b", intro).strip()
        title, _, body = msg.partition("\n")
        if NOISE_RE.search(title):
            continue
        base = parents[1]
        kept = []
        for path, line, fix in sorted(blamed):
            added = parse_patch(path, make_patch(show(repo, base, path), show(repo, intro, path)))
            if line in added.added_lines():  # sanity: the intro commit really added it
                kept.append({"path": path, "line": line, "fix": fix})
        if kept:
            cases.append(
                Case(
                    id=f"{name}-{intro[:10]}-real",
                    repo=str(Path(repo).resolve()),
                    base=base,
                    head=intro,
                    title=title,
                    body=body.strip(),
                    kind="real",
                    truth=kept,
                )
            )
    return cases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", required=True)
    ap.add_argument("--per-repo", type=int, default=15)
    ap.add_argument("--max-scan", type=int, default=3000)
    ap.add_argument("--out", type=Path, default=Path("bench/cases_real.jsonl"))
    ap.add_argument("--lang", choices=sorted(SOURCES), default="python")
    args = ap.parse_args()
    cases: list[Case] = []
    for repo in args.repo:
        got = mine(repo, args.per_repo, args.max_scan, args.lang)
        print(
            f"{Path(repo).name}: {len(got)} bug-introducing commits, "
            f"{sum(len(c.truth or []) for c in got)} truth lines"
        )
        cases += got
    args.out.write_text("".join(c.to_json() + "\n" for c in cases))
    print(f"wrote {len(cases)} cases to {args.out}")


if __name__ == "__main__":
    main()
