from tests.conftest import PATCH
from uxie.diff import filters
from uxie.diff.patch import make_patch, parse_patch


def test_line_numbers_track_new_file():
    fp = parse_patch("a.py", PATCH)
    assert fp.added_lines() == {2, 3, 4, 22}
    assert fp.commentable() == {1, 2, 3, 4, 5, 21, 22, 23}
    assert (fp.added, fp.removed) == (4, 1)
    assert len(fp.hunk_starts) == 2


def test_render_numbers_added_and_context_but_not_removed():
    out = parse_patch("a.py", PATCH).render().splitlines()
    assert out[0] == "### a.py (modified)"
    assert "    2 +     if not xs:" in out
    assert "      -     return xs[0]" in out
    assert "   ..." in out


def test_make_patch_roundtrips_through_parser():
    old = "a\nb\nc\nd\ne\nf\ng\n"
    new = "a\nb\nc\nD\ne\nf\ng\nh\n"
    fp = parse_patch("x", make_patch(old, new))
    assert fp.added_lines() == {4, 8}
    assert fp.removed == 1


def test_no_newline_marker_ignored():
    fp = parse_patch("x", "@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b")
    assert fp.added_lines() == {1}


def test_filters_skip_lockfiles_and_removed():
    keep = parse_patch("src/a.py", PATCH)
    lock = parse_patch("uv.lock", PATCH)
    gone = parse_patch("b.py", PATCH, status="removed")
    assert filters.select([keep, lock, gone]) == [keep]
    assert filters.changed_lines([keep]) == 5
