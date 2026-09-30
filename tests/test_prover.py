from pathlib import Path

from tests.conftest import PATCH, FakeModel, tool_turn
from uxie.diff.patch import parse_patch
from uxie.review.findings import Finding
from uxie.review.reviewer import PullRequest
from uxie.review.workspace import Workspace
from uxie.sandbox.spec import CONFIRMED, INCONCLUSIVE, LIKELY, REFUTED, RunOutcome
from uxie.verify.prover import ProverSettings, prove

GOOD_TEST = "from a import f\ndef test_f():\n    assert f([1, 2]) == 1\n"


class FakeExec:
    """Returns scripted outcomes per side; records which code ran where."""

    def __init__(self, head: list[str], base: str = "passed", reached=None):
        self.head, self.base = list(head), base
        self.reached = list(reached) if reached else None  # per head run; None = line 4 hit
        self.targets: list = []
        self.ran: list[tuple[str, str]] = []

    def run(self, src: Path, test_path: str, timeout_s: int, env=None, target_file=None, lang=None):
        side = src.name
        self.targets.append((side, target_file))
        code = (src / test_path).read_text()
        self.ran.append((side, code))
        outcome = self.head.pop(0) if side == "head" else self.base
        failure = "ImportError: cannot import name 'g'" if outcome == "error" else ""
        reached = [4]
        if side == "head" and self.reached is not None:
            reached = self.reached.pop(0)
        return RunOutcome(outcome, 1, 0.1, failure, "tail", reached)


def _finding():
    return Finding(
        "a.py", 4, "high", "bug", "wrong element", "returns last", 0.9, fingerprint="abc123abc123"
    )


def _setup(tmp_path):
    base, head = tmp_path / "b", tmp_path / "h"
    for d in (base, head):
        d.mkdir()
        (d / "a.py").write_text("def f(xs):\n    return xs[-1]\n")
    pr = PullRequest("t", "", [parse_patch("a.py", PATCH)])
    return pr, Workspace(head), base, head


def _submit(code, uid):
    return tool_turn("submit_test", {"test_code": code, "rationale": "r"}, uid)


def _prove(tmp_path, model, ex, base=True):
    pr, ws, b, h = _setup(tmp_path)
    return prove(
        _finding(),
        pr,
        ws,
        b if base else None,
        h,
        model,
        ex,
        tmp_path / "w",
        ProverSettings(max_attempts=3, max_tool_turns=2),
    )


def test_regression_confirmed_first_try(tmp_path):
    ex = FakeExec(["failed"], base="passed")
    p = _prove(tmp_path, FakeModel([_submit(GOOD_TEST, "u1")]), ex)
    assert (p.verdict, p.attempts) == (CONFIRMED, 1)
    assert [s for s, _ in ex.ran] == ["head", "base"]


def test_unrunnable_test_gets_feedback_and_retry(tmp_path):
    model = FakeModel(
        [_submit("from a import g\ndef test_x():\n    g()\n", "u1"), _submit(GOOD_TEST, "u2")]
    )
    ex = FakeExec(["error", "failed"], base="passed")
    p = _prove(tmp_path, model, ex)
    assert (p.verdict, p.attempts) == (CONFIRMED, 2)
    feedback = model.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert feedback["toolUseId"] == "u1"
    assert "cannot import name 'g'" in feedback["content"][0]["text"]
    assert ex.ran[-2] == ("head", GOOD_TEST)  # the fixed test is what ran


def test_passing_test_is_final_no_retry(tmp_path):
    model = FakeModel([_submit(GOOD_TEST, "u1")])  # a second submit would raise IndexError
    ex = FakeExec(["passed"])
    p = _prove(tmp_path, model, ex)
    assert (p.verdict, p.attempts) == (REFUTED, 1)
    assert [s for s, _ in ex.ran] == ["head"]


def test_syntax_error_retried_without_running(tmp_path):
    model = FakeModel([_submit("def test_x(:\n", "u1"), _submit(GOOD_TEST, "u2")])
    ex = FakeExec(["failed"], base="passed")
    p = _prove(tmp_path, model, ex)
    assert p.verdict == CONFIRMED and [s for s, _ in ex.ran] == ["head", "base"]
    assert (
        "SyntaxError"
        in model.calls[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"]
    )


def test_gives_up_after_max_attempts(tmp_path):
    bad = "from a import g\ndef test_x():\n    g()\n"
    model = FakeModel([_submit(bad, f"u{i}") for i in range(3)])
    p = _prove(tmp_path, model, FakeExec(["error"] * 3))
    assert (p.verdict, p.attempts) == (INCONCLUSIVE, 3)
    assert p.note.startswith("test never exercised")


def test_no_base_snapshot_is_likely(tmp_path):
    p = _prove(tmp_path, FakeModel([_submit(GOOD_TEST, "u1")]), FakeExec(["failed"]), base=False)
    assert p.verdict == LIKELY


def test_passing_without_reaching_the_line_is_retried(tmp_path):
    model = FakeModel([_submit(GOOD_TEST, "u1"), _submit(GOOD_TEST, "u2")])
    ex = FakeExec(["passed", "passed"], reached=[[90, 91], [3, 4]])
    p = _prove(tmp_path, model, ex)
    assert (p.verdict, p.attempts) == (REFUTED, 2)  # second test reached line 4 -> final
    feedback = model.calls[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"]
    assert "never executed a.py line 4" in feedback and "90, 91" in feedback
    assert ex.targets[0] == ("head", "a.py")


def test_failing_without_reaching_is_not_evidence(tmp_path):
    model = FakeModel([_submit(GOOD_TEST, f"u{i}") for i in range(3)])
    p = _prove(tmp_path, model, FakeExec(["failed"] * 3, reached=[[], [], [90]]))
    assert (p.verdict, p.attempts) == (INCONCLUSIVE, 3)
    assert "never exercised" in p.note


def test_proof_usage_counts_model_calls(tmp_path):
    p = _prove(tmp_path, FakeModel([_submit(GOOD_TEST, "u1")]), FakeExec(["failed"]))
    assert p.usage.calls == 1 and p.usage.input_tokens == 100
