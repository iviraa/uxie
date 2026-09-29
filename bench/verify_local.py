"""Review a local commit range, then try to prove each finding in the sandbox.

usage: python -m bench.verify_local --repo PATH --base SHA --head SHA [--executor k8s|docker]
"""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path

from bench.cases import changed_paths, show, snapshot
from uxie.common.config import ReviewSettings
from uxie.diff import filters
from uxie.diff.patch import make_patch, parse_patch
from uxie.review.findings import Finding, parse_findings, validate
from uxie.review.model import BedrockModel
from uxie.review.reviewer import PullRequest, review
from uxie.review.workspace import Workspace
from uxie.sandbox.executor import DockerExecutor, Executor
from uxie.verify.prover import prove

JOBS = Path.home() / ".uxie" / "jobs"
MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--executor", choices=["k8s", "docker"], default="k8s")
    ap.add_argument(
        "--extra-finding",
        action="append",
        default=[],
        help="JSON finding to prove in addition to the reviewer's",
    )
    args = ap.parse_args()

    files = []
    for path, status in changed_paths(args.repo, args.base, args.head):
        if status != "removed":
            patch = make_patch(show(args.repo, args.base, path), show(args.repo, args.head, path))
            files.append(parse_patch(path, patch, status))
    pr = PullRequest("local review", "", filters.select(files))
    base_snap, head_snap = snapshot(args.repo, args.base), snapshot(args.repo, args.head)

    model = BedrockModel(MODEL, "us-east-2", rpm=9)
    cfg = ReviewSettings(MODEL, "context", 8, 0.5, 10, 10**9, 1.0, 5.0)
    res = review(pr, Workspace(head_snap), model, cfg)
    extra, _ = validate(
        parse_findings([json.loads(x) for x in args.extra_finding]), pr.files, 0.0, 10
    )
    findings: list[Finding] = [*res.findings, *extra]
    print(f"review: {len(res.findings)} finding(s), +{len(extra)} extra, ${res.cost_usd:.4f}\n")

    ex: Executor
    if args.executor == "k8s":
        from uxie.sandbox.k8s import K8sExecutor

        ex = K8sExecutor(JOBS)
    else:
        ex = DockerExecutor()

    for f in findings:
        work = JOBS / f"local-{uuid.uuid4().hex[:8]}"
        try:
            p = prove(f, pr, Workspace(head_snap), base_snap, head_snap, model, ex, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        cost = p.usage.cost_usd(1.0, 5.0)
        print(f"== {f.path}:{f.line} {f.title}")
        print(f"   verdict: {p.verdict.upper()}  attempts={p.attempts}  ${cost:.4f}  {p.note}")
        print(f"   head: {p.head.outcome if p.head else '-'} {p.head.failure if p.head else ''}")
        print(f"   base: {p.base.outcome if p.base else '-'} {p.base.failure if p.base else ''}")
        print("   " + p.test_code.replace("\n", "\n   ") + "\n")


if __name__ == "__main__":
    main()
