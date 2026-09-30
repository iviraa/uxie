import json

from bench import score as sc
from bench.cases import Case
from bench.mutate import Mutation


def _case(cid, kind, line=10):
    m = Mutation("a.py", line, "compare", "x < 1", "x <= 1") if kind == "seeded" else None
    return Case(cid, "/r", "b", "h", "t", "", kind, m, "src" if m else None)


def _f(line, path="a.py"):
    return {"path": path, "line": line}


def test_score_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "RESULTS", tmp_path)
    cases = {
        c.id: c
        for c in [
            _case("s1", "seeded"),
            _case("s2", "seeded"),
            _case("c1", "clean"),
            _case("c2", "clean"),
            _case("s3", "seeded"),  # only ever errored
        ]
    }
    rows = [
        {"case_id": "s1", "findings": [_f(12), _f(40)]},  # hit within tolerance + one miss
        {"case_id": "s2", "findings": [_f(10, "b.py")]},  # wrong file
        {"case_id": "c1", "findings": [_f(3)]},
        {"case_id": "c2", "findings": []},
        {"case_id": "s3-unknown", "findings": []},
    ]
    for r in rows:
        r |= {"cost_usd": 0.01, "latency_s": 2.0, "tool_calls": [{}]}
    rows.append({"case_id": "c2", "error": "boom"})  # superseded by c2's success
    rows.append({"case_id": "s3", "error": "boom"})
    (tmp_path / "v.jsonl").write_text("\n".join(json.dumps(r) for r in rows))

    s = sc.score("v", cases)
    assert (s.seeded, s.caught, s.recall) == (2, 1, 0.5)
    assert (s.findings_seeded, s.on_target) == (3, 1)
    assert (s.clean, s.findings_clean, s.clean_flagged) == (2, 1, 1)
    assert s.errors == 1
    assert sc.by_op("v", cases) == {"compare": (1, 2)}


def test_tolerance_boundary():
    c = _case("s", "seeded", line=10)
    assert sc.hits(c, [_f(12)]) and sc.hits(c, [_f(8)])
    assert not sc.hits(c, [_f(13)])


def test_latest_prefers_success_over_later_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "RESULTS", tmp_path)
    ok = {"case_id": "a", "findings": []}
    rows = [
        {"case_id": "a", "error": "throttled"},
        ok,
        {"case_id": "a", "error": "again"},
        {"case_id": "b", "error": "x"},
    ]
    (tmp_path / "v.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    got = {r["case_id"]: r for r in sc.latest("v")}
    assert got["a"] == ok and got["b"]["error"] == "x"


def test_real_bug_case_uses_truth_lines():
    c = Case(
        "r1",
        "/r",
        "b",
        "h",
        "t",
        "",
        "real",
        truth=[{"path": "a.py", "line": 10}, {"path": "b.py", "line": 50}],
    )
    assert sc.hits(c, [_f(51, "b.py")]) and not sc.hits(c, [_f(10, "b.py")])
    assert c.targets == [("a.py", 10), ("b.py", 50)]
