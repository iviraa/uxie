from __future__ import annotations

from typing import Any

import pytest

from uxie.common.config import ReviewSettings
from uxie.review.model import Turn

PATCH = """@@ -1,4 +1,5 @@
 def f(xs):
-    return xs[0]
+    if not xs:
+        return None
+    return xs[-1]
 
@@ -20,2 +21,3 @@ def g():
     a = 1
+    b = a + 1
     return a"""


@pytest.fixture
def cfg() -> ReviewSettings:
    return ReviewSettings(
        model_id="test-model",
        mode="context",
        max_tool_turns=3,
        min_confidence=0.5,
        max_comments=10,
        max_changed_lines=1500,
        price_in_per_mtok=1.0,
        price_out_per_mtok=5.0,
    )


def tool_turn(name: str, args: dict[str, Any], uid: str = "t1") -> Turn:
    return Turn(
        content=[{"toolUse": {"toolUseId": uid, "name": name, "input": args}}],
        stop_reason="tool_use",
        usage={"inputTokens": 100, "outputTokens": 20},
    )


def text_turn(text: str) -> Turn:
    return Turn(
        content=[{"text": text}],
        stop_reason="end_turn",
        usage={"inputTokens": 100, "outputTokens": 20},
    )


class FakeModel:
    """Replays scripted turns and records what the reviewer sent."""

    def __init__(self, turns: list[Turn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def converse(self, system, messages, tools, tool_choice=None, max_tokens=4096) -> Turn:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": [t["toolSpec"]["name"] for t in tools],
                "tool_choice": tool_choice,
            }
        )
        return self.turns.pop(0)
