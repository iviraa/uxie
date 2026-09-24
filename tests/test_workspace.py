import pytest

from uxie.review.workspace import Workspace


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("\n".join(f"line{i}" for i in range(1, 11)))
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("line1")
    return Workspace(tmp_path)


def test_read_file_numbers_and_ranges(ws):
    out = ws.read_file("pkg/a.py", 3, 4)
    assert "lines 3-4 of 10" in out and "    3  line3" in out and "line5" not in out
    assert "more lines" in out


def test_blocks_escape(ws):
    with pytest.raises(ValueError):
        ws.read_file("../../etc/passwd")


def test_search_skips_git_dir(ws):
    assert ws.search(r"line1\b") == "pkg/a.py:1: line1"
    assert ws.search("zzz") == "no matches"
    assert ws.search("(").startswith("error")


def test_list_dir(ws):
    assert ws.list_dir() == "pkg/"
