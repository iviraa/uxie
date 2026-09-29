"""Tool-use conversation loop shared by the reviewer and the prover."""

from __future__ import annotations

from typing import Any

from uxie.review import tools
from uxie.review.model import ChatModel, Usage
from uxie.review.workspace import Workspace

CACHE_POINT = {"cachePoint": {"type": "default"}}


def _cached(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cache point on the newest message so each turn reuses the whole prior prefix."""
    last = messages[-1]
    return [*messages[:-1], {**last, "content": [*last["content"], CACHE_POINT]}]


class Conversation:
    """Runs the model with workspace tools until it calls `final_tool`.

    After a final call, `reply()` sends feedback as that call's result so `run()` can
    continue the same conversation (e.g. the prover fixing a test that couldn't run).
    """

    def __init__(
        self,
        model: ChatModel,
        system_text: str,
        first_text: str,
        specs: list[dict[str, Any]],
        final_tool: str,
        ws: Workspace,
        force_text: str,
        cache: bool = True,
    ) -> None:
        self.model, self.specs, self.final, self.ws = model, specs, final_tool, ws
        self.force_text, self.cache = force_text, cache
        self.system: list[dict[str, Any]] = [{"text": system_text}]
        if cache:
            self.system.append(CACHE_POINT)
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": first_text}]}]
        self.usage = Usage()
        self.calls: list[dict[str, Any]] = []
        self._pending: list[dict[str, Any]] = []

    def run(self, max_turns: int, force: bool = False) -> dict[str, Any] | None:
        for turn in range(max_turns + 2):  # +1 forced final, +1 nudge after a prose answer
            if turn >= max_turns:
                force = True
            choice = {"tool": {"name": self.final}} if force else {"auto": {}}
            msgs = _cached(self.messages) if self.cache else self.messages
            resp = self.model.converse(self.system, msgs, self.specs, choice)
            self.usage.add(resp.usage)
            self.messages.append({"role": "assistant", "content": resp.content})

            uses = resp.tool_uses()
            final = next((u for u in uses if u["name"] == self.final), None)
            if final is not None:
                self._pending = uses
                return dict(final.get("input") or {})
            if not uses:  # prose answer; insist on the tool
                self.messages.append({"role": "user", "content": [{"text": self.force_text}]})
                force = True
                continue
            results = [self._result(u, self._dispatch(u)) for u in uses]
            if turn + 1 >= max_turns:
                results.append({"text": self.force_text})
            self.messages.append({"role": "user", "content": results})
        return None

    def reply(self, text: str) -> None:
        """Answer the last final-tool call (and any sibling tool calls) so the loop can resume."""
        results = [
            self._result(u, text if u["name"] == self.final else self._dispatch(u))
            for u in self._pending
        ]
        self._pending = []
        self.messages.append({"role": "user", "content": results})

    def _dispatch(self, u: dict[str, Any]) -> str:
        out = tools.dispatch(self.ws, u["name"], u.get("input") or {})
        self.calls.append({"name": u["name"], "input": u.get("input"), "chars": len(out)})
        return out

    @staticmethod
    def _result(u: dict[str, Any], text: str) -> dict[str, Any]:
        return {"toolResult": {"toolUseId": u["toolUseId"], "content": [{"text": text}]}}
