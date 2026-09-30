"""Run one reviewer variant over a case file. Resumable: finished case ids are skipped.

usage: python -m bench.run --cases bench/cases.jsonl --variant haiku-baseline --mode baseline
"""

from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bench.cases import Case, load, materialize
from uxie.common.config import ReviewSettings
from uxie.review.model import BedrockModel
from uxie.review.reviewer import review
from uxie.review.workspace import Workspace

RESULTS = Path(__file__).parent / "results"
DEFAULT_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def run_case(case: Case, model: BedrockModel, cfg: ReviewSettings) -> dict:
    pr, root = materialize(case)
    if not pr.files:
        return {"case_id": case.id, "error": "no reviewable files"}
    res = review(pr, Workspace(root), model, cfg)
    return {"case_id": case.id, **res.to_dict()}


def _rows(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, default=Path("bench/cases.jsonl"))
    ap.add_argument("--variant", required=True)
    ap.add_argument("--mode", choices=["baseline", "context", "specialists"], required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--price-in", type=float, default=1.0)
    ap.add_argument("--price-out", type=float, default=5.0)
    ap.add_argument("--max-tool-turns", type=int, default=8)
    ap.add_argument("--min-confidence", type=float, default=0.5)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--shard", default="0/1", help="k/n: run every n-th case starting at k")
    ap.add_argument("--region", default="us-east-2")
    ap.add_argument("--rpm", type=float, default=9, help="stay under the account's Bedrock quota")
    args = ap.parse_args()

    cfg = ReviewSettings(
        model_id=args.model,
        mode=args.mode,
        max_tool_turns=args.max_tool_turns,
        min_confidence=args.min_confidence,
        max_comments=10,
        max_changed_lines=10**9,
        price_in_per_mtok=args.price_in,
        price_out_per_mtok=args.price_out,
    )
    out = RESULTS / f"{args.variant}.jsonl"
    out.parent.mkdir(exist_ok=True)
    done = {r["case_id"] for r in _rows(out) if not r.get("error")}
    cases = [c for c in load(args.cases) if c.id not in done]
    k, n = (int(x) for x in args.shard.split("/"))
    cases = [c for i, c in enumerate(cases) if i % n == k]
    if args.limit:
        cases = cases[: args.limit]
    meta = {"variant": args.variant, "mode": args.mode, "model": args.model}
    print(f"{args.variant}: {len(cases)} to run, {len(done)} already done")

    model = BedrockModel(args.model, args.region, args.rpm)
    lock = threading.Lock()
    spent = 0.0
    with ThreadPoolExecutor(args.workers) as pool, out.open("a") as fh:
        futs = {pool.submit(run_case, c, model, cfg): c for c in cases}
        for i, fut in enumerate(as_completed(futs), 1):
            case = futs[fut]
            try:
                row = fut.result()
            except Exception as e:  # keep going; the error is recorded and scored
                row = {"case_id": case.id, "error": f"{type(e).__name__}: {e}"[:500]}
            row |= meta
            with lock:
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                spent += row.get("cost_usd", 0.0)
            print(
                f"[{i}/{len(cases)}] {case.id} findings={len(row.get('findings', []))} "
                f"cost=${spent:.3f} {row.get('error') or ''}"
            )


if __name__ == "__main__":
    main()
