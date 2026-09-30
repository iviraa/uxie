import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.conftest import FakeModel, tool_turn
from tests.test_pipeline import FINDING, FakeGH, FakeStore
from uxie import pipeline
from uxie.github.webhook import ReviewJob

_exporter = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def provider():
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(_exporter))
    trace.set_tracer_provider(tp)  # the module-level tracer is a proxy, so this takes effect


def test_review_trace_shape(tmp_path, cfg):
    _exporter.clear()
    model = FakeModel(
        [
            tool_turn("read_file", {"path": "a.py"}, "u1"),
            tool_turn("submit_review", {"summary": "s", "findings": [FINDING]}, "u2"),
        ]
    )
    status = pipeline.run(
        ReviewJob("o/r", 3, 7, "abc", "opened"),
        FakeGH(),
        model,
        cfg,
        FakeStore(),
        None,
        tmp_path / "w",
    )
    spans = {s.name: s for s in _exporter.get_finished_spans()}
    assert status == "done"
    assert {"review", "llm.converse", "tool.read_file"} <= set(spans)
    root = spans["review"]
    assert root.attributes["pr.repo"] == "o/r" and root.attributes["review.status"] == "done"
    llm = spans["llm.converse"]
    assert llm.parent.span_id == root.context.span_id  # model calls nest under the review
    assert llm.attributes["llm.input_tokens"] == 100


def test_failed_review_marks_span_error(tmp_path, cfg):
    _exporter.clear()
    with pytest.raises(IndexError):
        pipeline.run(
            ReviewJob("o/r", 3, 7, "abc", "opened"),
            FakeGH(),
            FakeModel([]),
            cfg,
            FakeStore(),
            None,
            tmp_path / "w",
        )
    (root,) = [s for s in _exporter.get_finished_spans() if s.name == "review"]
    assert root.status.status_code == trace.StatusCode.ERROR


def test_specialist_calls_nest_under_the_review(tmp_path, cfg):
    from dataclasses import replace

    from tests.test_reviewer import FocusModel

    _exporter.clear()
    model = FocusModel({"only on logic": [], "only on edge": [], "contracts:": []})
    pipeline.run(
        ReviewJob("o/r", 3, 7, "abc", "opened"),
        FakeGH(),
        model,
        replace(cfg, mode="specialists"),
        FakeStore(),
        None,
        tmp_path / "w",
    )
    spans = _exporter.get_finished_spans()
    (root,) = [s for s in spans if s.name == "review"]
    llm = [s for s in spans if s.name == "llm.converse"]
    assert len(llm) == 3 and all(s.parent and s.parent.span_id == root.context.span_id for s in llm)
