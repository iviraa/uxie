from uxie.review.findings import Finding
from uxie.sandbox.spec import RunOutcome
from uxie.verify.prover import Proof
from uxie.verify.render import verdict_reply


def _proof(verdict, failure="AssertionError: assert 1 == 2"):
    f = Finding("a.py", 3, "high", "bug", "t", "e", 0.9)
    return Proof(
        f,
        verdict,
        "def test_x():\n    assert f() == 2\n",
        head=RunOutcome("failed", 1, 0.1, failure),
    )


def test_confirmed_shows_failure_and_test():
    body = verdict_reply(_proof("confirmed"))
    assert body.startswith("✅") and "assert 1 == 2" in body and "def test_x" in body


def test_refuted_hides_failure_line():
    body = verdict_reply(_proof("refuted"))
    assert body.startswith("❎") and "assert 1 == 2" not in body


def test_inconclusive_stays_quiet():
    assert verdict_reply(_proof("inconclusive")) is None


def test_test_block_is_labelled_with_the_language():
    from uxie.sandbox.spec import RunOutcome

    f = Finding("go/limits/limits.go", 5, "high", "bug", "t", "e", 0.9)
    p = Proof(f, "confirmed", "package limits\n", head=RunOutcome("failed", 1, 0.1, "x"))
    assert "```go\n" in verdict_reply(p) and "```python" not in verdict_reply(p)
