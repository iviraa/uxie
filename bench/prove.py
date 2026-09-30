"""Prove every finding from a review variant in the sandbox, then derive filtered variants.

usage:
  python -m bench.prove run --source haiku-context --shard 0/2 --model us.anthropic...
  python -m bench.prove derive --source haiku-context
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from bench.cases import Case, load, materialize, snapshot
from bench.score import RESULTS, latest
from uxie.review.findings import Finding
from uxie.review.model import BedrockModel
from uxie.review.workspace import Workspace
from uxie.sandbox.envs import Deps
from uxie.sandbox.executor import DockerExecutor, Executor
from uxie.sandbox.languages import PYTHON, for_path
from uxie.sandbox.spec import CONFIRMED, LIKELY, REFUTED
from uxie.verify.prover import ProverSettings, TestWriter, prove

JOBS = Path.home() / ".uxie" / "jobs"
PRICES = (1.0, 5.0)  # USD per million input/output tokens; set from --price-in/--price-out
WRITER_FACTORY: Callable[[Path], TestWriter] | None = None  # set by local experiment scripts


def _commit_date(case: Case) -> str:
    iso = subprocess.run(
        ["git", "-C", case.repo, "log", "-1", "--format=%cI", case.head],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return (datetime.fromisoformat(iso) + timedelta(days=1)).isoformat()  # deps as of that day


def prove_case(
    case: Case,
    review: dict[str, Any],
    model: BedrockModel,
    ex: Executor,
    envs: Deps,
) -> dict[str, Any]:
    t0 = time.monotonic()
    pr, head_root = materialize(case)
    base_root = snapshot(case.repo, case.base)
    date = _commit_date(case)
    proofs = []
    env: Path | None = None
    for raw in review["findings"]:
        f = Finding(**raw)
        work = JOBS / f"bench-{uuid.uuid4().hex[:8]}"
        lang = for_path(f.path) or PYTHON
        env = envs.ensure(head_root, date, f.path, lang.name)
        writer = None
        if WRITER_FACTORY is not None:  # alternative test writer gets a private repo copy
            view = work / "view"
            shutil.copytree(head_root, view, symlinks=True)
            writer = WRITER_FACTORY(view)
        try:
            p = prove(
                f,
                pr,
                Workspace(head_root),
                base_root,
                head_root,
                model,
                ex,
                work,
                ProverSettings(),
                env,
                writer,
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if writer is not None:
            cost, turns = getattr(writer, "cost_usd", 0.0), getattr(writer, "turns", 0)
        else:
            cost, turns = p.usage.cost_usd(*PRICES), p.usage.calls
        proofs.append(
            {
                **p.to_dict(),
                "cost_usd": cost,
                "turns": turns,
                "env": env.name if env else None,
                "lang": lang.name,
            }
        )
    if case.mutation:  # mutated copies are per-case; don't let them pile up on disk
        shutil.rmtree(head_root, ignore_errors=True)
    return {
        "case_id": case.id,
        "env": env.name if env else None,
        "proofs": proofs,
        "prove_latency_s": round(time.monotonic() - t0, 2),
    }


def _stem(args: argparse.Namespace) -> str:
    return f"{args.source}-{args.tag}" if args.tag else args.source


def run(args: argparse.Namespace) -> None:
    k, n = (int(x) for x in args.shard.split("/"))
    out = RESULTS / f"{_stem(args)}-proofs.{k}.jsonl"
    done = (
        {json.loads(ln)["case_id"] for ln in out.read_text().splitlines()}
        if out.exists()
        else set()
    )
    cases = {c.id: c for c in load(args.cases)}
    todo = [
        r
        for r in latest(args.source)
        if not r.get("error")
        and r["findings"]
        and r["case_id"] not in done
        and int(r["case_id"].split("-")[-2], 16) % n == k
    ]
    global PRICES
    PRICES = (args.price_in, args.price_out)
    if args.held_below is not None:  # prove only what the live gate would hold back
        todo = [
            {**r, "findings": [f for f in r["findings"] if f["confidence"] < args.held_below]}
            for r in todo
        ]
        todo = [r for r in todo if r["findings"]]
    if args.only_cases:  # restrict to the cases of an earlier run, for paired comparisons
        keep = {json.loads(ln)["case_id"] for ln in args.only_cases.read_text().splitlines()}
        todo = [r for r in todo if r["case_id"] in keep]
    if args.max_findings:  # random sample of whole cases, capped by total findings
        random.Random(7).shuffle(todo)
        picked, total = [], 0
        for r in todo:
            if total >= args.max_findings:
                break
            picked.append(r)
            total += len(r["findings"])
        todo = picked
    print(f"shard {k}/{n}: {len(todo)} cases, {sum(len(r['findings']) for r in todo)} findings")

    model = BedrockModel(args.model, "us-east-2", rpm=args.rpm)
    ex: Executor
    if args.executor == "k8s":
        from uxie.sandbox.k8s import K8sExecutor

        ex = K8sExecutor(JOBS)
    else:
        ex = DockerExecutor()
    envs = Deps(JOBS / "envs")
    lock, spent = threading.Lock(), 0.0
    with ThreadPoolExecutor(args.workers) as pool, out.open("a") as fh:
        futs = {pool.submit(prove_case, cases[r["case_id"]], r, model, ex, envs): r for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            r = futs[fut]
            try:
                row = fut.result()
            except Exception as e:  # recorded; derive treats the case as unverified
                row = {"case_id": r["case_id"], "error": f"{type(e).__name__}: {e}"[:500]}
            with lock:
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                spent += sum(p["cost_usd"] for p in row.get("proofs", []))
            verdicts = [p["verdict"][:4] for p in row.get("proofs", [])]
            print(
                f"[{i}/{len(todo)}] {r['case_id']} {verdicts} ${spent:.3f} "
                f"{row.get('error') or ''}",
                flush=True,
            )


def _fkey(f: dict[str, Any]) -> str:
    return str(f.get("fingerprint") or f"{f['path']}:{f['line']}")


def derive(args: argparse.Namespace) -> None:
    proofs: dict[str, dict[str, Any]] = {}
    for path in sorted(RESULTS.glob(f"{_stem(args)}-proofs.*.jsonl")):
        for ln in path.read_text().splitlines():
            row = json.loads(ln)
            if not row.get("error") or row["case_id"] not in proofs:
                proofs[row["case_id"]] = row
    # score only cases we proved, plus the source's own rows for exactly those cases
    base_rows = [r for r in latest(args.source) if r["case_id"] in proofs]
    with (RESULTS / f"{_stem(args)}-subset.jsonl").open("w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in base_rows)
    print(f"{len(base_rows)} proven cases")

    gate = args.held_below
    proven_ok = {CONFIRMED, LIKELY}
    policies: dict[str, Callable[[dict[str, Any], str | None], bool]] = (
        {
            "gated": lambda f, v: f["confidence"] >= gate or v in proven_ok,  # live bot's rule
            "gated-lenient": lambda f, v: f["confidence"] >= gate or v != REFUTED,
        }
        if gate is not None
        else {"strict": lambda f, v: v in proven_ok, "lenient": lambda f, v: v != REFUTED}
    )
    for name, keep in policies.items():
        out = RESULTS / f"{_stem(args)}-verify-{name}.jsonl"
        with out.open("w") as fh:
            for r in base_rows:
                p = proofs[r["case_id"]]
                verdict = {_fkey(x["finding"]): x["verdict"] for x in p.get("proofs", [])}
                needs = [f for f in r.get("findings", []) if gate is None or f["confidence"] < gate]
                if r.get("error") or p.get("error") or any(_fkey(f) not in verdict for f in needs):
                    fh.write(json.dumps({"case_id": r["case_id"], "error": "unproven"}) + "\n")
                    continue
                kept = [f for f in r["findings"] if keep(f, verdict.get(_fkey(f)))]
                extra = sum(x["cost_usd"] for x in p.get("proofs", []))
                fh.write(
                    json.dumps(
                        {
                            **r,
                            "findings": kept,
                            "cost_usd": r["cost_usd"] + extra,
                            "latency_s": r["latency_s"] + p.get("prove_latency_s", 0.0),
                            "verdicts": list(verdict.values()),
                        }
                    )
                    + "\n"
                )
        print(f"wrote {out.name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--source", default="haiku-context")
    r.add_argument("--cases", type=Path, default=Path("bench/cases.jsonl"))
    r.add_argument("--shard", default="0/1")
    r.add_argument("--model", default="global.anthropic.claude-haiku-4-5-20251001-v1:0")
    r.add_argument("--rpm", type=float, default=9)
    r.add_argument("--workers", type=int, default=3)
    r.add_argument("--executor", choices=["k8s", "docker"], default="docker")
    r.add_argument("--max-findings", type=int, default=0)
    r.add_argument("--tag", default="")
    r.add_argument("--only-cases", type=Path, help="results jsonl whose case ids to keep")
    r.add_argument("--held-below", type=float, help="prove only findings under this confidence")
    r.add_argument("--price-in", type=float, default=1.0)
    r.add_argument("--price-out", type=float, default=5.0)
    d = sub.add_parser("derive")
    d.add_argument("--source", default="haiku-context")
    d.add_argument("--tag", default="")
    d.add_argument("--held-below", type=float, help="score the live gate: prove only below this")
    args = ap.parse_args()
    run(args) if args.cmd == "run" else derive(args)


if __name__ == "__main__":
    main()
