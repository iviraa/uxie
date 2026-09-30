"""Replies posted under a review comment once its finding has been tested."""

from __future__ import annotations

from uxie.sandbox.spec import CONFIRMED, LIKELY, REFUTED
from uxie.verify.prover import Proof

HEADLINES = {
    CONFIRMED: "✅ **Confirmed by a test.** It passes on the code before this PR and fails "
    "after it.",
    LIKELY: "🧪 **Reproduced.** This test fails on the PR. The code it tests is new, so there is "
    "no before/after comparison.",
    REFUTED: "❎ **Couldn't reproduce.** A test that runs this line passes, so this may be a "
    "false alarm.",
}


def verdict_reply(proof: Proof) -> str | None:
    """Markdown reply, or None when the result is inconclusive (stay quiet)."""
    headline = HEADLINES.get(proof.verdict)
    if headline is None:
        return None
    parts = [headline]
    if proof.head and proof.head.failure and proof.verdict != REFUTED:
        parts += ["", f"`{proof.head.failure[:300]}`"]
    parts += [
        "",
        "<details><summary>Test</summary>",
        "",
        "```python",
        proof.test_code.strip(),
        "```",
        "</details>",
    ]
    return "\n".join(parts)
