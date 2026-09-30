from uxie.sandbox.languages import PYTHON, for_path, verifiable


def test_lookup_by_extension():
    assert for_path("pkg/mod.py") is PYTHON
    assert for_path("README.md") is None and not verifiable("src/app.rs")


def test_python_syntax_check():
    assert PYTHON.syntax_error("def test_x():\n    assert True\n") is None
    assert "SyntaxError" in PYTHON.syntax_error("def test_x(:\n")
    assert "No test functions" in PYTHON.syntax_error("x = 1\n")


def test_go_and_typescript_adapters(tmp_path):
    from uxie.sandbox.languages import GO, TYPESCRIPT

    assert for_path("cmd/app/main.go") is GO
    assert for_path("web/src/app.tsx") is TYPESCRIPT and for_path("lib/x.mjs") is TYPESCRIPT
    assert GO.test_path("pkg/limits/limits.go", tmp_path) == "pkg/limits/uxie_verify_test.go"
    assert TYPESCRIPT.test_path("src/a.ts", tmp_path) == "src/uxie_verify.test.ts"

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "x.go").write_text("// doc\npackage limits\n")
    assert "package limits" in GO.hint("pkg/x.go", tmp_path)
    assert (
        GO.syntax_error('package limits\nimport "testing"\nfunc TestUxieA(t *testing.T) {}\n')
        is None
    )
    assert "TestUxie" in GO.syntax_error("package limits\nfunc TestA(t *testing.T) {}\n")
    assert "node:test" in TYPESCRIPT.syntax_error("test('x', () => {})")


def test_typescript_staging_links_node_modules(tmp_path):
    from uxie.sandbox.languages import TYPESCRIPT

    TYPESCRIPT.prepare(tmp_path, True)
    assert (tmp_path / "node_modules").is_symlink()
    assert str((tmp_path / "node_modules").readlink()) == "/env/mod/node_modules"
    other = tmp_path / "nodeps"
    other.mkdir()
    TYPESCRIPT.prepare(other, False)
    assert not (other / "node_modules").exists()
