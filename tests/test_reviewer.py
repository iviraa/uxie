from dataclasses import replace

from tests.conftest import PATCH, FakeModel, text_turn, tool_turn
from uxie.diff.patch import parse_patch
from uxie.review.reviewer import PullRequest, review
from uxie.review.workspace import Workspace

FINDING = {
    "path": "a.py",
    "line": 4,
    "severity": "high",
    "category": "bug",
    "title": "returns last not first",
    "explanation": "xs[-1] changes behavior",
    "confidence": 0.8,
}


def _pr():
    return PullRequest("t", "b", [parse_patch("a.py", PATCH)])


def _ws(tmp_path):
    (tmp_path / "a.py").write_text("def f(xs):\n    return xs[-1]\n")
    return Workspace(tmp_path)


def test_baseline_is_single_forced_call(tmp_path, cfg):
    model = FakeModel([tool_turn("submit_review", {"summary": "s", "findings": [FINDING]})])
    res = review(_pr(), _ws(tmp_path), model, replace(cfg, mode="baseline"))
    assert len(model.calls) == 1
    assert model.calls[0]["tools"] == ["submit_review"]
    assert model.calls[0]["tool_choice"] == {"tool": {"name": "submit_review"}}
    assert [f.line for f in res.findings] == [4]
    assert res.usage.calls == 1 and res.cost_usd > 0


def test_context_runs_tools_then_submits(tmp_path, cfg):
    model = FakeModel(
        [
            tool_turn("read_file", {"path": "a.py"}, "u1"),
            tool_turn("search_code", {"pattern": "xs"}, "u2"),
            tool_turn("submit_review", {"summary": "s", "findings": [FINDING]}, "u3"),
        ]
    )
    res = review(_pr(), _ws(tmp_path), model, cfg)
    assert [c["name"] for c in res.tool_calls] == ["read_file", "search_code"]
    result_msg = model.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert result_msg["toolUseId"] == "u1" and "return xs[-1]" in result_msg["content"][0]["text"]
    assert model.calls[0]["tool_choice"] == {"auto": {}}
    assert len(res.findings) == 1 and res.error is None


def test_budget_forces_submit(tmp_path, cfg):
    turns = [tool_turn("list_dir", {}, f"u{i}") for i in range(3)]
    turns.append(tool_turn("submit_review", {"summary": "s", "findings": []}, "done"))
    model = FakeModel(turns)
    res = review(_pr(), _ws(tmp_path), model, cfg)  # max_tool_turns=3
    assert model.calls[3]["tool_choice"] == {"tool": {"name": "submit_review"}}
    assert res.findings == [] and res.error is None


def test_prose_answer_gets_nudged(tmp_path, cfg):
    model = FakeModel(
        [
            text_turn("looks fine to me"),
            tool_turn("submit_review", {"summary": "ok", "findings": []}),
        ]
    )
    res = review(_pr(), _ws(tmp_path), model, cfg)
    assert model.calls[1]["tool_choice"] == {"tool": {"name": "submit_review"}}
    assert res.summary == "ok"


def test_never_submits_is_an_error(tmp_path, cfg):
    model = FakeModel([text_turn("no")] * 10)
    res = review(_pr(), _ws(tmp_path), model, replace(cfg, max_tool_turns=1))
    assert res.error == "no submit_review"


def test_cache_point_moves_to_newest_message(tmp_path, cfg):
    model = FakeModel(
        [
            tool_turn("list_dir", {}, "u1"),
            tool_turn("submit_review", {"summary": "s", "findings": []}, "u2"),
        ]
    )
    review(_pr(), _ws(tmp_path), model, cfg)
    for call in model.calls:
        msgs = call["messages"]
        marks = [i for i, m in enumerate(msgs) for b in m["content"] if "cachePoint" in b]
        assert marks == [len(msgs) - 1]


def test_rate_limiter_spaces_calls(monkeypatch):
    from uxie.review import model as m

    clock = [100.0]
    slept: list[float] = []
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(m.time, "sleep", lambda s: slept.append(s))
    lim = m.RateLimiter(rpm=6)  # one call per 10s
    for _ in range(3):
        lim.wait()
    assert slept == [0.0, 10.0, 20.0]


class FocusModel:
    """Answers by specialist focus, found in the system prompt; thread-safe."""

    def __init__(self, by_focus):
        self.by_focus, self.systems = by_focus, []

    def converse(self, system, messages, tools, tool_choice=None, max_tokens=4096):
        text = system[0]["text"]
        self.systems.append(text)
        focus = next(k for k in self.by_focus if k in text)
        return tool_turn("submit_review", {"summary": focus, "findings": self.by_focus[focus]})


def test_specialists_run_in_parallel_and_dedupe(tmp_path, cfg):
    from dataclasses import replace

    weak = FINDING | {"confidence": 0.6, "title": "weak"}
    strong = FINDING | {"confidence": 0.95, "title": "strong"}
    other = FINDING | {"line": 22, "title": "edge"}
    model = FocusModel({"only on logic": [weak], "only on edge": [other, strong], "contracts:": []})
    res = review(_pr(), _ws(tmp_path), model, replace(cfg, mode="specialists"))
    assert len(model.systems) == 3
    assert sorted(f.title for f in res.findings) == ["edge", "strong"]  # dupe kept the stronger
    assert res.stats["raw_logic"] == 1 and res.stats["raw_edges"] == 2 and res.stats["dupe"] == 1
    assert res.usage.calls == 3 and res.error is None
