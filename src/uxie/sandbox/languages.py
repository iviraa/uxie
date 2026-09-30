"""Language adapters: everything the sandbox needs to verify findings in one language.

The runner inside every sandbox image prints the same JSON RunOutcome, so executors,
verdicts, the dispatcher, and Kubernetes stay language-neutral.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class Language:
    name: str
    suffixes: frozenset[str]
    image: str
    test_rules: str  # prover instructions: framework, file layout, imports
    test_path: Callable[[str, Path], str]  # (flagged file, repo root) -> generated test path
    syntax_error: Callable[[str], str | None]
    deps_env: Callable[[str], dict[str, str]]  # mounted deps dir -> env vars for the runner
    tmp_size: str = "64Mi"  # writable scratch; compilers need more than an interpreter
    tmp_in_memory: bool = True
    memory: str = "512Mi"
    pids: int = 128
    prepare: Callable[[Path, bool], None] = lambda checkout, has_deps: None  # staging hook
    hint: Callable[[str, Path], str] = lambda path, root: ""  # extra context for the prover
    property_hint: str = ""  # how to write a deterministic property-based test


def _python_syntax(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e.msg} (line {e.lineno})"
    has_test = any(
        isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("test")
        for n in ast.walk(tree)
    )
    return None if has_test else "No test functions found (names must start with test)."


PYTHON = Language(
    name="python",
    suffixes=frozenset({".py"}),
    image="uxie-sandbox:dev",
    test_rules=(
        "Write a single pytest file. Import the code under test from the repository root "
        "(e.g. `from pkg.module import f`). Use only the standard library and pytest."
    ),
    test_path=lambda path, root: "test_uxie_verify.py",  # root, so `python -m pytest` imports
    syntax_error=_python_syntax,
    deps_env=lambda mount: {"UXIE_DEPS": f"{mount}/site"},  # loaded after stdlib, see sandbox/
    property_hint=(
        "Hypothesis is installed (`from hypothesis import given, strategies as st`); it runs "
        "deterministically with 200 examples, so don't change its settings."
    ),
)

_GO_PACKAGE = re.compile(r"^package\s+(\w+)", re.M)


def _go_package(path: str, root: Path) -> str:
    try:
        m = _GO_PACKAGE.search((root / path).read_text(errors="replace"))
    except OSError:
        return ""
    return m.group(1) if m else ""


def _go_hint(path: str, root: Path) -> str:
    pkg = _go_package(path, root)
    return (
        f"The flagged file is in Go package `{pkg}`; start the test file with `package {pkg}`."
        if pkg
        else ""
    )


def _go_syntax(code: str) -> str | None:
    if not _GO_PACKAGE.search(code):
        return "Missing a package clause."
    if not re.search(r"func TestUxie\w*\(t \*testing\.T\)", code):
        return "No test functions found (names must start with TestUxie and take *testing.T)."
    return None


GO = Language(
    name="go",
    suffixes=frozenset({".go"}),
    image="uxie-sandbox-go:dev",
    test_rules=(
        "Write a Go test file using only the standard library `testing` package. It goes in the "
        "same directory and package as the flagged file, so it can call unexported functions. "
        "Name every test function TestUxie<Something>(t *testing.T); only those run."
    ),
    test_path=lambda path, root: str(PurePosixPath(path).parent / "uxie_verify_test.go"),
    syntax_error=_go_syntax,
    deps_env=lambda mount: {"GOMODCACHE": f"{mount}/gomod"},
    tmp_size="2Gi",  # build cache
    tmp_in_memory=False,
    memory="2Gi",
    pids=512,
    hint=_go_hint,
    property_hint=(
        "Use the standard library's testing/quick with a fixed seed: "
        "`quick.Check(f, &quick.Config{MaxCount: 200, Rand: rand.New(rand.NewSource(1))})`."
    ),
)


def _ts_syntax(code: str) -> str | None:
    if "node:test" not in code:
        return 'Import the runner with `import { test } from "node:test";`.'
    if not re.search(r"\btest\s*\(", code):
        return "No test(...) calls found."
    return None


def _link_node_modules(checkout: Path, has_deps: bool) -> None:
    """Point the checkout's node_modules at the read-only deps mounted at /env."""
    link = checkout / "node_modules"
    if has_deps and not link.exists() and not link.is_symlink():
        link.symlink_to("/env/mod/node_modules")  # resolved inside the container


_JSX = (".tsx", ".jsx")


def _ts_test_path(path: str, root: Path) -> str:
    ext = ".tsx" if PurePosixPath(path).suffix in _JSX else ".ts"
    return str(PurePosixPath(path).parent / f"uxie_verify.test{ext}")


def _ts_hint(path: str, root: Path) -> str:
    if PurePosixPath(path).suffix not in _JSX:
        return ""
    return (
        "This is a React component. A DOM is already set up for the test. "
        '`import React from "react"` and use `@testing-library/react` (render, screen, '
        "fireEvent, act) to render the component, interact with it, and assert what the user "
        "would see. Call `cleanup()` after each test."
    )


TYPESCRIPT = Language(
    name="typescript",
    suffixes=frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}),
    image="uxie-sandbox-ts:dev",
    test_rules=(
        "Write a TypeScript test file using Node's built-in runner: "
        '`import { test } from "node:test"` and `import assert from "node:assert/strict"`. '
        "It sits next to the flagged file, so import the code under test with a relative path "
        '(e.g. `import { f } from "./module"`). Don\'t use jest, vitest, or mocha.'
    ),
    test_path=_ts_test_path,
    syntax_error=_ts_syntax,
    deps_env=lambda mount: {"NODE_PATH": f"{mount}/mod/node_modules"},  # CommonJS fallback
    tmp_size="1Gi",
    tmp_in_memory=False,
    memory="1Gi",
    pids=256,
    prepare=_link_node_modules,
    hint=_ts_hint,
    property_hint=(
        'fast-check is available (`import fc from "fast-check"`); always pass '
        "`{ seed: 42, numRuns: 200 }` to fc.assert so runs are deterministic."
    ),
)

LANGUAGES: tuple[Language, ...] = (PYTHON, GO, TYPESCRIPT)


def for_path(path: str) -> Language | None:
    suffix = PurePosixPath(path).suffix
    return next((lang for lang in LANGUAGES if suffix in lang.suffixes), None)


def verifiable(path: str) -> bool:
    return for_path(path) is not None
