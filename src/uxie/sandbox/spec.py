"""Verify-job contract shared by the orchestrator, dispatcher, and sandbox runner."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PASSED, FAILED, ERROR, TIMEOUT = "passed", "failed", "error", "timeout"  # single-run outcomes
CONFIRMED, LIKELY, REFUTED, INCONCLUSIVE = "confirmed", "likely", "refuted", "inconclusive"


@dataclass(frozen=True)
class VerifyJob:
    job_id: str
    repo: str
    base_sha: str
    head_sha: str
    test_path: str  # where the generated test is written inside the repo
    test_code: str
    finding: dict[str, Any] = field(default_factory=dict)
    timeout_s: int = 60

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunOutcome:
    outcome: str  # passed | failed | error | timeout
    exit_code: int
    duration_s: float
    failure: str = ""  # first line of the failure message, e.g. "AssertionError: assert 1 == 2"
    output_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Verdict:
    job_id: str
    verdict: str  # confirmed | likely | refuted | inconclusive
    head: RunOutcome
    base: RunOutcome | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "verdict": self.verdict,
            "head": self.head.to_dict(),
            "base": self.base.to_dict() if self.base else None,
        }


def decide(head: RunOutcome, base: RunOutcome | None) -> str:
    """Differential verdict: the bug must show on head and, ideally, not on base."""
    if head.outcome != FAILED:
        return REFUTED if head.outcome == PASSED else INCONCLUSIVE
    if base is None or base.outcome in (ERROR, TIMEOUT):
        return LIKELY  # base can't run the test (e.g. new code): weaker evidence
    return CONFIRMED if base.outcome == PASSED else INCONCLUSIVE
