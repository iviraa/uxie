"""Benchmark cases: a real commit replayed as a PR, optionally with one injected bug."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from bench.mutate import Mutation
from uxie.diff import filters
from uxie.diff.patch import make_patch, parse_patch
from uxie.review.reviewer import PullRequest

CACHE = Path(__file__).parent / "cache"


@dataclass(frozen=True)
class Case:
    id: str
    repo: str  # local clone path
    base: str
    head: str
    title: str
    body: str
    kind: str  # seeded | clean
    mutation: Mutation | None = None
    mutated_source: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(line: str) -> Case:
        d = json.loads(line)
        m = d.pop("mutation")
        return Case(**d, mutation=Mutation(**m) if m else None)


def load(path: Path) -> list[Case]:
    return [Case.from_json(ln) for ln in path.read_text().splitlines() if ln.strip()]


def git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], check=True, capture_output=True, text=True
    ).stdout


def show(repo: str, sha: str, path: str) -> str:
    try:
        return git(repo, "show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return ""  # file added in this commit


def changed_paths(repo: str, base: str, head: str) -> list[tuple[str, str]]:
    status_map = {"A": "added", "M": "modified", "D": "removed", "R": "renamed"}
    out = []
    for ln in git(repo, "diff", "--name-status", "--no-renames", base, head).splitlines():
        code, path = ln.split("\t", 1)
        out.append((path, status_map.get(code[0], "modified")))
    return out


def snapshot(repo: str, sha: str) -> Path:
    """Extract the tree at sha once and reuse it (read-only)."""
    dest = CACHE / "snapshots" / Path(repo).name / sha
    if not dest.exists():
        tmp = dest.with_suffix(".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        archive = subprocess.run(
            ["git", "-C", repo, "archive", sha], check=True, capture_output=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=archive, check=True)
        tmp.rename(dest)
    return dest


def materialize(case: Case) -> tuple[PullRequest, Path]:
    """Build the PR the reviewer sees and a workspace dir that matches it."""
    root = snapshot(case.repo, case.head)
    if case.mutation and case.mutated_source is not None:
        work = CACHE / "work" / case.id
        if not work.exists():
            shutil.copytree(root, work, symlinks=True)
            (work / case.mutation.path).write_text(case.mutated_source)
        root = work

    files = []
    for path, status in changed_paths(case.repo, case.base, case.head):
        if status == "removed":
            continue
        new = (root / path).read_text(errors="replace") if (root / path).is_file() else ""
        patch = make_patch(show(case.repo, case.base, path), new)
        if patch:
            files.append(parse_patch(path, patch, status))
    return PullRequest(case.title, case.body, filters.select(files)), root
