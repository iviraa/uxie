"""Integration: the same scenarios against real Docker containers and real k3s Jobs.

Each backend skips when unavailable (no image / no cluster), so CI runs what it can.
"""

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from uxie.sandbox.executor import DockerExecutor, verify
from uxie.sandbox.spec import CONFIRMED, LIKELY, REFUTED, VerifyJob

JOBS = Path.home() / ".uxie" / "jobs"  # mounted into k3d nodes at /jobs


def _ok(*cmd: str) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


HAVE_DOCKER = _ok("docker", "image", "inspect", "uxie-sandbox:dev")
HAVE_K8S = HAVE_DOCKER and _ok("kubectl", "get", "namespace", "uxie-sandbox")


@pytest.fixture(
    params=[
        pytest.param("docker", marks=pytest.mark.skipif(not HAVE_DOCKER, reason="no docker")),
        pytest.param("k8s", marks=pytest.mark.skipif(not HAVE_K8S, reason="no k3d cluster")),
    ]
)
def ex(request):
    if request.param == "docker":
        return DockerExecutor()
    from uxie.sandbox.k8s import K8sExecutor

    return K8sExecutor(JOBS)


@pytest.fixture
def tmp_path():  # under the shared mount so k8s nodes can see staged checkouts
    d = JOBS / f"test-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


GOOD = "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n"
BUGGY = "def clamp(x, lo, hi):\n    return max(lo, min(x, lo))\n"  # regression introduced by "PR"


@pytest.fixture
def snaps(tmp_path):
    base, head = tmp_path / "base", tmp_path / "head"
    for d, src in ((base, GOOD), (head, BUGGY)):
        (d / "lib").mkdir(parents=True)
        (d / "lib" / "__init__.py").write_text("")
        (d / "lib" / "mathx.py").write_text(src)
    (head / "lib" / "fresh.py").write_text("def pages(n, size):\n    return n // size\n")
    return base, head


def _verify(ex, snaps, tmp_path, code, name="j"):
    job = VerifyJob(name, "o/r", "b", "h", "tests/test_uxie_verify.py", code, timeout_s=20)
    return verify(job, snaps[0], snaps[1], tmp_path / name, ex)


def test_regression_is_confirmed(ex, snaps, tmp_path):
    v = _verify(
        ex,
        snaps,
        tmp_path,
        "from lib.mathx import clamp\ndef test_t():\n    assert clamp(5, 0, 10) == 5\n",
    )
    assert v.verdict == CONFIRMED and v.base and v.base.outcome == "passed"


def test_new_code_bug_is_likely(ex, snaps, tmp_path):
    v = _verify(
        ex,
        snaps,
        tmp_path,
        "from lib.fresh import pages\ndef test_t():\n    assert pages(11, 10) == 2\n",
    )
    assert v.verdict == LIKELY and v.base and v.base.outcome == "error"


def test_false_claim_is_refuted(ex, snaps, tmp_path):
    v = _verify(
        ex,
        snaps,
        tmp_path,
        "from lib.mathx import clamp\ndef test_t():\n    assert clamp(-5, 0, 10) == 0\n",
    )
    assert v.verdict == REFUTED and v.base is None


@pytest.mark.parametrize(
    "code,must_mention",
    [
        ("def test_t():\n    open('/src/pwned', 'w').write('x')\n", "Read-only file system"),
        # an HTTP error would mean we reached the internet; only a connect failure passes
        (
            "import urllib.request\n"
            "def test_t():\n    urllib.request.urlopen('http://1.1.1.1', timeout=3)\n",
            "URLError: <urlopen error",
        ),
        ("import os\ndef test_t():\n    assert os.getuid() == 0\n", "assert 10001 == 0"),
    ],
    ids=["write-source", "network", "root"],
)
def test_escapes_are_blocked(ex, snaps, tmp_path, code, must_mention):
    v = _verify(ex, snaps, tmp_path, code)
    assert v.head.outcome == "failed", v.head.output_tail
    assert must_mention in v.head.failure, v.head.failure
    assert not (snaps[1] / "pwned").exists()


def test_infinite_loop_times_out(ex, snaps, tmp_path):
    job = VerifyJob(
        "t",
        "o/r",
        "b",
        "h",
        "tests/test_uxie_verify.py",
        "def test_t():\n    while True:\n        pass\n",
        timeout_s=3,
    )
    v = verify(job, snaps[0], snaps[1], tmp_path / "t", ex)
    assert v.head.outcome == "timeout"


def test_repo_pytest_config_is_ignored(ex, snaps, tmp_path):
    # a repo config with plugin-only flags would otherwise abort pytest before our test runs
    (snaps[1] / "setup.cfg").write_text("[tool:pytest]\naddopts = --cov=lib\n")
    v = _verify(
        ex,
        snaps,
        tmp_path,
        "from lib.mathx import clamp\ndef test_t():\n    assert clamp(-5, 0, 10) == 0\n",
    )
    assert v.head.outcome == "passed", v.head.output_tail
