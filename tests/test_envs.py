import shutil
import subprocess
import textwrap
import uuid
from pathlib import Path

import pytest

from uxie.sandbox.envs import EnvBuilder, env_key, requirements


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(textwrap.dedent(text))


def test_pep621_excludes_own_project_and_splits_groups(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [project]
        name = "My_Pkg"
        dependencies = ["httpx>=0.27", "my-pkg[extra]"]
        [project.optional-dependencies]
        cli = ["click"]
        [dependency-groups]
        dev = ["pytest", {include-group = "cli"}]
    """,
    )
    _write(tmp_path, "requirements.txt", "-e .[cli]\nmypy==1.0\ngit+https://x/y.git\n")
    core, groups = requirements(tmp_path)
    assert core == ["httpx>=0.27"]  # own package dropped even with extras / other spelling
    assert groups == {
        "extra:cli": ["click"],
        "file:requirements.txt": ["mypy==1.0"],
        "group:dev": ["pytest"],
    }


def test_requirements_is_core_without_pyproject_deps(tmp_path):
    _write(tmp_path, "requirements.txt", "boto3>=1.34  # aws\n\n-r extra.txt\n")
    _write(tmp_path, "extra.txt", "jinja2\n-r requirements.txt\n")  # cycle
    _write(tmp_path, "requirements-dev.txt", "-r requirements.txt\nruff\n")
    core, groups = requirements(tmp_path)
    assert core == ["boto3>=1.34", "jinja2"]
    assert groups == {"file:requirements-dev.txt": ["boto3>=1.34", "jinja2", "ruff"]}


def test_poetry_caret_and_optional(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [tool.poetry]
        name = "rich"
        [tool.poetry.dependencies]
        python = "^3.8"
        pygments = "^2.13.0"
        ipywidgets = { version = ">=7.5.1,<9", optional = true }
        local = { path = "../local" }
    """,
    )
    core, groups = requirements(tmp_path)
    assert core == ["pygments>=2.13.0"]
    assert groups == {"poetry:extras": ["ipywidgets>=7.5.1,<9"]}


def test_key_is_stable_and_sensitive():
    a = env_key(["x"], {"g": ["y"]}, None)
    assert a == env_key(["x"], {"g": ["y"]}, None)
    assert a != env_key(["x"], {"g": ["y"]}, "2024-01-01")
    assert a != env_key(["x", "z"], {"g": ["y"]}, None)


def test_no_deps_means_no_env(tmp_path):
    assert EnvBuilder(tmp_path / "envs").ensure(tmp_path) is None


def _docker_ok() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "image", "inspect", "uxie-sandbox:dev"], capture_output=True
            ).returncode
            == 0
        )
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _docker_ok(), reason="needs docker")
def test_build_and_import_in_sandbox():
    from uxie.sandbox.executor import DockerExecutor

    jobs = Path.home() / ".uxie" / "jobs"
    work = jobs / f"envtest-{uuid.uuid4().hex[:8]}"
    repo = work / "repo"
    (repo / "mypkg").mkdir(parents=True)
    (repo / "mypkg" / "__init__.py").write_text("import six\nVALUE = six.PY3\n")
    _write(repo, "pyproject.toml", '[project]\nname = "mypkg"\ndependencies = ["six"]\n')
    (repo / "test_it.py").write_text(
        "import importlib.util, mypkg\n"
        "def test_t():\n"
        "    assert mypkg.VALUE is True\n"
        "    assert mypkg.__file__.startswith('/src/')  # PR code, not an installed copy\n"
    )
    try:
        small = 2 * 1024**3  # a one-package env; CI runners have ~13 GiB free
        env = EnvBuilder(work / "envs", min_free_bytes=small).ensure(
            repo, exclude_newer="2025-01-01T00:00:00Z"
        )
        failed = list((work / "envs").glob("*.failed"))
        assert env is not None, failed[0].read_text()[-2000:] if failed else "no env, no log"
        assert (env / "site" / "six.py").exists()
        assert not list((env / "site").glob("mypkg*"))  # the project itself never installed
        cached = EnvBuilder(work / "envs", min_free_bytes=small).ensure(
            repo, "2025-01-01T00:00:00Z"
        )
        assert cached == env
        backends = [DockerExecutor()]
        if (
            subprocess.run(["kubectl", "get", "ns", "uxie-sandbox"], capture_output=True).returncode
            == 0
        ):
            from uxie.sandbox.k8s import K8sExecutor

            backends.append(K8sExecutor(jobs))
        for ex in backends:
            out = ex.run(repo, "test_it.py", 30, env)
            assert out.outcome == "passed", (type(ex).__name__, out.failure, out.output_tail)
            assert ex.run(repo, "test_it.py", 30).outcome == "error"  # without env: no six
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_heavy_ml_stack_is_skipped(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "w"\ndependencies = ["numpy", "torch>=2.4"]\n',
    )
    assert EnvBuilder(tmp_path / "envs").ensure(tmp_path) is None
    assert not (tmp_path / "envs").exists()  # nothing was built


def test_refuses_to_build_when_disk_is_low(tmp_path, monkeypatch):
    import shutil as sh

    from uxie.sandbox import envs

    _write(tmp_path, "pyproject.toml", '[project]\nname = "w"\ndependencies = ["six"]\n')
    monkeypatch.setattr(sh, "disk_usage", lambda p: sh._ntuple_diskusage(100, 99, 1))
    with pytest.raises(RuntimeError, match="refusing env build"):
        envs.EnvBuilder(tmp_path / "envs").ensure(tmp_path)


def test_setup_py_is_parsed_not_executed(tmp_path):
    _write(
        tmp_path,
        "setup.py",
        """
        import os
        os.system("touch PWNED")  # must never run
        EXTRAS = {"http2": "h2==3.*", "cli": ["click"]}
        REQS = ["certifi", "httpcore>=0.12,<0.13"]
        setup(
            name="httpx",
            install_requires=REQS + ["sniffio"] if False else REQS,
            extras_require=EXTRAS,
        )
    """,
    )
    _write(tmp_path, "setup2.py", "")
    core, groups = requirements(tmp_path)
    assert not (tmp_path / "PWNED").exists()
    assert groups == {"extra:cli": ["click"], "extra:http2": ["h2==3.*"]}
    # a non-literal expression is skipped rather than evaluated
    assert core == []


def test_setup_py_literal_and_name_refs(tmp_path):
    _write(
        tmp_path,
        "setup.py",
        """
        from setuptools import setup
        REQS = ["certifi", "rfc3986[idna2008]>=1.3,<2"]
        setuptools.setup(name="pkg", install_requires=REQS)
    """,
    )
    core, _ = requirements(tmp_path)
    assert core == ["certifi", "rfc3986[idna2008]>=1.3,<2"]


def test_poetry_python_constraint_becomes_marker(tmp_path):
    _write(
        tmp_path,
        "pyproject.toml",
        """
        [tool.poetry]
        name = "rich"
        [tool.poetry.dependencies]
        python = "^3.6.2"
        dataclasses = { version = ">=0.7,<0.9", python = "<3.7" }
        typing-extensions = { version = ">=3.7.4, <5.0", python = ">=3.6,<3.9" }
        colorama = { version = "^0.4", markers = "sys_platform == 'win32'" }
    """,
    )
    core, _ = requirements(tmp_path)
    assert 'dataclasses>=0.7,<0.9; python_version < "3.7"' in core
    assert (
        'typing-extensions>=3.7.4, <5.0; python_version >= "3.6" and python_version < "3.9"' in core
    )
    assert "colorama>=0.4; sys_platform == 'win32'" in core


@pytest.mark.skipif(not _docker_ok(), reason="needs docker")
def test_env_packages_never_shadow_the_stdlib():
    from uxie.sandbox.executor import DockerExecutor

    work = Path.home() / ".uxie" / "jobs" / f"shadow-{uuid.uuid4().hex[:8]}"
    env, repo = work / "env", work / "repo"
    (env / "site").mkdir(parents=True)
    repo.mkdir()
    # a broken backport with a stdlib name, like dataclasses 0.8 on modern Python
    (env / "site" / "dataclasses.py").write_text("raise ImportError('backport shadowed stdlib')\n")
    (env / "site" / "extra_pkg.py").write_text("VALUE = 42\n")
    (repo / "test_it.py").write_text(
        "import dataclasses, extra_pkg\n"
        "def test_t():\n"
        "    assert dataclasses.__file__.startswith('/usr/local/lib/python3.12')\n"
        "    assert extra_pkg.VALUE == 42\n"
    )
    try:
        out = DockerExecutor().run(repo, "test_it.py", 30, env)
        assert out.outcome == "passed", out.output_tail
    finally:
        shutil.rmtree(work, ignore_errors=True)
