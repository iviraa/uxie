from tests.conftest import PATCH
from uxie.diff.patch import parse_patch
from uxie.review.findings import parse_findings, validate


def _raw(**kw):
    base = {
        "path": "a.py",
        "line": 3,
        "severity": "high",
        "category": "bug",
        "title": "t",
        "explanation": "e",
        "confidence": 0.9,
    }
    return base | kw


def test_validate_keeps_snaps_and_drops():
    files = [parse_patch("a.py", PATCH)]
    raw = [
        _raw(),  # kept
        _raw(line=8),  # 3 away from 5: snapped
        _raw(line=50),  # off diff: dropped
        _raw(path="nope.py"),  # unknown file
        _raw(line=4, confidence=0.2),  # low confidence
        _raw(line=3, title="dupe"),  # same anchor
        {"path": "a.py"},  # malformed
    ]
    kept, stats = validate(parse_findings(raw), files, 0.5, 10)
    assert [(f.line, f.snapped_from) for f in kept] == [(3, None), (5, 8)]
    assert stats == {"raw": 6, "bad_path": 1, "off_diff": 1, "low_conf": 1, "dupe": 1, "capped": 0}


def test_validate_orders_by_severity_and_caps():
    files = [parse_patch("a.py", PATCH)]
    raw = [
        _raw(line=2, severity="low"),
        _raw(line=3, severity="high"),
        _raw(line=4, severity="medium"),
    ]
    kept, stats = validate(parse_findings(raw), files, 0.5, 2)
    assert [f.severity for f in kept] == ["high", "medium"]
    assert stats["capped"] == 1


def test_parse_clamps_and_defaults_enums():
    (f,) = parse_findings([_raw(severity="critical", category="nit", confidence=4)])
    assert (f.severity, f.category, f.confidence) == ("medium", "bug", 1.0)


def test_fingerprint_survives_line_shift():
    from uxie.diff.patch import parse_patch as pp

    shifted = PATCH.replace("@@ -1,4 +1,5 @@", "@@ -11,4 +11,5 @@")
    a, _ = validate(parse_findings([_raw(line=3)]), [pp("a.py", PATCH)], 0.5, 10)
    b, _ = validate(parse_findings([_raw(line=13)]), [pp("a.py", shifted)], 0.5, 10)
    c, _ = validate(parse_findings([_raw(line=4)]), [pp("a.py", PATCH)], 0.5, 10)
    assert a[0].fingerprint == b[0].fingerprint != c[0].fingerprint
