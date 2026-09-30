"""Language adapters: everything the sandbox needs to verify findings in one language.

The runner inside every sandbox image prints the same JSON RunOutcome, so executors,
verdicts, the dispatcher, and Kubernetes stay language-neutral.
"""

from __future__ import annotations

import ast
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
)

LANGUAGES: tuple[Language, ...] = (PYTHON,)


def for_path(path: str) -> Language | None:
    suffix = PurePosixPath(path).suffix
    return next((lang for lang in LANGUAGES if suffix in lang.suffixes), None)


def verifiable(path: str) -> bool:
    return for_path(path) is not None
