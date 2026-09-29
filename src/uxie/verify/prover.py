"""Prover agent: turn a review finding into a pytest test, then run it in the sandbox."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uxie.review import tools
from uxie.review.agent import Conversation
from uxie.review.findings import Finding
from uxie.review.model import ChatModel, Usage
from uxie.review.reviewer import PullRequest
from uxie.review.workspace import Workspace
from uxie.sandbox.executor import Executor, stage
from uxie.sandbox.spec import ERROR, FAILED, INCONCLUSIVE, RunOutcome, VerifyJob, decide

TEST_PATH = "test_uxie_verify.py"  # repo root, so `python -m pytest` can import the package

SYSTEM = """\
You write a single pytest file that checks whether a reported bug is real.

The test must assert the CORRECT behavior the code should have. It must PASS on correct
code and FAIL if the reported bug exists. It will run twice: on the code before the PR
and on the code after it.

Rules:
- Import the code under test from the repository root (e.g. `from pkg.module import f`).
  Read the files first; call functions with their real signatures.
- Exercise the exact code path in the report. Keep it small: one to three test functions.
- No network, no files outside /tmp, no sleeping, deterministic inputs only.
- Never mock or monkeypatch the code under test, and never assert on the bug itself
  (assert what correct code does, not what the buggy code does).
- Use only the standard library and pytest.

Call submit_test with the complete file contents."""

RETRY = """\
Your test could not run, so it proves nothing yet:

{failure}

{tail}

Fix the test so it actually exercises the code (imports, signatures, setup). Do not change
what it asserts about correct behavior. Call submit_test again."""

FORCE = "Call submit_test now with the complete test file."

SUBMIT_TEST: dict[str, Any] = {
    "toolSpec": {
        "name": "submit_test",
        "description": "Submit the complete pytest file.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "test_code": {"type": "string"},
                    "rationale": {
                        "type": "string",
                        "description": "One sentence: why this fails if the bug is real.",
                    },
                },
                "required": ["test_code", "rationale"],
            }
        },
    }
}


@dataclass(frozen=True)
class ProverSettings:
    max_attempts: int = 3  # retries only when the test can't run; a passing test is final
    max_tool_turns: int = 6
    timeout_s: int = 60


@dataclass
class Proof:
    finding: Finding
    verdict: str
    test_code: str = ""
    rationale: str = ""
    attempts: int = 0
    head: RunOutcome | None = None
    base: RunOutcome | None = None
    usage: Usage = field(default_factory=Usage)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding": self.finding.to_dict(),
            "verdict": self.verdict,
            "test_code": self.test_code,
            "rationale": self.rationale,
            "attempts": self.attempts,
            "head": self.head.to_dict() if self.head else None,
            "base": self.base.to_dict() if self.base else None,
            "usage": vars(self.usage),
            "note": self.note,
        }


def _brief(f: Finding, pr: PullRequest) -> str:
    diff = next((fp.render() for fp in pr.files if fp.path == f.path), "")
    return (
        f"PR title: {pr.title}\n\n"
        f"Reported bug at {f.path}:{f.line} ({f.severity}, {f.category}): {f.title}\n"
        f"{f.explanation}\n\n"
        f"Diff of {f.path} (new-file line numbers on the left):\n\n{diff}"
    )


def _syntax_error(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno})"
    has_test = any(
        isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("test")
        for n in ast.walk(tree)
    )
    return None if has_test else "No test functions found (names must start with test)."


def prove(
    finding: Finding,
    pr: PullRequest,
    head_ws: Workspace,
    base_snapshot: Path | None,
    head_snapshot: Path,
    model: ChatModel,
    ex: Executor,
    workdir: Path,
    cfg: ProverSettings | None = None,
) -> Proof:
    cfg = cfg or ProverSettings()
    conv = Conversation(
        model,
        SYSTEM,
        _brief(finding, pr),
        [*tools.CONTEXT_TOOLS, SUBMIT_TEST],
        "submit_test",
        head_ws,
        FORCE,
    )
    proof = Proof(finding, INCONCLUSIVE, usage=conv.usage)

    for attempt in range(1, cfg.max_attempts + 1):
        sub = conv.run(cfg.max_tool_turns)
        proof.attempts = attempt
        if sub is None:
            proof.note = "prover never submitted a test"
            return proof
        proof.test_code = str(sub.get("test_code") or "")
        proof.rationale = str(sub.get("rationale") or "")

        problem = _syntax_error(proof.test_code)
        if problem is None:
            job = VerifyJob(
                f"{finding.fingerprint or 'f'}-{attempt}",
                "",
                "",
                "",
                TEST_PATH,
                proof.test_code,
                finding.to_dict(),
                cfg.timeout_s,
            )
            proof.head = ex.run(
                stage(head_snapshot, job, workdir / "head"), TEST_PATH, cfg.timeout_s
            )
            if proof.head.outcome != ERROR:
                break  # failed or passed on head: the verdict stands, no second chances
            problem = f"{proof.head.failure}".strip() or "the test errored before asserting"
        if attempt == cfg.max_attempts:
            proof.note = f"test never ran: {problem}"
            return proof
        tail = proof.head.output_tail[-1500:] if proof.head else ""
        conv.reply(RETRY.format(failure=problem, tail=tail))

    assert proof.head is not None
    if proof.head.outcome == FAILED and base_snapshot is not None:
        job = VerifyJob("base", "", "", "", TEST_PATH, proof.test_code, {}, cfg.timeout_s)
        proof.base = ex.run(stage(base_snapshot, job, workdir / "base"), TEST_PATH, cfg.timeout_s)
    proof.verdict = decide(proof.head, proof.base)
    return proof
