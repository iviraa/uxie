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
    model = FocusModel(
        {"only on logic": [], "only on edge": [], "contracts:": [], "behavior over time": []}
    )
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
    assert len(llm) == 4 and all(s.parent and s.parent.span_id == root.context.span_id for s in llm)


def test_carrier_roundtrip_links_parent_and_child():
    from uxie.common import telemetry

    _exporter.clear()
    with telemetry.span("webhook") as root:
        headers = telemetry.carrier()
    assert headers["traceparent"].startswith("00-")
    with telemetry.continued(headers), telemetry.span("sqs.receive"):
        pass
    child = next(s for s in _exporter.get_finished_spans() if s.name == "sqs.receive")
    assert child.context.trace_id == root.get_span_context().trace_id
    assert child.parent.span_id == root.get_span_context().span_id


def test_verify_jobs_carry_the_review_trace(tmp_path, cfg):
    from tests.test_pipeline import _job, _model

    _exporter.clear()
    queued = []
    pipeline.run(_job(), FakeGH(), _model(), cfg, FakeStore(), None, tmp_path / "w", queued.append)
    (review,) = [s for s in _exporter.get_finished_spans() if s.name == "review"]
    (req,) = queued
    assert req.trace["traceparent"].split("-")[1] == format(review.context.trace_id, "032x")


def test_dispatcher_continues_the_trace():
    import threading

    from tests.test_dispatcher_loop import REQ, FakeSQS, _msg
    from uxie.common import telemetry
    from uxie.verify.main import serve

    _exporter.clear()
    with telemetry.span("review") as review:
        req = REQ.__class__(**{**REQ.__dict__, "trace": telemetry.carrier()})
    stop = threading.Event()
    sqs = FakeSQS([[_msg(1, body=req.to_json()) | {"Attributes": {"SentTimestamp": "0"}}]], stop)
    serve(sqs, "q", lambda r: "confirmed", stop)
    recv = next(s for s in _exporter.get_finished_spans() if s.name == "sqs.receive")
    assert recv.context.trace_id == review.get_span_context().trace_id
    assert recv.attributes["queue"] == "verify" and recv.attributes["queue.wait_ms"] > 0
