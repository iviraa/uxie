from uxie.sandbox.languages import PYTHON, for_path, verifiable


def test_lookup_by_extension():
    assert for_path("pkg/mod.py") is PYTHON
    assert for_path("README.md") is None and not verifiable("src/app.rs")


def test_python_syntax_check():
    assert PYTHON.syntax_error("def test_x():\n    assert True\n") is None
    assert "SyntaxError" in PYTHON.syntax_error("def test_x(:\n")
    assert "No test functions" in PYTHON.syntax_error("x = 1\n")
